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

from ppt_agent.domain.analysis import PaperAnalysis
from ppt_agent.domain.evidence import ClaimEvidence, EvidencePack, FigureAsset, SectionEvidence, TableAsset
from ppt_agent.domain.models import AgentMode, AgentState, DeckIntent, PptSpec
from ppt_agent.agent.skill_loader import load_user_skill, load_user_skills, project_skill_dir
from ppt_agent.graph.agent import create_agent_graph
from ppt_agent.ingest import EvidenceBuilder, MarkdownParser, MinerUAdapter
from ppt_agent.ingest.mineru_adapter import MinerUOptions
from ppt_agent.llm.analyzer import generate_paper_analysis_with_llm
from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model, validate_provider
from ppt_agent.runtime.document_qa import DocumentQaReport, run_document_qa, run_pptx_render_qa, write_document_qa_report
from ppt_agent.runtime.document_repair import repair_plan_spec
from ppt_agent.runtime.agent_llm import default_agent_llm_config, write_default_agent_llm_config
from ppt_agent.runtime.plan_polisher import polish_plan_spec
from ppt_agent.runtime.planner import resolve_planner_selection, test_planner_connection
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.runtime.harness import HarnessAction, HarnessRunner, read_events
from ppt_agent.runtime.harness.manifest import invalidate_from_stage, load_manifest, resolve_task_dir, task_root
from ppt_agent.runtime.visual_quality import evaluate_pptx_visual_quality, visual_quality_report_path
from ppt_agent.storage.llm_settings import key_statuses, load_api_key, load_selection, save_api_key, save_selection
from ppt_agent.storage.project_memory import record_execution_trace, record_project_memory, retrieve_failure_patterns, retrieve_project_memory
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
agent_app = typer.Typer(help="Multi-agent configuration")
task_app = typer.Typer(help="Harness task inspection, recovery, and retry commands")
feedback_app = typer.Typer(help="Record feedback into project memory")
console = Console()
err_console = Console(stderr=True)
app.add_typer(llm_app, name="llm")
app.add_typer(skill_app, name="skill")
app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(feedback_app, name="feedback")


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
    analysis: Annotated[Path | None, typer.Option("--analysis", help="paper_analysis.json to guide the generated plan.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="LLM provider override for planning.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="LLM model override for planning.")] = None,
    allow_fallback: Annotated[bool, typer.Option("--allow-fallback", help="Use deterministic planner when configured LLM key is missing.")] = False,
    skill_template: Annotated[list[str] | None, typer.Option("--skill-template", help="Apply and auto-create a v2 skill template. Can be repeated.")] = None,
) -> None:
    """Create a structured deck spec without building a PPTX."""
    _validate_llm_override(provider=provider, model=model)
    evidence_pack: EvidencePack | None = None
    paper_analysis: PaperAnalysis | None = None
    if evidence is not None:
        try:
            evidence_pack = _read_evidence_pack(evidence)
        except ValueError as exc:
            err_console.print(f"[bold red]plan[/bold red]: {exc}")
            raise typer.Exit(code=1) from exc
    if analysis is not None:
        try:
            paper_analysis = PaperAnalysis.model_validate(_read_json_file(analysis, label="paper analysis"))
        except ValueError as exc:
            err_console.print(f"[bold red]plan[/bold red]: {exc}")
            raise typer.Exit(code=1) from exc

    effective_topic = topic or (paper_analysis.paper_title if paper_analysis and paper_analysis.paper_title else None) or _topic_from_evidence(evidence_pack)
    if not effective_topic:
        err_console.print("[bold red]plan[/bold red]: topic is required unless --evidence is provided")
        raise typer.Exit(code=2)
    applied_skill_templates = _ensure_skill_templates(Path.cwd(), skill_template or [])

    graph = create_agent_graph()
    if evidence_pack is None:
        memory = retrieve_project_memory(Path.cwd(), query=effective_topic)
        failures = retrieve_failure_patterns(Path.cwd(), query=effective_topic)
    else:
        memory = {"preferences": []}
        failures = {"failure_patterns": []}
    evidence_digest = _evidence_digest(evidence_pack, evidence_path=evidence) if evidence_pack is not None else None
    source_digest = _planning_source_digest(evidence_digest=evidence_digest, paper_analysis=paper_analysis, analysis_path=analysis)
    intent = DeckIntent(
        topic=effective_topic,
        project_preferences=memory.get("preferences", []),
        failure_patterns=failures.get("failure_patterns", []),
        source_digest=source_digest,
        source_context=(evidence_digest or {}).get("evidence_items", []) if evidence_digest else [],
        applied_skills=applied_skill_templates,
    )
    _print_planner_run_info(
        evidence_digest=evidence_digest,
        provider=provider,
        model=model,
        allow_fallback=allow_fallback,
    )
    state = AgentState(
        intent=intent,
        mode=AgentMode.PLAN,
        planner_provider=provider,
        planner_model=model,
        allow_fallback=allow_fallback,
    )
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
    _print_multi_agent_artifacts(ppt_spec)


