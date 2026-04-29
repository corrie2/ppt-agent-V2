from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
import typer
from rich.console import Console

from ppt_agent.domain.models import AgentMode, AgentState, DeckIntent, PptSpec
from ppt_agent.agent.skill_loader import load_user_skill, load_user_skills, project_skill_dir
from ppt_agent.graph.agent import create_agent_graph
from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model, validate_provider
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


@app.command()
def plan(
    topic: Annotated[str, typer.Argument(help="Presentation topic or goal.")],
    spec: Annotated[Path, typer.Option("--spec", "-s", help="Where to write the structured spec.")] = Path("deck_spec.json"),
    provider: Annotated[str | None, typer.Option("--provider", help="LLM provider override for planning.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="LLM model override for planning.")] = None,
) -> None:
    """Create a structured deck spec without building a PPTX."""
    _validate_llm_override(provider=provider, model=model)
    graph = create_agent_graph()
    memory = retrieve_project_memory(Path.cwd(), query=topic)
    failures = retrieve_failure_patterns(Path.cwd(), query=topic)
    intent = DeckIntent(
        topic=topic,
        project_preferences=memory.get("preferences", []),
        failure_patterns=failures.get("failure_patterns", []),
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
) -> None:
    """Build a PPTX from a unified plan/spec JSON file with schema_version."""
    document = _load_executable_plan(spec, command_name="build")
    ppt_spec = document.spec
    result = build_pptx(ppt_spec, out)
    console.print(f"Wrote PPTX to [bold]{result.path}[/bold]")


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
