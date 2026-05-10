from __future__ import annotations

import json
import shutil
import subprocess
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
import typer
from rich.console import Console

from ppt_agent.domain.evidence import EvidencePack
from ppt_agent.domain.models import AgentMode, AgentState, DeckIntent, PptSpec
from ppt_agent.agent.skill_loader import load_user_skill, load_user_skills, project_skill_dir
from ppt_agent.graph.agent import create_agent_graph
from ppt_agent.ingest import EvidenceBuilder, MarkdownParser, MinerUAdapter
from ppt_agent.ingest.mineru_adapter import MinerUOptions
from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model, validate_provider
from ppt_agent.runtime.document_qa import DocumentQaReport, run_document_qa, write_document_qa_report
from ppt_agent.runtime.document_repair import repair_plan_spec
from ppt_agent.runtime.planner import test_planner_connection
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.storage.llm_settings import key_statuses, load_selection, save_api_key, save_selection
from ppt_agent.storage.project_memory import retrieve_failure_patterns, retrieve_project_memory
from ppt_agent.storage.plan_io import (
    MigratePlanResult,
    PLAN_SCHEMA_VERSION,
    PlanDocument,
    ValidateReport,
    migrate_plan_document,
    read_plan_document,
    validate_plan_document,
    write_plan_document,
)

app = typer.Typer(help="PPT Agent CLI")
llm_app = typer.Typer(help="LLM provider configuration")
skill_app = typer.Typer(help="User skill management")
console = Console()
err_console = Console(stderr=True)
app.add_typer(llm_app, name="llm")
app.add_typer(skill_app, name="skill")


class IngestParser(str, Enum):
    AUTO = "auto"
    MARKDOWN = "markdown"
    MINERU = "mineru"


class MinerUBackend(str, Enum):
    PIPELINE = "pipeline"
    VLM_HTTP_CLIENT = "vlm-http-client"
    HYBRID_HTTP_CLIENT = "hybrid-http-client"
    VLM_AUTO_ENGINE = "vlm-auto-engine"
    HYBRID_AUTO_ENGINE = "hybrid-auto-engine"


class MinerUMethod(str, Enum):
    AUTO = "auto"
    TXT = "txt"
    OCR = "ocr"


@app.command()
def plan(
    topic: Annotated[str | None, typer.Argument(help="Presentation topic or goal. Optional when using --evidence.")] = None,
    spec: Annotated[Path, typer.Option("--spec", "-s", help="Where to write the structured spec.")] = Path("deck_spec.json"),
    evidence: Annotated[Path | None, typer.Option("--evidence", help="EvidencePack JSON to ground the generated plan.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="LLM provider override for planning.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="LLM model override for planning.")] = None,
) -> None:
    """Create a structured deck spec without building a PPTX."""
    _validate_llm_override(provider=provider, model=model)
    evidence_pack: EvidencePack | None = None
    if evidence is not None:
        try:
            evidence_pack = _read_evidence_pack(evidence)
        except ValueError as exc:
            err_console.print(f"[bold red]plan[/bold red]: {exc}")
            raise typer.Exit(code=1) from exc

    effective_topic = topic or _topic_from_evidence(evidence_pack)
    if not effective_topic:
        err_console.print("[bold red]plan[/bold red]: topic is required unless --evidence is provided")
        raise typer.Exit(code=2)

    graph = create_agent_graph()
    if evidence_pack is None:
        memory = retrieve_project_memory(Path.cwd(), query=effective_topic)
        failures = retrieve_failure_patterns(Path.cwd(), query=effective_topic)
    else:
        memory = {"preferences": []}
        failures = {"failure_patterns": []}
    evidence_digest = _evidence_digest(evidence_pack, evidence_path=evidence) if evidence_pack is not None else None
    intent = DeckIntent(
        topic=effective_topic,
        project_preferences=memory.get("preferences", []),
        failure_patterns=failures.get("failure_patterns", []),
        source_digest=evidence_digest,
        source_context=(evidence_digest or {}).get("evidence_items", []) if evidence_digest else [],
    )
    state = AgentState(intent=intent, mode=AgentMode.PLAN, planner_provider=provider, planner_model=model)
    result = _invoke_graph_or_exit(graph, state)
    ppt_spec = PptSpec.model_validate(result["spec"])
    write_plan_document(
        spec,
        intent=intent,
        spec=ppt_spec,
        mode=result.get("mode", AgentMode.PLAN),
        approved=result.get("approved", False),
        transitions=result.get("transitions", []),
        metadata=_project_memory_metadata(memory=memory, failures=failures),
    )
    console.print(f"Wrote spec to [bold]{spec}[/bold]")