@app.command()
def analyze(
    evidence: Annotated[Path, typer.Option("--evidence", help="EvidencePack JSON to analyze.")],
    analysis: Annotated[Path, typer.Option("--analysis", help="Where to write paper_analysis.json.")],
    provider: Annotated[str | None, typer.Option("--provider", help="LLM provider override for analysis.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="LLM model override for analysis.")] = None,
) -> None:
    """Analyze a paper EvidencePack into paper_analysis.json without creating slides."""
    _validate_llm_override(provider=provider, model=model)
    try:
        evidence_pack = _read_evidence_pack(evidence)
    except ValueError as exc:
        err_console.print(f"[bold red]analyze[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc
    resolved_provider, resolved_model = resolve_planner_selection(provider=provider, model=model)
    if not resolved_provider or not resolved_model:
        err_console.print("[bold red]analyze[/bold red]: no provider/model configured. Run `ppt-agent llm configure --provider <provider> --model <model>`.")
        raise typer.Exit(code=1)
    api_key = load_api_key(resolved_provider)
    if not api_key:
        err_console.print(
            f"[bold red]analyze[/bold red]: missing API key for provider {resolved_provider}. "
            f"Run `ppt-agent llm set-key {resolved_provider} --api-key <key>`."
        )
        raise typer.Exit(code=1)

    evidence_digest = _evidence_digest(evidence_pack, evidence_path=evidence)
    try:
        result = generate_paper_analysis_with_llm(
            evidence_digest,
            provider=resolved_provider,
            model=resolved_model,
            api_key=api_key,
        )
    except (httpx.HTTPError, ValueError) as exc:
        err_console.print(f"[bold red]analyze[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc
    analysis.parent.mkdir(parents=True, exist_ok=True)
    analysis.write_text(result.to_json(), encoding="utf-8")
    console.print(f"Analyzer: llm {resolved_provider}/{resolved_model}")
    console.print(f"Wrote analysis to [bold]{analysis}[/bold]")


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
    debug_source_trace: Annotated[
        bool,
        typer.Option("--debug-source-trace", help="Include full Source Trace in speaker notes for debugging."),
    ] = False,
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
    result = build_pptx(
        ppt_spec,
        out,
        evidence_pack=evidence_pack,
        evidence_path=evidence,
        debug_source_trace=debug_source_trace,
    )
    render_report = run_pptx_render_qa(
        ppt_spec,
        pptx_path=result.path,
        debug_source_trace=debug_source_trace,
    )
    for issue in render_report.issues:
        console.print(f"Warning: {issue.message}")
    visual_report_path = visual_quality_report_path(result.path)
    visual_report = evaluate_pptx_visual_quality(ppt_spec, result.path, report_path=visual_report_path)
    if not visual_report.ok:
        console.print(f"Warning: visual quality score {visual_report.score}: {visual_report.summary}")
    console.print(f"Wrote visual quality report to [bold]{visual_report_path}[/bold]")
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


@app.command("polish-plan")
def polish_plan(
    input_path: Annotated[
        Path,
        typer.Argument(help=f"Path to an existing analysis/LLM plan JSON. Schema={PLAN_SCHEMA_VERSION} and legacy plans are supported."),
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the presentation-ready plan JSON.")],
) -> None:
    """Compress an existing plan into a presentation-ready plan for rendering."""
    try:
        document = read_plan_document(input_path)
        result = polish_plan_spec(document.spec)
        write_plan_document(
            out,
            intent=_intent_from_plan_payload(document.payload, result.spec),
            spec=result.spec,
            mode=document.payload.get("mode", "plan"),
            approved=document.payload.get("approved", False),
            transitions=[*document.payload.get("transitions", []), "polish_plan"],
            theme=document.payload.get("theme"),
        )
    except ValueError as exc:
        err_console.print(f"[bold red]polish-plan[/bold red]: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Input: {input_path}")
    console.print(f"Output: {out}")
    console.print(f"Slides Changed: {result.slides_changed}")
    console.print(f"Bullets Shortened: {result.bullets_shortened}")
    console.print(f"Notes Extended: {result.notes_extended}")


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
    skill_template: Annotated[list[str] | None, typer.Option("--skill-template", help="Apply and auto-create a v2 skill template. Can be repeated.")] = None,
) -> None:
    """Run the agent loop."""
    _validate_llm_override(provider=provider, model=model)
    applied_skill_templates = _ensure_skill_templates(Path.cwd(), skill_template or [])
    if from_plan is not None:
        document = _load_executable_plan(from_plan, command_name="from-plan")
        loaded_spec = document.spec
        effective_topic = document.payload.get("request", {}).get("topic", loaded_spec.title)
        if topic:
            console.print("[bold]plan[/bold]: using plan from file; ignoring provided topic")

        state = AgentState(
            intent=DeckIntent(
                topic=effective_topic,
                audience=loaded_spec.audience,
                output_path=str(out),
                applied_skills=[*loaded_spec.applied_skills, *applied_skill_templates],
            ),
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
                applied_skills=applied_skill_templates,
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
                if result.get("spec"):
                    _print_multi_agent_artifacts(PptSpec.model_validate(result["spec"]))
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
                console.print("[bold]visual[/bold]: evaluating generated deck quality")
            elif node_name == "visual_quality":
                score = (result.get("visual_quality_report") or {}).get("score")
                if score is not None:
                    console.print(f"[bold]visual[/bold]: score {score}")
                if result.get("visual_quality_report_path"):
                    console.print(f"[bold]visual[/bold]: report {result['visual_quality_report_path']}")
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


def _print_multi_agent_artifacts(spec: PptSpec) -> None:
    source = spec.source_digest or {}
    if source.get("type") != "multi_agent_pipeline":
        return
    task_dir = source.get("task_dir")
    if task_dir:
        console.print(f"[bold]agents[/bold]: artifacts written to [bold]{task_dir}[/bold]")


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
    sections = _section_summaries(pack)
    figures = _figure_summaries(pack)
    tables = _table_summaries(pack)
    claims = _claim_summaries(pack)
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
        "selection_summary": {
            "sections_total": len(pack.sections),
            "figures_total": len(pack.figures),
            "tables_total": len(pack.tables),
            "claims_total": len(pack.claims),
            "sections_selected": len(sections),
            "figures_selected": len(figures),
            "tables_selected": len(tables),
            "claims_selected": len(claims),
            "missing_roles": _missing_section_roles(sections),
        },
        "evidence_items": [*sections, *figures, *tables, *claims],
    }


def _planning_source_digest(
    *,
    evidence_digest: dict | None,
    paper_analysis: PaperAnalysis | None,
    analysis_path: Path | None,
) -> dict | None:
    if evidence_digest is None and paper_analysis is None:
        return None
    if paper_analysis is None:
        return evidence_digest
    payload = {
        "type": "paper_analysis",
        "analysis_path": str(analysis_path) if analysis_path else None,
        "paper_analysis": paper_analysis.model_dump(mode="json"),
    }
    if evidence_digest is not None:
        payload["evidence_digest"] = evidence_digest
        payload["type"] = "paper_analysis_with_evidence"
    return payload


def _section_summaries(pack: EvidencePack) -> list[dict]:
    selected = _select_sections(pack.sections)
    return [
        {
            "type": "section",
            "role": role,
            "evidence_id": section.id,
            "source_file": section.source_file,
            "page": section.page,
            "heading": section.heading,
            "text": _truncate(section.text, limit=1000),
            "why_selected": why,
            "score": round(score, 3),
        }
        for section, role, why, score in selected
    ]


def _figure_summaries(pack: EvidencePack) -> list[dict]:
    selected = _select_figures(pack.figures)
    return [
        {
            "type": "figure",
            "role": role,
            "evidence_id": figure.id,
            "source_file": figure.source_file,
            "page": figure.page,
            "caption": figure.caption,
            "text": _truncate(figure.text or "", limit=700),
            "path": figure.path,
            "why_selected": why,
            "score": round(score, 3),
        }
        for figure, role, why, score in selected
    ]


def _table_summaries(pack: EvidencePack) -> list[dict]:
    selected = _select_tables(pack.tables)
    return [
        {
            "type": "table",
            "role": role,
            "evidence_id": table.id,
            "source_file": table.source_file,
            "page": table.page,
            "caption": table.caption,
            "text": _truncate(table.text or "", limit=700),
            "why_selected": why,
            "score": round(score, 3),
        }
        for table, role, why, score in selected
    ]


def _claim_summaries(pack: EvidencePack) -> list[dict]:
    selected = _select_claims(pack.claims)
    return [
        {
            "type": "claim",
            "role": role,
            "evidence_id": claim.id,
            "source_file": claim.source_file,
            "page": claim.page,
            "text": _truncate(claim.text, limit=500),
            "supporting_evidence_ids": claim.supporting_evidence_ids,
            "confidence": claim.confidence,
            "why_selected": why,
            "score": round(score, 3),
        }
        for claim, role, why, score in selected
    ]


SECTION_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "abstract": ("abstract", "summary"),
    "introduction": ("introduction", "intro"),
    "problem_or_motivation": ("problem", "motivation", "challenge", "background"),
    "method_or_approach": ("method", "approach", "architecture", "framework", "algorithm", "system", "pipeline"),
    "experiment_or_evaluation": ("experiment", "evaluation", "setup", "dataset", "baseline", "metric"),
    "results": ("result", "performance", "comparison", "benchmark", "outperform"),
    "ablation_or_analysis": ("ablation", "analysis", "sensitivity", "case study", "discussion"),
    "conclusion_or_limitations": ("conclusion", "limitation", "future work", "takeaway"),
}

FIGURE_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "method_overview": ("framework", "architecture", "pipeline", "overview", "workflow"),
    "algorithm_or_component": ("algorithm", "module", "component"),
    "result": ("result", "performance", "comparison", "benchmark"),
    "analysis_or_ablation": ("ablation", "sensitivity", "analysis", "case study"),
}

TABLE_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "main_results": ("result", "performance", "comparison", "benchmark", "score"),
    "ablation": ("ablation", "variant", "sensitivity", "component"),
    "dataset_or_setup": ("dataset", "setup", "baseline", "metric", "statistics"),
}


def _select_sections(sections: list[SectionEvidence], *, max_items: int = 36) -> list[tuple[SectionEvidence, str, str, float]]:
    selected: dict[str, tuple[SectionEvidence, str, str, float]] = {}
    used_ids: set[str] = set()
    scored = [_score_section(section) for section in sections]

    for role in SECTION_ROLE_KEYWORDS:
        candidates = [item for item in scored if item[1] == role and item[0].id not in used_ids]
        if not candidates:
            continue
        best = max(candidates, key=lambda item: (item[3], _text_density(item[0].text)))
        selected[best[0].id] = best
        used_ids.add(best[0].id)

    remaining = [item for item in scored if item[0].id not in used_ids]
    remaining.sort(key=lambda item: (item[3], _text_density(item[0].text)), reverse=True)
    for item in remaining:
        if len(selected) >= max_items:
            break
        if item[3] <= 0 and len(selected) >= min(max_items, 12):
            break
        selected[item[0].id] = item
    return list(selected.values())


def _score_section(section: SectionEvidence) -> tuple[SectionEvidence, str, str, float]:
    heading = section.heading or ""
    sample = f"{heading} {section.text[:500]}".lower()
    best_role = "high_information"
    best_hits = 0
    for role, keywords in SECTION_ROLE_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in sample)
        if hits > best_hits:
            best_role = role
            best_hits = hits
    density = min(_text_density(section.text) / 1200, 0.25)
    score = min(1.0, 0.45 + best_hits * 0.18 + density) if best_hits else min(0.5, 0.2 + density)
    why = f"heading/text matched {best_role} keywords" if best_hits else "high information density section"
    return section, best_role, why, score