@app.command()
def build(
    spec: Annotated[
        Path,
        typer.Argument(
            help=f"Path to a unified plan/spec JSON file. The canonical schema includes schema_version={PLAN_SCHEMA_VERSION}."
        ),
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output PPTX path.")] = Path("deck.pptx"),
    evidence: Annotated[Path | None, typer.Option("--evidence", help="EvidencePack JSON used to resolve figure_ids.")] = None,
) -> None:
    """Build a PPTX from a unified plan/spec JSON file with schema_version."""
    document = _load_executable_plan(spec, command_name="build")
    ppt_spec = document.spec
    evidence_pack = None
    if evidence is not None:
        try:
            evidence_pack = _read_evidence_pack(evidence)
        except ValueError as exc:
            err_console.print(f"[bold red]build[/bold red]: {exc}")
            raise typer.Exit(code=1) from exc
    result = build_pptx(ppt_spec, out, evidence_pack=evidence_pack, evidence_path=evidence)
    console.print(f"Wrote PPTX to [bold]{result.path}[/bold]")


@app.command()
def ingest(
    input_path: Annotated[Path, typer.Argument(help="Markdown file or PDF to convert into evidence.json.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write evidence.json.")],
    parser: Annotated[IngestParser, typer.Option("--parser", help="Parser to use.")] = IngestParser.AUTO,
    workdir: Annotated[Path, typer.Option("--workdir", help="MinerU working/output directory.")] = Path(".ppt-agent/ingest"),
    mineru_backend: Annotated[
        MinerUBackend | None,
        typer.Option("--mineru-backend", help="MinerU backend to use for PDF parsing."),
    ] = MinerUBackend.PIPELINE,
    mineru_method: Annotated[
        MinerUMethod | None,
        typer.Option("--mineru-method", help="MinerU PDF parsing method."),
    ] = MinerUMethod.AUTO,
    mineru_lang: Annotated[str | None, typer.Option("--mineru-lang", help="Input language hint for MinerU OCR.")] = None,
    mineru_start: Annotated[int | None, typer.Option("--mineru-start", help="Starting page for MinerU, zero-based.")] = None,
    mineru_end: Annotated[int | None, typer.Option("--mineru-end", help="Ending page for MinerU, zero-based.")] = None,
) -> None:
    """Convert Markdown or MinerU output into an EvidencePack JSON file."""
    try:
        options = MinerUOptions(
            backend=mineru_backend.value if mineru_backend is not None else None,
            method=mineru_method.value if mineru_method is not None else None,
            lang=mineru_lang,
            start=mineru_start,
            end=mineru_end,
        )
        pack = _build_ingest_evidence(input_path=input_path, parser=parser, workdir=workdir, mineru_options=options)
    except (RuntimeError, ValueError, OSError) as exc:
        err_console.print(f"[bold red]ingest[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(pack.to_json(), encoding="utf-8")
    typer.echo(f"Sections: {len(pack.sections)}")
    typer.echo(f"Figures: {len(pack.figures)}")
    typer.echo(f"Tables: {len(pack.tables)}")
    typer.echo(f"Output: {out}")


@app.command()
def doctor() -> None:
    """Check optional local runtime dependencies."""
    status = _mineru_status()
    console.print("MinerU:")
    console.print(f"  command: {'found' if status['command_found'] else 'missing'}")
    if status["path"]:
        console.print(f"  path: {status['path']}")
    if status["package_version"]:
        console.print(f"  package: {status['package_version']}")
    if status["cli_version"]:
        console.print(f"  cli: {status['cli_version']}")
    if status["error"]:
        console.print(f"  error: {status['error']}")


@app.command()
def validate(
    plan: Annotated[
        Path,
        typer.Argument(help=f"Path to a plan/spec JSON file. The canonical schema includes schema_version={PLAN_SCHEMA_VERSION}."),
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Emit a stable JSON validation report.")] = False,
) -> None:
    """Validate a plan/spec JSON file and report schema compatibility."""
    report = validate_plan_document(plan)

    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print_validate_report(report)

    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def qa(
    plan: Annotated[Path, typer.Argument(help="Path to a plan/spec JSON file.")],
    evidence: Annotated[Path | None, typer.Option("--evidence", help="Optional EvidencePack JSON for citation and asset checks.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write qa_report.json.")] = Path("qa_report.json"),
) -> None:
    """Run deterministic Document-to-Deck QA checks."""
    try:
        document = read_plan_document(plan)
        evidence_pack = _read_evidence_pack(evidence) if evidence is not None else None
    except ValueError as exc:
        err_console.print(f"[bold red]qa[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc

    report = run_document_qa(document.spec, evidence_pack=evidence_pack, evidence_path=evidence)
    write_document_qa_report(report, out)
    console.print(f"QA Issues: {len(report.issues)}")
    console.print(f"Output: {out}")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def repair(
    plan: Annotated[Path, typer.Argument(help="Path to the plan/spec JSON file to repair.")],
    qa: Annotated[Path, typer.Option("--qa", help="qa_report.json produced by `ppt-agent qa`.")],
    evidence: Annotated[Path | None, typer.Option("--evidence", help="Optional EvidencePack JSON for deterministic repair.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the repaired plan JSON.")] = Path("repaired_plan.json"),
) -> None:
    """Repair a plan/spec JSON using deterministic Document-to-Deck QA results."""
    try:
        document = read_plan_document(plan)
        qa_report = DocumentQaReport.model_validate(_read_json_file(qa, label="qa report"))
        evidence_pack = _read_evidence_pack(evidence) if evidence is not None else None
    except ValueError as exc:
        err_console.print(f"[bold red]repair[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc

    repaired = repair_plan_spec(document.spec, qa_report=qa_report, evidence_pack=evidence_pack)
    write_plan_document(
        out,
        intent=_intent_from_plan_payload(document.payload, repaired),
        spec=repaired,
        mode=document.payload.get("mode", "plan"),
        approved=document.payload.get("approved", False),
        transitions=document.payload.get("transitions", []),
        theme=document.payload.get("theme"),
    )
    issue_count = len(qa_report.issues)
    console.print(f"Read QA Issues: {issue_count}")
    console.print(f"Output: {out}")


@app.command("migrate-plan")
def migrate_plan(
    input_path: Annotated[
        Path,
        typer.Argument(help=f"Path to an input plan/spec JSON file. Legacy and formal schema={PLAN_SCHEMA_VERSION} are supported."),
    ],
    out: Annotated[Path, typer.Option("--out", help="Where to write the migrated formal-schema JSON.")],
) -> None:
    """Normalize a plan/spec JSON file to the current formal schema."""
    try:
        result = migrate_plan_document(input_path, out)
    except ValueError as exc:
        err_console.print(f"[bold red]migrate-plan[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Input: {result.input_path}")
    console.print(f"Output: {result.output_path}")
    console.print(f"Source Type: {result.source_type}")
    console.print(f"Target Schema Version: {result.target_schema_version}")
    if result.already_current:
        console.print("Already current schema, normalized output written")


@app.command()
def run(
    topic: Annotated[str | None, typer.Argument(help="Presentation topic or goal. Optional when using --from-plan.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Output PPTX path.")] = Path("deck.pptx"),
    plan_out: Annotated[
        Path | None,
        typer.Option("--plan-out", help="Write the generated plan/spec JSON before approval."),
    ] = None,
    from_plan: Annotated[
        Path | None,
        typer.Option("--from-plan", help="Load an existing plan/spec JSON and continue from approval."),
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="LLM provider override for planning.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="LLM model override for planning.")] = None,
    mode: Annotated[AgentMode, typer.Option("--mode", "-m", help="Execution mode.")] = AgentMode.EXECUTE,
    auto_approve: Annotated[
        bool,
        typer.Option("--auto-approve", help="Skip the interactive approval gate and build immediately after planning."),
    ] = False,
) -> None:
    """Run the agent loop."""
    _validate_llm_override(provider=provider, model=model)
    if from_plan is not None:
        document = _load_executable_plan(from_plan, command_name="from-plan")
        loaded_spec = document.spec
        effective_topic = document.payload.get("request", {}).get("topic", loaded_spec.title)
        if topic:
            console.print("[bold]plan[/bold]: using plan from file; ignoring provided topic")

        state = AgentState(
            intent=DeckIntent(topic=effective_topic, audience=loaded_spec.audience, output_path=str(out)),
            mode=mode,
            planner_provider=provider,
            planner_model=model,
            approved=auto_approve,
        )
        result = state.model_dump(mode="json")
        result["spec"] = loaded_spec.model_dump(mode="json")
        graph = create_agent_graph(entry_point="asset_plan")
        console.print(f"[bold]plan[/bold]: loaded review file from [bold]{from_plan}[/bold]")
        console.print("[bold]asset[/bold]: refreshing visual planning before approval")
        console.print("[bold]approve[/bold]: review loaded plan before build")
    else:
        if not topic:
            err_console.print("[bold red]run[/bold red]: topic is required unless --from-plan is provided")
            raise typer.Exit(code=2)

        memory = retrieve_project_memory(Path.cwd(), query=topic)
        failures = retrieve_failure_patterns(Path.cwd(), query=topic)
        state = AgentState(
            intent=DeckIntent(
                topic=topic,
                output_path=str(out),
                project_preferences=memory.get("preferences", []),
                failure_patterns=failures.get("failure_patterns", []),
            ),
            mode=mode,
            planner_provider=provider,
            planner_model=model,
            approved=auto_approve,
        )
        result = state.model_dump(mode="json")
        graph = create_agent_graph()
        console.print("[bold]plan[/bold]: generating structured deck spec")

    for chunk in _stream_graph_or_exit(graph, result):
        for node_name, update in chunk.items():
            result.update(update)
            if node_name == "plan":
                if plan_out is not None:
                    _write_plan(plan_out, result)
                    console.print(f"[bold]plan[/bold]: wrote review file to [bold]{plan_out}[/bold]")
                    if mode != AgentMode.PLAN:
                        console.print("[bold]approve[/bold]: review or edit the plan file before approving")
            elif node_name == "asset_plan":
                console.print("[bold]asset[/bold]: planning visual requirements")
            elif node_name == "asset_resolve":
                for warning in result.get("asset_warnings", []):
                    console.print(f"Warning: {warning}")
                if plan_out is not None:
                    _write_plan(plan_out, result)
                    console.print(f"[bold]plan[/bold]: wrote review file to [bold]{plan_out}[/bold]")
                if mode == AgentMode.PLAN:
                    continue
                console.print("[bold]approve[/bold]: review required before build")
            elif node_name == "approve":
                if result.get("approved"):
                    console.print("[bold]build[/bold]: approval received, writing PPTX")
                else:
                    console.print("[bold]approve[/bold]: rejected, build skipped")
            elif node_name == "build":
                console.print("[bold]qa[/bold]: checking generated deck")
            elif node_name == "qa":
                console.print("[bold]qa[/bold]: complete")

    if result.get("spec") and mode == AgentMode.PLAN:
        console.print(json.dumps(result["spec"], ensure_ascii=False, indent=2))
        return

    if result.get("artifact"):
        console.print(f"Wrote PPTX to [bold]{result['artifact']['path']}[/bold]")
    else:
        console.print("No artifact was produced.")


def _write_run_plan(path: Path, state: dict) -> None:
    intent = DeckIntent.model_validate(state["intent"])
    spec = PptSpec.model_validate(state["spec"])
    write_plan_document(
        path,
        intent=intent,
        spec=spec,
        mode=state.get("mode", AgentMode.EXECUTE),
        approved=state.get("approved", False),
        transitions=state.get("transitions", []),
    )


def _write_plan(path: Path, state: dict) -> None:
    _write_run_plan(path, state)


def _read_json_file(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"failed to read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label} {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid {label} {path}: expected JSON object")
    return payload


def _intent_from_plan_payload(payload: dict, spec: PptSpec) -> DeckIntent:
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    return DeckIntent(
        topic=request.get("topic", spec.title),
        audience=request.get("audience", spec.audience),
        tone=request.get("tone", "clear and pragmatic"),
        output_path=request.get("output_path", "deck.pptx"),
        source_digest=spec.source_digest,
        applied_skills=spec.applied_skills,
        output_format=spec.output_format,
    )


def _read_evidence_pack(path: Path) -> EvidencePack:
    try:
        return EvidencePack.from_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"failed to read evidence file {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"invalid evidence file {path}: {exc}") from exc


def _topic_from_evidence(pack: EvidencePack | None) -> str | None:
    if pack is None:
        return None
    for source in pack.source_files:
        if source.title:
            return source.title
        if source.source_file:
            return Path(source.source_file).stem
    return None


def _evidence_digest(pack: EvidencePack, *, evidence_path: Path | None) -> dict:
    return {
        "type": "evidence_pack",
        "path": str(evidence_path) if evidence_path else None,
        "sources": [
            {
                "source_id": source.id,
                "name": source.source_file,
                "title": source.title or Path(source.source_file).stem,
                "path": source.path,
            }
            for source in pack.source_files
        ],
        "evidence_items": [
            *_section_summaries(pack),
            *_figure_summaries(pack),
            *_table_summaries(pack),
            *_claim_summaries(pack),
        ],
    }


def _section_summaries(pack: EvidencePack) -> list[dict]:
    return [
        {
            "type": "section",
            "evidence_id": section.id,
            "source_file": section.source_file,
            "page": section.page,
            "heading": section.heading,
            "text": _truncate(section.text),
        }
        for section in pack.sections[:24]
    ]


def _figure_summaries(pack: EvidencePack) -> list[dict]:
    return [
        {
            "type": "figure",
            "evidence_id": figure.id,
            "source_file": figure.source_file,
            "page": figure.page,
            "caption": figure.caption,
            "text": _truncate(figure.text or ""),
            "path": figure.path,
        }
        for figure in pack.figures[:12]
    ]


def _table_summaries(pack: EvidencePack) -> list[dict]:
    return [
        {
            "type": "table",
            "evidence_id": table.id,
            "source_file": table.source_file,
            "page": table.page,
            "caption": table.caption,
            "text": _truncate(table.text or ""),
        }
        for table in pack.tables[:12]
    ]


def _claim_summaries(pack: EvidencePack) -> list[dict]:
    return [
        {
            "type": "claim",
            "evidence_id": claim.id,
            "source_file": claim.source_file,
            "page": claim.page,
            "text": _truncate(claim.text),
            "supporting_evidence_ids": claim.supporting_evidence_ids,
            "confidence": claim.confidence,
        }
        for claim in pack.claims[:24]
    ]


def _truncate(value: str, *, limit: int = 360) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _build_ingest_evidence(*, input_path: Path, parser: IngestParser, workdir: Path, mineru_options: MinerUOptions | None = None):
    source = Path(input_path)
    if not source.exists():
        raise ValueError(f"input file does not exist: {source}")

    selected_parser = _resolve_ingest_parser(source, parser)
    if selected_parser == IngestParser.MARKDOWN:
        parse_result = MarkdownParser().parse(source)
    elif selected_parser == IngestParser.MINERU:
        parse_result = MinerUAdapter(options=mineru_options).parse(source, workdir)
    else:
        raise ValueError(f"unsupported parser: {parser.value}")

    return EvidenceBuilder().build(parse_result)


def _resolve_ingest_parser(input_path: Path, parser: IngestParser) -> IngestParser:
    if parser != IngestParser.AUTO:
        return parser

    suffix = input_path.suffix.lower()
    if suffix == ".md":
        return IngestParser.MARKDOWN
    if suffix == ".pdf":
        return IngestParser.MINERU
    raise ValueError(f"unsupported input format for auto parser: {input_path.suffix or '<none>'}; use .md or .pdf")


def _mineru_status() -> dict:
    path = shutil.which("mineru")
    try:
        package_version = metadata.version("mineru")
    except metadata.PackageNotFoundError:
        package_version = None

    cli_version = None
    error = None
    if path:
        try:
            completed = subprocess.run([path, "--version"], check=True, capture_output=True, text=True)
            cli_version = (completed.stdout or completed.stderr).strip() or None
        except (OSError, subprocess.CalledProcessError) as exc:
            error = str(exc)

    return {
        "command_found": path is not None,
        "path": path,
        "package_version": package_version,
        "cli_version": cli_version,
        "error": error,
    }


def _project_memory_metadata(*, memory: dict, failures: dict) -> dict | None:
    preferences = memory.get("preferences", [])
    failure_patterns = failures.get("failure_patterns", [])
    if not preferences and not failure_patterns:
        return None
    return {"project_memory": {"preferences": preferences, "failure_patterns": failure_patterns}}


def _format_label(document: PlanDocument) -> str:
    labels = {
        "versioned": "formal schema",
        "legacy_slides": "legacy compatibility (slides without schema_version)",
        "legacy_slide_specs": "legacy compatibility (slide_specs)",
        "bare_pptspec": "legacy compatibility (bare PptSpec)",
    }
    return labels.get(document.source_type, document.source_type)


def _load_executable_plan(path: Path, *, command_name: str) -> PlanDocument:
    report = validate_plan_document(path)
    if not report.ok:
        _print_execution_validation_error(report, command_name=command_name)
        raise typer.Exit(code=1)

    if report.format == "legacy compatibility":
        console.print(f"Warning: {command_name} is using a legacy compatibility plan file")
        for warning in report.warnings:
            console.print(f"Warning: {warning}")

    return read_plan_document(path)


def _print_execution_validation_error(report: ValidateReport, command_name: str) -> None:
    if report.format == "unsupported schema version":
        version = report.schema_version if report.schema_version is not None else "unknown"
        err_console.print(
            f"[bold red]{command_name}[/bold red]: unsupported future schema version: {version}, "
            f"current supported version is {PLAN_SCHEMA_VERSION}"
        )
        return

    message = report.errors[0] if report.errors else "invalid plan schema"
    err_console.print(f"[bold red]{command_name}[/bold red]: {message}")


def _print_validate_report(report: ValidateReport) -> None:
    console.print(f"Path: {report.path}")
    console.print(f"Schema Version: {report.schema_version if report.schema_version is not None else 'none'}")
    console.print(f"Format: {report.format}")
    console.print(f"Source Type: {report.source_type}")
    if report.title is not None:
        console.print(f"Slides: {report.slides_count}")
        console.print(f"Title: {report.title}")
        console.print(f"Request Topic: {report.request_topic}")
        console.print(f"Request Audience: {report.request_audience}")
    for warning in report.warnings:
        console.print(f"Warning: {warning}")
    for error in report.errors:
        err_console.print(f"Error: {error}")


@llm_app.command("providers")
def list_providers() -> None:
    """List supported LLM providers and models."""
    for name, spec in PROVIDER_SPECS.items():
        console.print(f"{name}: {spec.base_url}")
        for model in spec.models:
            console.print(f"  - {model}")


@llm_app.command("configure")
def configure_llm(
    provider: Annotated[str, typer.Option("--provider", help="Provider name.")],
    model: Annotated[str, typer.Option("--model", help="Model name for the provider.")],
) -> None:
    """Persist the default provider and model selection."""
    selection = save_selection(provider, model)
    console.print(f"Saved default planner: {selection.provider} / {selection.model}")


@llm_app.command("set-key")
def set_key(
    provider: Annotated[str, typer.Argument(help="Provider name.")],
    api_key: Annotated[str, typer.Option("--api-key", prompt=True, hide_input=True, help="API key to store locally.")],
) -> None:
    """Store a provider API key in the local workspace."""
    validate_provider(provider)
    path = save_api_key(provider, api_key)
    console.print(f"Saved API key for {provider} to [bold]{path}[/bold]")


@llm_app.command("show")
def show_llm_config() -> None:
    """Show default selection and local key presence."""
    selection = load_selection()
    if selection:
        console.print(f"Default Planner: {selection.provider} / {selection.model}")
    else:
        console.print("Default Planner: not configured")
    for status in key_statuses():
        console.print(f"{status.provider}: api_key={'yes' if status.has_key else 'no'}")


@skill_app.command("init")
def skill_init(name: Annotated[str, typer.Argument(help="Skill name.")]) -> None:
    """Create a project-local markdown skill template."""
    target = project_skill_dir(Path.cwd()) / name
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "skill.json"
    markdown_path = target / "skill.md"
    if not manifest_path.exists():
        manifest_path.write_text(
            json.dumps(
                {
                    "name": name,
                    "description": "Describe what this skill helps with.",
                    "when_to_use": "Use when the user asks for this workflow.",
                    "type": "markdown",
                    "input_schema": {"type": "object", "properties": {}},
                    "allowed_builtin_skills": ["scan_workspace", "generate_plan", "show_current_plan", "revise_plan", "build_ppt"],
                    "requires_approval": False,
                    "is_read_only": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if not markdown_path.exists():
        markdown_path.write_text(
            f"# {name}\n\nDescribe the workflow instructions for this skill.\n\nDo not build PPTX directly; use built-in skills and require approval before build.\n",
            encoding="utf-8",
        )
    console.print(f"Created skill template at [bold]{target}[/bold]")


@skill_app.command("add")
def skill_add(
    path_or_git_url: Annotated[str, typer.Argument(help="Local skill directory or Git URL.")],
    name: Annotated[str | None, typer.Option("--name", help="Override imported skill directory name.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing project skill directory.")] = False,
) -> None:
    """Import a local or GitHub Claude/ppt-agent skill into .ppt-agent/skills."""
    source_path: Path
    temp_path: Path | None = None

    try:
        if _looks_like_git_url(path_or_git_url):
            temp_path = Path.cwd() / ".ppt-agent" / "tmp" / f"skill-add-{uuid4().hex}"
            source_path = temp_path / "repo"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(["git", "clone", "--depth", "1", path_or_git_url, str(source_path)], check=True)
            except subprocess.CalledProcessError as exc:
                err_console.print(f"skill add: git clone failed for {path_or_git_url}: exit code {exc.returncode}")
                raise typer.Exit(code=1) from exc
        else:
            source_path = Path(path_or_git_url).expanduser().resolve()
            if not source_path.exists() or not source_path.is_dir():
                err_console.print(f"skill add: not a directory: {source_path}")
                raise typer.Exit(code=1)

        loaded = load_user_skill(source_path, source="imported")
        target_name = name or loaded.name or source_path.name
        target = project_skill_dir(Path.cwd()) / target_name
        if target.exists():
            if not force:
                err_console.print(f"skill add: {target} already exists; use --force to overwrite")
                raise typer.Exit(code=1)
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, target)
        imported = load_user_skill(target, source="project")
        console.print(f"Imported skill to [bold]{target}[/bold]")
        console.print(f"name: {imported.name}")
        console.print(f"enabled: {'yes' if imported.enabled else 'no'}")
        for error in imported.validation_errors:
            console.print(f"Warning: {error}")
    finally:
        if temp_path is not None:
            shutil.rmtree(temp_path, ignore_errors=True)


@skill_app.command("convert")
def skill_convert(claude_skill_dir: Annotated[Path, typer.Argument(help="Claude skill directory containing SKILL.md.")]) -> None:
    """Generate skill.json for a Claude-style SKILL.md directory."""
    source = claude_skill_dir.expanduser().resolve()
    loaded = load_user_skill(source, source="project")
    if not loaded.manifest:
        for error in loaded.validation_errors:
            err_console.print(f"Error: {error}")
        raise typer.Exit(code=1)
    target_dir = project_skill_dir(Path.cwd()) / loaded.name
    target_dir.mkdir(parents=True, exist_ok=True)
    if loaded.skill_md_path:
        destination_md = target_dir / loaded.skill_md_path.name
        if loaded.skill_md_path.resolve() != destination_md.resolve():
            shutil.copy2(loaded.skill_md_path, destination_md)
    manifest_path = target_dir / "skill.json"
    manifest_path.write_text(loaded.manifest.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Converted skill to [bold]{target_dir}[/bold]")
    console.print(f"Wrote manifest: {manifest_path}")


@skill_app.command("list")
def skill_list() -> None:
    """List project and global user skills."""
    skills = load_user_skills(Path.cwd())
    if not skills:
        console.print("No user skills found.")
        return
    for skill in skills:
        status = "enabled" if skill.enabled else "invalid"
        description = skill.manifest.description if skill.manifest else ""
        console.print(f"{skill.name} [{skill.source}] {status} - {description}")


@skill_app.command("validate")
def skill_validate(path_or_name: Annotated[str, typer.Argument(help="Skill path or name.")]) -> None:
    """Validate a user skill manifest and markdown file."""
    candidate = Path(path_or_name)
    if not candidate.exists():
        candidate = project_skill_dir(Path.cwd()) / path_or_name
    skill = load_user_skill(candidate, source="project")
    console.print(f"name: {skill.name}")
    console.print(f"path: {skill.path}")
    console.print(f"enabled: {'yes' if skill.enabled else 'no'}")
    if skill.validation_errors:
        for error in skill.validation_errors:
            err_console.print(f"Error: {error}")
        raise typer.Exit(code=1)
    console.print("Validation OK")


def _looks_like_git_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://") or lowered.endswith(".git")


@llm_app.command("test")
def test_llm(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider override for the connection test.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model override for the connection test.")] = None,
) -> None:
    """Test the configured or selected LLM provider/model/key."""
    _validate_llm_override(provider=provider, model=model)
    try:
        result = test_planner_connection(provider=provider, model=model)
    except PlannerConfigError as exc:
        err_console.print(f"[bold red]llm test[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[bold red]llm test[/bold red]: connection test failed: {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        err_console.print(f"[bold red]llm test[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Provider: {result.provider}")
    console.print(f"Model: {result.model}")
    console.print(f"Key Status: {result.key_status}")
    console.print(f"Connection OK: {'yes' if result.connection_ok else 'no'}")


def _validate_llm_override(*, provider: str | None, model: str | None) -> None:
    if provider and not model:
        saved = load_selection()
        if not saved or saved.provider != provider:
            raise typer.BadParameter("--model is required when --provider does not match a saved provider selection")
    if model and not provider:
        raise typer.BadParameter("--provider is required when --model is supplied")
    if provider and model:
        try:
            validate_provider(provider)
            validate_model(provider, model)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc


def _invoke_graph_or_exit(graph, state: AgentState) -> dict:
    try:
        return graph.invoke(state.model_dump(mode="json"))
    except PlannerConfigError as exc:
        err_console.print(f"[bold red]planner[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[bold red]planner[/bold red]: provider request failed: {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        err_console.print(f"[bold red]planner[/bold red]: invalid planner response: {exc}")
        raise typer.Exit(code=1) from exc


def _stream_graph_or_exit(graph, state: dict):
    try:
        yield from graph.stream(state)
    except PlannerConfigError as exc:
        err_console.print(f"[bold red]planner[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[bold red]planner[/bold red]: provider request failed: {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        err_console.print(f"[bold red]planner[/bold red]: invalid planner response: {exc}")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