def _select_figures(figures: list[FigureAsset], *, max_items: int = 12) -> list[tuple[FigureAsset, str, str, float]]:
    scored = [_score_visual(figure, FIGURE_ROLE_KEYWORDS, fallback_role="supporting_figure") for figure in figures]
    return _pick_by_role_then_score(scored, max_items=max_items)


def _select_tables(tables: list[TableAsset], *, max_items: int = 12) -> list[tuple[TableAsset, str, str, float]]:
    scored = [_score_visual(table, TABLE_ROLE_KEYWORDS, fallback_role="supporting_table") for table in tables]
    if len(scored) <= max_items:
        return scored
    return _pick_by_role_then_score(scored, max_items=max_items)


def _select_claims(claims: list[ClaimEvidence], *, max_items: int = 24) -> list[tuple[ClaimEvidence, str, str, float]]:
    scored: list[tuple[ClaimEvidence, str, str, float]] = []
    for claim in claims:
        text = claim.text.lower()
        role = "claim"
        if any(token in text for token in ("result", "outperform", "improve", "performance")):
            role = "result_claim"
        elif any(token in text for token in ("method", "approach", "propose", "framework")):
            role = "method_claim"
        confidence = claim.confidence if claim.confidence is not None else 0.5
        support_bonus = min(len(claim.supporting_evidence_ids) * 0.08, 0.24)
        scored.append((claim, role, "claim confidence and supporting evidence ranking", min(1.0, confidence + support_bonus)))
    return sorted(scored, key=lambda item: item[3], reverse=True)[:max_items]


def _score_visual(item: FigureAsset | TableAsset, role_keywords: dict[str, tuple[str, ...]], *, fallback_role: str) -> tuple:
    haystack = f"{item.caption or ''} {getattr(item, 'text', '') or ''}".lower()
    best_role = fallback_role
    best_hits = 0
    for role, keywords in role_keywords.items():
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits > best_hits:
            best_role = role
            best_hits = hits
    score = min(1.0, 0.35 + best_hits * 0.2 + min(len(haystack) / 1400, 0.2)) if best_hits else 0.3
    why = f"caption/text matched {best_role} keywords" if best_hits else "included as supporting visual evidence"
    return item, best_role, why, score


def _pick_by_role_then_score(scored: list[tuple], *, max_items: int) -> list[tuple]:
    selected: dict[str, tuple] = {}
    for role in dict.fromkeys(item[1] for item in scored):
        candidates = [item for item in scored if item[1] == role]
        if candidates:
            best = max(candidates, key=lambda item: item[3])
            selected[best[0].id] = best
    for item in sorted(scored, key=lambda item: item[3], reverse=True):
        if len(selected) >= max_items:
            break
        selected.setdefault(item[0].id, item)
    return list(selected.values())


def _missing_section_roles(sections: list[dict]) -> list[str]:
    selected_roles = {section.get("role") for section in sections}
    return [role for role in SECTION_ROLE_KEYWORDS if role not in selected_roles]


def _text_density(text: str) -> int:
    return len({token.strip(".,:;()[]{}").lower() for token in text.split() if len(token.strip(".,:;()[]{}")) > 3})


def _print_planner_run_info(
    *,
    evidence_digest: dict | None,
    provider: str | None,
    model: str | None,
    allow_fallback: bool,
) -> None:
    resolved_provider, resolved_model = resolve_planner_selection(provider=provider, model=model)
    if resolved_provider and resolved_model and load_api_key(resolved_provider):
        console.print(f"Planner: llm {resolved_provider}/{resolved_model}")
    elif resolved_provider and resolved_model and allow_fallback:
        console.print("Planner: multi-agent pipeline fallback")
    elif resolved_provider and resolved_model:
        console.print(f"Planner: llm {resolved_provider}/{resolved_model} (missing key)")
    else:
        console.print("Planner: multi-agent pipeline")
    if evidence_digest:
        summary = evidence_digest.get("selection_summary") or {}
        console.print(
            "Evidence: "
            f"items={len(evidence_digest.get('evidence_items') or [])}, "
            f"sections={summary.get('sections_selected', 0)}/{summary.get('sections_total', 0)}, "
            f"figures={summary.get('figures_selected', 0)}/{summary.get('figures_total', 0)}, "
            f"tables={summary.get('tables_selected', 0)}/{summary.get('tables_total', 0)}, "
            f"claims={summary.get('claims_selected', 0)}/{summary.get('claims_total', 0)}"
        )


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
    ask_deepseek_pro: Annotated[
        bool,
        typer.Option(
            "--ask-deepseek-pro",
            help="When configuring deepseek-v4-flash, ask whether to use deepseek-v4-pro instead.",
        ),
    ] = False,
) -> None:
    """Persist the default provider and model selection."""
    if ask_deepseek_pro and provider == "deepseek" and model == "deepseek-v4-flash":
        if typer.confirm("Use deepseek-v4-pro instead of deepseek-v4-flash?", default=False):
            model = "deepseek-v4-pro"
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


@skill_app.command("init-template")
def skill_init_template(
    template: Annotated[str, typer.Argument(help="academic-paper-deck, course-teaching-deck, business-report-deck, or deck-quality-gate.")],
    name: Annotated[str | None, typer.Option("--name", help="Override output skill directory name.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing template skill.")] = False,
) -> None:
    """Create a v2 Skill template with policy, writing, layout, QA rules, and examples."""
    target = _write_skill_template(Path.cwd(), template, name=name, force=force, command_name="skill init-template")
    console.print(f"Created v2 skill template at [bold]{target}[/bold]")


def _write_skill_template(root: Path, template: str, *, name: str | None = None, force: bool = False, command_name: str = "skill template") -> Path:
    specs = _skill_template_specs()
    if template not in specs:
        raise typer.BadParameter(f"unknown template: {template}; choose one of {', '.join(sorted(specs))}")
    spec = specs[template]
    skill_name = name or spec["name"]
    target = project_skill_dir(root) / skill_name
    if target.exists():
        if not force:
            err_console.print(f"{command_name}: {target} already exists; use --force to overwrite")
            raise typer.Exit(code=1)
        shutil.rmtree(target)
    (target / "examples").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": skill_name,
        "description": spec["description"],
        "when_to_use": spec["when_to_use"],
        "type": "markdown",
        "input_schema": {"type": "object", "properties": {}},
        "allowed_builtin_skills": ["scan_workspace", "retrieve_project_memory", "generate_plan", "show_current_plan", "revise_plan"],
        "requires_approval": False,
        "is_read_only": True,
        "agent_scope": spec["agent_scope"],
        "applies_to": spec["applies_to"],
        "quality_gates": spec["quality_gates"],
        "artifacts": {
            "task_policy": "task_policy.md",
            "writing_rules": "writing_rules.md",
            "layout_rules": "layout_rules.md",
            "qa_rules": "qa_rules.json",
        },
        "examples": ["examples/request.json", "examples/expected_outline.json"],
        "version": "2.0.0",
    }
    (target / "skill.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "skill.md").write_text(spec["skill_md"], encoding="utf-8")
    (target / "task_policy.md").write_text(spec["task_policy"], encoding="utf-8")
    (target / "writing_rules.md").write_text(spec["writing_rules"], encoding="utf-8")
    (target / "layout_rules.md").write_text(spec["layout_rules"], encoding="utf-8")
    (target / "qa_rules.json").write_text(json.dumps(spec["qa_rules"], ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "examples" / "request.json").write_text(json.dumps(spec["example_request"], ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "examples" / "expected_outline.json").write_text(json.dumps(spec["expected_outline"], ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _ensure_skill_templates(root: Path, templates: list[str]) -> list[str]:
    applied: list[str] = []
    specs = _skill_template_specs()
    for template in templates:
        if template not in specs:
            raise typer.BadParameter(f"unknown skill template: {template}; choose one of {', '.join(sorted(specs))}")
        skill_name = specs[template]["name"]
        target = project_skill_dir(root) / skill_name
        if not target.exists():
            _write_skill_template(root, template, force=False, command_name="--skill-template")
            console.print(f"[bold]skill[/bold]: created template [bold]{skill_name}[/bold]")
        applied.append(skill_name)
    return applied


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


@agent_app.command("init-config")
def agent_init_config(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing agent config.")] = False,
) -> None:
    """Write the default multi-agent model routing config."""
    path = Path.cwd() / ".ppt-agent" / "agents" / "config.json"
    if path.exists() and not force:
        console.print(f"Agent config already exists at [bold]{path}[/bold]")
        console.print("Use --force to overwrite it.")
        return
    path = write_default_agent_llm_config(Path.cwd())
    console.print(f"Wrote agent config to [bold]{path}[/bold]")


@agent_app.command("show-config")
def agent_show_config() -> None:
    """Show the default multi-agent model routing config."""
    console.print(default_agent_llm_config().model_dump_json(indent=2))


def _skill_template_specs() -> dict[str, dict]:
    base_rules = {
        "required_fields": {"severity": "error", "description": "Every slide must have title and core message."},
        "max_bullets_per_slide": {"severity": "warning", "max": 3},
        "no_blank_slide_risk": {"severity": "error"},
    }
    return {
        "academic-paper-deck": {
            "name": "academic-paper-deck",
            "description": "Generate research paper and technical report presentation decks with citations, method/result structure, and compact academic narration.",
            "when_to_use": "Use for paper reading, thesis defense, research report, experiment result, and technical article presentation tasks.",
            "agent_scope": ["brief_outline", "content", "qa", "page_designer"],
            "applies_to": ["paper", "research", "academic", "experiment", "technical-report"],
            "quality_gates": ["citation_required_when_evidence", "method_result_structure", "max_bullets_per_slide"],
            "skill_md": "# academic-paper-deck\n\nUse this skill to turn research material into a rigorous but readable presentation. Preserve evidence, explain the problem-method-result chain, and avoid unsupported claims.\n",
            "task_policy": "# Task Policy\n\nPrioritize factual grounding. Build the narrative as: background, problem, prior gap, method, experiments, results, contribution, limitations, future work.\n",
            "writing_rules": "# Writing Rules\n\n- Use concise academic language.\n- Put the claim in the slide title or message.\n- Keep visible bullets to three or fewer.\n- Explain metrics and experimental setup before results.\n- Speaker notes may contain more detail than slide text.\n",
            "layout_rules": "# Layout Rules\n\n- Use diagrams for method slides.\n- Use comparison tables for baselines and ablations.\n- Use figure-caption layouts for paper figures.\n- Keep citations visible in notes or captions.\n",
            "qa_rules": {**base_rules, "citation_required_when_evidence": {"severity": "warning"}, "method_result_structure": {"severity": "warning"}},
            "example_request": {"topic": "Paper reading deck for a machine learning paper", "slides": 12, "audience": "graduate students"},
            "expected_outline": {"slides": ["Title", "Background", "Problem", "Method Overview", "Method Details", "Experiment Setup", "Main Results", "Ablation", "Limitations", "Takeaways"]},
        },
        "course-teaching-deck": {
            "name": "course-teaching-deck",
            "description": "Generate step-by-step course teaching decks with concept introduction, examples, visual explanations, and summary checks.",
            "when_to_use": "Use for class teaching, concept explanation, knowledge walkthrough, tutorial, and training material.",
            "agent_scope": ["brief_outline", "content", "qa", "page_designer"],
            "applies_to": ["course", "teaching", "tutorial", "training", "knowledge-explanation"],
            "quality_gates": ["concept_progression", "example_required", "max_bullets_per_slide"],
            "skill_md": "# course-teaching-deck\n\nUse this skill to explain knowledge gradually. Make every major concept concrete with examples, diagrams, and checkpoints.\n",
            "task_policy": "# Task Policy\n\nOrganize the deck as: motivation, prerequisite, concept, example, process, misconception, practice, summary.\n",
            "writing_rules": "# Writing Rules\n\n- Use second-person or classroom-friendly language.\n- Start each section with why the concept matters.\n- Introduce one idea per slide.\n- Pair abstract terms with examples.\n",
            "layout_rules": "# Layout Rules\n\n- Use process timelines for multi-step concepts.\n- Use side-by-side comparison for confusing ideas.\n- Use callout cards for key definitions.\n",
            "qa_rules": {**base_rules, "concept_progression": {"severity": "warning"}, "example_required": {"severity": "warning"}},
            "example_request": {"topic": "Teach DNS resolution to beginners", "slides": 10, "audience": "undergraduate students"},
            "expected_outline": {"slides": ["Motivation", "Prerequisites", "Core Concept", "Step-by-step Flow", "Example", "Common Mistakes", "Practice", "Summary"]},
        },
        "business-report-deck": {
            "name": "business-report-deck",
            "description": "Generate executive business report decks with conclusion-first structure, evidence-backed insights, and action recommendations.",
            "when_to_use": "Use for project reports, product reviews, business analysis, roadmap communication, and executive updates.",
            "agent_scope": ["brief_outline", "content", "design_chart", "qa", "page_designer"],
            "applies_to": ["business", "executive", "product", "roadmap", "operations"],
            "quality_gates": ["action_recommendation_required", "data_support_required", "max_bullets_per_slide"],
            "skill_md": "# business-report-deck\n\nUse this skill to produce decision-oriented reports. Lead with conclusions, support with evidence, and close with concrete actions.\n",
            "task_policy": "# Task Policy\n\nOrganize as: executive summary, current state, key insight, options, recommendation, roadmap, risks, ask.\n",
            "writing_rules": "# Writing Rules\n\n- Make titles assertive and conclusion-first.\n- Avoid vague recommendations.\n- Connect each data point to a decision.\n- Keep slides scannable for repeated review.\n",
            "layout_rules": "# Layout Rules\n\n- Use comparison tables for options.\n- Use roadmap timelines for execution plans.\n- Use metric cards for KPI summaries.\n",
            "qa_rules": {**base_rules, "action_recommendation_required": {"severity": "warning"}, "data_support_required": {"severity": "warning"}},
            "example_request": {"topic": "Quarterly product roadmap review", "slides": 12, "audience": "executive team"},
            "expected_outline": {"slides": ["Executive Summary", "Current State", "Key Metrics", "Insight", "Options", "Recommendation", "Roadmap", "Risks", "Decision Ask"]},
        },
        "deck-quality-gate": {
            "name": "deck-quality-gate",
            "description": "Reusable quality gate skill for checking slide structure, density, citations, renderability, and audience fit.",
            "when_to_use": "Use whenever generated deck plans or PPTX outputs need deterministic and reviewable quality checks.",
            "agent_scope": ["qa", "evaluator", "render_review", "page_designer"],
            "applies_to": ["qa", "review", "quality-gate", "harness"],
            "quality_gates": list(base_rules),
            "skill_md": "# deck-quality-gate\n\nUse this skill to enforce deck quality before downstream rendering or user delivery.\n",
            "task_policy": "# Task Policy\n\nBlock severe structural failures. Report warnings with actionable fixes. Prefer deterministic checks before LLM judgment.\n",
            "writing_rules": "# Writing Rules\n\n- Report issues with slide number, rule id, severity, and suggested fix.\n- Do not rewrite content unless asked by the repair stage.\n",
            "layout_rules": "# Layout Rules\n\n- Flag unsupported layouts.\n- Flag dense slides and likely overflow.\n- Flag missing visual hierarchy.\n",
            "qa_rules": base_rules,
            "example_request": {"topic": "Review generated deck quality", "slides": 10},
            "expected_outline": {"checks": list(base_rules)},
        },
    }


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


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host", help="Host interface for the web server.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port for the web server.")] = 7000,
    reload: Annotated[bool, typer.Option("--reload", help="Reload server when source files change.")] = False,
    preload_mineru: Annotated[bool, typer.Option("--preload-mineru/--no-preload-mineru", help="Start and warm a MinerU API service with PPT Agent Studio.")] = True,
    mineru_host: Annotated[str, typer.Option("--mineru-host", help="Host interface for the managed MinerU API service.")] = "127.0.0.1",
    mineru_port: Annotated[int, typer.Option("--mineru-port", help="Port for the managed MinerU API service.")] = 8000,
    mineru_preload_timeout: Annotated[int, typer.Option("--mineru-preload-timeout", help="Seconds to wait for the managed MinerU API service to become healthy.")] = 180,
) -> None:
    """Start the PPT Agent Studio web UI."""
    try:
        import uvicorn
    except ImportError as exc:
        err_console.print("[bold red]serve[/bold red]: missing uvicorn. Install the web dependencies first.")
        raise typer.Exit(code=1) from exc
    import os

    if preload_mineru:
        os.environ["PPT_AGENT_PRELOAD_MINERU"] = "1"
        os.environ["PPT_AGENT_MINERU_HOST"] = mineru_host
        os.environ["PPT_AGENT_MINERU_PORT"] = str(mineru_port)
        os.environ["PPT_AGENT_MINERU_PRELOAD_TIMEOUT"] = str(mineru_preload_timeout)
        os.environ["PPT_AGENT_MINERU_API_URL"] = f"http://{mineru_host}:{mineru_port}"
    else:
        os.environ["PPT_AGENT_PRELOAD_MINERU"] = "0"
    console.print(f"PPT Agent Studio: http://{host}:{port}")
    if preload_mineru:
        console.print(f"MinerU API preload: http://{mineru_host}:{mineru_port}")
    uvicorn.run("ppt_agent.server.app:app", host=host, port=port, reload=reload)


@task_app.command("list")
def task_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List Harness task runs in .ppt-agent/tasks."""
    root = task_root(Path.cwd())
    rows = []
    if root.exists():
        for manifest_file in sorted(root.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                manifest = load_manifest(manifest_file.parent)
            except ValueError:
                continue
            rows.append(
                {
                    "task_id": manifest.task_id,
                    "topic": manifest.topic,
                    "status": manifest.status,
                    "current_stage": manifest.current_stage,
                    "updated_at": manifest.updated_at,
                    "path": str(manifest_file.parent),
                }
            )
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        console.print("No Harness tasks found.")
        return
    for row in rows:
        console.print(f"{row['task_id']} [{row['status']}] {row['topic']}")
        console.print(f"  stage={row['current_stage'] or '-'} updated={row['updated_at']}")


@task_app.command("inspect")
def task_inspect(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit full manifest JSON.")] = False,
) -> None:
    """Inspect one Harness task manifest."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    manifest = load_manifest(task_dir)
    if json_output:
        typer.echo(manifest.model_dump_json(indent=2))
        return
    console.print(f"Task: [bold]{manifest.task_id}[/bold]")
    console.print(f"Topic: {manifest.topic}")
    console.print(f"Status: {manifest.status}")
    console.print(f"Current Stage: {manifest.current_stage or '-'}")
    console.print(f"Resume: last={manifest.resume.get('last_passed_stage')} next={manifest.resume.get('next_stage')}")
    console.print("Stages:")
    for stage in manifest.stages:
        issue_count = len(stage.issues)
        console.print(f"  - {stage.id}: {stage.status} issues={issue_count} output={stage.output_path or '-'}")
    if manifest.outputs:
        console.print("Outputs:")
        for name, path in manifest.outputs.items():
            console.print(f"  {name}: {path}")
    if manifest.reports:
        console.print("Reports:")
        for name, path in manifest.reports.items():
            console.print(f"  {name}: {path}")


@task_app.command("events")
def task_events(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of recent events to show.")] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON events.")] = False,
) -> None:
    """Show task event log."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    events = read_events(task_dir, limit=limit)
    if json_output:
        typer.echo(json.dumps(events, ensure_ascii=False, indent=2))
        return
    for event in events:
        stage = f" [{event['stage_id']}]" if event.get("stage_id") else ""
        console.print(f"{event.get('created_at')} {event.get('event')}{stage}")


@task_app.command("artifacts")
def task_artifacts(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON artifact list.")] = False,
) -> None:
    """List task artifacts from manifest and stage outputs."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    manifest = load_manifest(task_dir)
    artifacts = []
    for stage in manifest.stages:
        for kind, value in (("input", stage.input_path), ("output", stage.output_path), ("eval", stage.eval_path), ("status", stage.status_path)):
            if value:
                artifacts.append({"stage": stage.id, "kind": kind, "path": value})
    for name, path in manifest.outputs.items():
        artifacts.append({"stage": None, "kind": f"output:{name}", "path": path})
    for name, path in manifest.reports.items():
        artifacts.append({"stage": None, "kind": f"report:{name}", "path": path})
    if json_output:
        typer.echo(json.dumps(artifacts, ensure_ascii=False, indent=2))
        return
    for item in artifacts:
        prefix = item["stage"] or "task"
        console.print(f"{prefix} {item['kind']}: {item['path']}")


@task_app.command("resume")
def task_resume(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write a resumable plan JSON.")] = Path("resumed_plan.json"),
) -> None:
    """Recover the latest valid PptSpec from a task into a plan JSON."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    manifest = load_manifest(task_dir)
    ppt_spec_path = manifest.outputs.get("ppt_spec")
    if not ppt_spec_path:
        raise typer.BadParameter("task has no ppt_spec output to resume from")
    source = task_dir / ppt_spec_path
    if not source.exists():
        raise typer.BadParameter(f"ppt_spec output is missing: {source}")
    spec = PptSpec.model_validate(_read_json_file(source, label="ppt spec"))
    write_plan_document(
        out,
        intent=DeckIntent(topic=manifest.topic, audience=spec.audience),
        spec=spec,
        mode="plan",
        approved=False,
        transitions=["task_resume"],
        metadata={"harness_task": {"task_id": manifest.task_id, "source": str(task_dir)}},
    )
    console.print(f"Recovered plan from task [bold]{manifest.task_id}[/bold]")
    console.print(f"Output: [bold]{out}[/bold]")


@task_app.command("retry-stage")
def task_retry_stage(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    stage: Annotated[str, typer.Argument(help="Stage id to retry from, e.g. content.")],
    reason: Annotated[str, typer.Option("--reason", help="Why this stage is being invalidated.")] = "manual retry",
) -> None:
    """Invalidate a stage and every downstream stage so a future runner can retry from it."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    manifest = load_manifest(task_dir)
    if not manifest.stage(stage):
        raise typer.BadParameter(f"unknown stage: {stage}")
    invalidate_from_stage(task_dir, manifest, stage, reason=reason)
    console.print(f"Invalidated [bold]{stage}[/bold] and downstream stages for task [bold]{manifest.task_id}[/bold]")


@task_app.command("continue")
def task_continue(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    auto_rework: Annotated[bool, typer.Option("--auto-rework", help="Try bounded automatic regeneration when gates request rework.")] = False,
    max_rework: Annotated[int, typer.Option("--max-rework", help="Maximum automatic rework attempts per stage.")] = 1,
    json_output: Annotated[bool, typer.Option("--json", help="Emit runner action JSON.")] = False,
) -> None:
    """Advance a Harness task until completion, approval, rework, or failure."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    action = HarnessRunner(task_dir).run_until_blocked(auto_rework=auto_rework, max_rework=max_rework)
    _print_harness_action(action, json_output=json_output)


@task_app.command("run")
def task_run(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    auto_rework: Annotated[bool, typer.Option("--auto-rework", help="Try bounded automatic regeneration when gates request rework.")] = False,
    max_rework: Annotated[int, typer.Option("--max-rework", help="Maximum automatic rework attempts per stage.")] = 1,
    json_output: Annotated[bool, typer.Option("--json", help="Emit runner action JSON.")] = False,
) -> None:
    """Alias for task continue."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    action = HarnessRunner(task_dir).run_until_blocked(auto_rework=auto_rework, max_rework=max_rework)
    _print_harness_action(action, json_output=json_output)


@task_app.command("approve")
def task_approve(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    stage: Annotated[str, typer.Option("--stage", help="Approval stage id.")] = "plan_confirm",
    note: Annotated[str, typer.Option("--note", help="Approval note.")] = "approved",
    json_output: Annotated[bool, typer.Option("--json", help="Emit runner action JSON.")] = False,
) -> None:
    """Approve a waiting Harness confirmation point and continue."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    action = HarnessRunner(task_dir).approve(stage, note=note)
    _print_harness_action(action, json_output=json_output)


@task_app.command("reject")
def task_reject(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    stage: Annotated[str, typer.Option("--stage", help="Approval stage id.")] = "plan_confirm",
    reason: Annotated[str, typer.Option("--reason", help="Rejection reason.")] = "changes requested",
    json_output: Annotated[bool, typer.Option("--json", help="Emit runner action JSON.")] = False,
) -> None:
    """Reject a waiting Harness confirmation point."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    action = HarnessRunner(task_dir).reject(stage, reason=reason)
    _print_harness_action(action, json_output=json_output)


@task_app.command("gates")
def task_gates(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    stage: Annotated[str, typer.Option("--stage", help="Stage id to check.")] = "content",
    json_output: Annotated[bool, typer.Option("--json", help="Emit gate JSON.")] = False,
) -> None:
    """Run deterministic quality gates for one archived stage."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    gate = HarnessRunner(task_dir).run_stage_gate(stage)
    if json_output:
        typer.echo(gate.model_dump_json(indent=2))
        return
    console.print(f"Stage: [bold]{stage}[/bold]")
    console.print(f"Status: {gate.status} score={gate.score} next={gate.next_action}")
    for issue in gate.issues:
        console.print(f"  - {issue.severity} {issue.rule}: {issue.message}")


@task_app.command("preview")
def task_preview(
    task: Annotated[str, typer.Argument(help="Task id or task directory.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit runner action JSON.")] = False,
) -> None:
    """Generate page-level preview JSON files from an archived ppt_spec."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    runner = HarnessRunner(task_dir)
    runner.approve("plan_confirm", note="auto-approved for preview") if load_manifest(task_dir).stage("plan_confirm") and load_manifest(task_dir).stage("plan_confirm").status == "waiting_approval" else None
    action = runner.run_until_blocked()
    _print_harness_action(action, json_output=json_output)


def _print_harness_action(action: HarnessAction, *, json_output: bool = False) -> None:
    if json_output:
        typer.echo(action.model_dump_json(indent=2))
        return
    console.print(f"Task: [bold]{action.task_id}[/bold]")
    console.print(f"Action: {action.action}")
    console.print(f"Stage: {action.stage_id or '-'}")
    console.print(action.message)


@feedback_app.command("add")
def feedback_add(
    text: Annotated[str, typer.Argument(help="Feedback text to remember.")],
    type: Annotated[str, typer.Option("--type", help="preference, correction, failure, or accepted_output.")] = "preference",
    task: Annotated[str | None, typer.Option("--task", help="Optional related Harness task id/path.")] = None,
    stage: Annotated[str | None, typer.Option("--stage", help="Optional related stage id.")] = None,
) -> None:
    """Record user feedback into project memory."""
    metadata = {"task": task, "stage": stage}
    if type == "preference" or type == "correction":
        result = record_project_memory(Path.cwd(), feedback=text, category=type, source="user_feedback", metadata=metadata)
        console.print(f"Recorded project preference: {result['path']}")
    elif type in {"failure", "accepted_output"}:
        trace_type = "qa_failure" if type == "failure" else "accepted_output"
        result = record_execution_trace(Path.cwd(), event=text, trace_type=trace_type, payload=metadata)
        console.print(f"Recorded {trace_type}: {result['path']}")
    else:
        raise typer.BadParameter("--type must be preference, correction, failure, or accepted_output")


@feedback_app.command("accept")
def feedback_accept(
    task: Annotated[str, typer.Argument(help="Accepted Harness task id or path.")],
    note: Annotated[str, typer.Option("--note", help="Optional acceptance note.")] = "accepted generated output",
) -> None:
    """Record a task as an accepted output."""
    task_dir = resolve_task_dir(Path.cwd(), task)
    manifest = load_manifest(task_dir)
    result = record_execution_trace(
        Path.cwd(),
        event=note,
        trace_type="accepted_output",
        payload={"task_id": manifest.task_id, "topic": manifest.topic, "outputs": manifest.outputs, "reports": manifest.reports},
    )
    console.print(f"Recorded accepted output: {result['path']}")


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
