from __future__ import annotations

from typing import Any
from pathlib import Path

from pydantic import BaseModel, Field

from ppt_agent.agent.skill_registry import SkillDefinition
from ppt_agent.domain.models import AgentMode, AgentState, DeckIntent, PptSpec, SlideSpec
from ppt_agent.graph.agent import create_agent_graph
from ppt_agent.runtime.html_deck import build_html_deck, validate_html_deck
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.runtime.source_store import append_memory_event, digest_sources, index_source, ingest_sources, retrieve_source_context
from ppt_agent.runtime.workspace import scan_workspace
from ppt_agent.storage.project_memory import (
    record_execution_trace,
    record_project_memory,
    retrieve_failure_patterns,
    retrieve_project_memory,
)
from ppt_agent.shell.session import ShellSession
from ppt_agent.storage.plan_io import migrate_plan_document, read_plan_document, validate_plan_document, write_plan_document


class ScanWorkspaceInput(BaseModel):
    max_depth: int = Field(default=3, ge=1)


class ListSourcesInput(BaseModel):
    pass


class GeneratePlanInput(BaseModel):
    topic: str
    slides: int | None = Field(default=None, ge=1)
    min_slides: int | None = Field(default=None, ge=1)
    sources: list[str] | None = None
    audience: str | None = None
    tone: str | None = None
    plan_path: str | None = None
    output_format: str | None = None
    applied_skills: list[str] | None = None
    theme: str | None = None
    skill_root: str | None = None
    skill_md_path: str | None = None
    source_digest: dict[str, Any] | None = None
    source_context: list[dict[str, Any]] | None = None
    project_preferences: list[dict[str, Any]] | None = None
    failure_patterns: list[dict[str, Any]] | None = None


class ValidatePlanInput(BaseModel):
    plan_path: str | None = None


class MigratePlanInput(BaseModel):
    input_path: str
    output_path: str


class BuildPptInput(BaseModel):
    plan_path: str | None = None
    output_path: str | None = None


class BuildHtmlDeckInput(BaseModel):
    plan_path: str | None = None
    skill_name: str | None = None
    output_path: str | None = None
    theme: str | None = None


class DigestPdfSourcesInput(BaseModel):
    sources: list[str] | None = None


class IngestSourcesInput(BaseModel):
    sources: list[str] | None = None


class IndexSourceInput(BaseModel):
    source: str


class RetrieveSourceContextInput(BaseModel):
    sources: list[str] | None = None
    query: str = ""
    limit: int = Field(default=5, ge=1)


class RetrieveProjectMemoryInput(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1)


class RecordProjectMemoryInput(BaseModel):
    feedback: str
    category: str | None = None
    source: str = "user_feedback"
    metadata: dict[str, Any] | None = None


class RecordExecutionTraceInput(BaseModel):
    event: str
    payload: dict[str, Any] | None = None
    trace_type: str = "execution"


class RetrieveFailurePatternsInput(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1)


class RunFromPlanInput(BaseModel):
    plan_path: str | None = None
    output_path: str | None = None


class ShowCurrentPlanInput(BaseModel):
    pass


class RevisePlanInput(BaseModel):
    revision: str


class ListGeneratedFilesInput(BaseModel):
    pass


def register_default_skills(registry, *, session: ShellSession) -> None:
    skill_specs = [
        ("scan_workspace", "Scan the current workspace for source files.", ScanWorkspaceInput, scan_workspace_skill, {"is_read_only": True}),
        ("list_sources", "List currently discovered source files.", ListSourcesInput, list_sources_skill, {"is_read_only": True}),
        ("generate_plan", "Generate a PPT plan/spec file.", GeneratePlanInput, generate_plan_skill, {}),
        ("ingest_sources", "Index and digest selected source files into the project source store.", IngestSourcesInput, ingest_sources_skill, {}),
        ("index_source", "Index one source file into the project source store.", IndexSourceInput, index_source_skill, {}),
        (
            "retrieve_source_context",
            "Retrieve source chunks from the project source store for a query.",
            RetrieveSourceContextInput,
            retrieve_source_context_skill,
            {"is_read_only": True},
        ),
        (
            "retrieve_project_memory",
            "Retrieve project preferences and accepted outputs relevant to a query.",
            RetrieveProjectMemoryInput,
            retrieve_project_memory_skill,
            {"is_read_only": True},
        ),
        (
            "record_project_memory",
            "Record user feedback as persistent project preferences.",
            RecordProjectMemoryInput,
            record_project_memory_skill,
            {},
        ),
        (
            "record_execution_trace",
            "Append an execution, QA failure, or accepted output trace to project memory.",
            RecordExecutionTraceInput,
            record_execution_trace_skill,
            {},
        ),
        (
            "retrieve_failure_patterns",
            "Retrieve prior QA or feedback failure patterns relevant to a query.",
            RetrieveFailurePatternsInput,
            retrieve_failure_patterns_skill,
            {"is_read_only": True},
        ),
        (
            "digest_pdf_sources",
            "Extract a grounded digest from selected PDF sources before planning.",
            DigestPdfSourcesInput,
            digest_pdf_sources_skill,
            {"is_read_only": True},
        ),
        ("validate_plan", "Validate a plan/spec file.", ValidatePlanInput, validate_plan_skill, {"is_read_only": True}),
        ("migrate_plan", "Migrate a plan/spec file.", MigratePlanInput, migrate_plan_skill, {}),
        ("build_ppt", "Build a PPTX from the latest plan.", BuildPptInput, build_ppt_skill, {"requires_approval": True}),
        (
            "build_html_deck",
            "Build a single-file HTML deck from the latest plan.",
            BuildHtmlDeckInput,
            build_html_deck_skill,
            {"requires_approval": True},
        ),
        ("run_from_plan", "Run from an existing plan/spec file.", RunFromPlanInput, run_from_plan_skill, {"requires_approval": True}),
        ("show_current_plan", "Show summary for the latest plan.", ShowCurrentPlanInput, show_current_plan_skill, {"is_read_only": True}),
        ("revise_plan", "Regenerate the latest plan with revised intent.", RevisePlanInput, revise_plan_skill, {}),
        ("list_generated_files", "List generated plan and ppt paths.", ListGeneratedFilesInput, list_generated_files_skill, {}),
    ]
    for name, description, input_schema, handler, options in skill_specs:
        registry.register(
            SkillDefinition(
                name=name,
                description=description,
                input_schema=input_schema,
                callable=lambda _handler=handler, **kwargs: _handler(session=session, **kwargs),
                **options,
            )
        )


def scan_workspace_skill(*, session: ShellSession, max_depth: int = 3) -> dict[str, Any]:
    files = scan_workspace(session.input_dir, max_depth=max_depth)
    session.discovered_sources = [item.model_dump(mode="json") for item in files]
    return {"files": session.discovered_sources, "reply": f"Discovered {len(files)} source files in {session.input_dir}."}


def list_sources_skill(*, session: ShellSession) -> dict[str, Any]:
    return {"files": session.discovered_sources, "reply": f"Found {len(session.discovered_sources)} source files."}


def generate_plan_skill(
    *,
    session: ShellSession,
    topic: str,
    slides: int | None = None,
    min_slides: int | None = None,
    sources: list[str] | None = None,
    audience: str | None = None,
    tone: str | None = None,
    plan_path: str | None = None,
    output_format: str | None = None,
    applied_skills: list[str] | None = None,
    theme: str | None = None,
    skill_root: str | None = None,
    skill_md_path: str | None = None,
    source_digest: dict[str, Any] | None = None,
    source_context: list[dict[str, Any]] | None = None,
    project_preferences: list[dict[str, Any]] | None = None,
    failure_patterns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    used_sources = list(sources or session.selected_pdf_paths())
    if used_sources:
        session.selected_sources = list(used_sources)
        session.draft_request.selected_sources = list(used_sources)
        if len(used_sources) == 1:
            session.draft_request.requested_pdf_name = Path(used_sources[0]).name
    applied = list(applied_skills or session.draft_request.applied_skills)
    resolved_output_format = output_format or session.draft_request.output_format or "pptx"
    resolved_theme = theme or session.draft_request.theme
    if applied:
        _activate_user_skill_context(session, applied[0])
        if session.draft_request.skill_root:
            skill_root = skill_root or session.draft_request.skill_root
        if session.draft_request.skill_md_path:
            skill_md_path = skill_md_path or session.draft_request.skill_md_path
    resolved_source_digest = source_digest
    resolved_source_context = list(source_context or [])
    if used_sources and resolved_source_digest is None:
        resolved_source_digest = digest_pdf_sources_skill(session=session, sources=used_sources).get("source_digest")
    if used_sources and not resolved_source_context:
        context_result = retrieve_source_context_skill(session=session, sources=used_sources, query=topic, limit=5)
        resolved_source_context = context_result.get("contexts", [])
    if resolved_source_digest is not None and resolved_source_context:
        resolved_source_digest = {**resolved_source_digest, "retrieved_context": resolved_source_context}
    project_memory = {"preferences": project_preferences or [], "accepted_outputs": []}
    if project_preferences is None:
        project_memory = retrieve_project_memory_skill(session=session, query=topic, limit=20)
    failure_memory = {"failure_patterns": failure_patterns or []}
    if failure_patterns is None:
        failure_memory = retrieve_failure_patterns_skill(session=session, query=topic, limit=20)
    session.draft_request.merge(
        {
            "topic": topic,
            "audience": audience,
            "tone": tone,
            "slides": slides,
            "min_slides": min_slides,
            "output_format": resolved_output_format,
            "applied_skills": applied,
            "theme": resolved_theme,
            "skill_root": skill_root,
            "skill_md_path": skill_md_path,
        }
    )
    graph = create_agent_graph()
    use_assistant_planner = session.assistant_enabled and session.assistant_key_configured()
    state = AgentState(
        intent=DeckIntent(
            topic=topic,
            audience=audience or "general business audience",
            tone=tone or "clear and pragmatic",
            source_digest=resolved_source_digest,
            source_context=resolved_source_context,
            active_skill_context=session.active_skill_context,
            applied_skills=applied,
            output_format=resolved_output_format,
            project_preferences=project_memory.get("preferences", []),
            failure_patterns=failure_memory.get("failure_patterns", []),
        ),
        mode=AgentMode.PLAN,
        planner_provider=session.assistant_provider if use_assistant_planner else None,
        planner_model=session.assistant_model if use_assistant_planner else None,
    )
    result = graph.invoke(state.model_dump(mode="json"))
    spec = PptSpec.model_validate(result["spec"])
    resolved_audience = audience or spec.audience
    minimum_slides = max(slides or 0, min_slides or 0)
    spec = _normalize_spec(spec, topic=topic, audience=resolved_audience, minimum_slides=minimum_slides)
    spec = spec.model_copy(
        update={
            "output_format": resolved_output_format,
            "applied_skills": applied,
            "source_digest": resolved_source_digest,
            "skill_root": skill_root,
            "skill_md_path": skill_md_path,
            "theme": resolved_theme or spec.theme,
            "grounding_warnings": (resolved_source_digest or {}).get("warnings", []),
        }
    )
    target = Path(plan_path or (session.output_dir / "shell-plan.json"))
    write_plan_document(
        target,
        intent=state.intent,
        spec=spec,
        mode=result.get("mode", AgentMode.PLAN),
        approved=result.get("approved", False),
        transitions=result.get("transitions", []),
        metadata={
            "output_format": resolved_output_format,
            "applied_skills": applied,
            "source_digest": resolved_source_digest,
            "skill_root": skill_root,
            "skill_md_path": skill_md_path,
            "grounding_warnings": spec.grounding_warnings,
            "project_memory": {
                "preferences": project_memory.get("preferences", []),
                "failure_patterns": failure_memory.get("failure_patterns", []),
            },
        },
    )
    record_execution_trace_skill(
        session=session,
        event="plan_generated",
        trace_type="execution",
        payload={
            "plan_path": str(target),
            "topic": topic,
            "preferences_used": project_memory.get("preferences", []),
            "failure_patterns_used": failure_memory.get("failure_patterns", []),
        },
    )
    if resolved_source_digest:
        append_memory_event(
            session.cwd,
            {
                "type": "plan_generated",
                "plan_path": str(target),
                "topic": topic,
                "sources": [
                    {"source_id": item.get("source_id"), "path": item.get("path"), "name": item.get("name")}
                    for item in resolved_source_digest.get("sources", [])
                ],
                "warnings": resolved_source_digest.get("warnings", []),
            },
        )
    session.latest_plan_path = str(target)
    session.latest_plan_sources = used_sources
    session.current_request = topic
    summary = _plan_summary(
        spec,
        used_sources=used_sources,
        requested_min_slides=minimum_slides or None,
        output_format=resolved_output_format,
        applied_skills=applied,
    )
    return {
        "plan_path": str(target),
        "plan_summary": summary,
        "reply": f"Wrote plan to {target}. {summary}",
        "requested_slides": slides,
        "min_slides": min_slides,
        "sources": used_sources,
        "output_format": resolved_output_format,
        "applied_skills": applied,
        "source_digest": resolved_source_digest,
    }


def digest_pdf_sources_skill(*, session: ShellSession, sources: list[str] | None = None) -> dict[str, Any]:
    used_sources = list(sources or session.selected_pdf_paths())
    source_digest = digest_sources([Path(path) for path in used_sources], workspace=session.cwd)
    return {"source_digest": source_digest, "reply": f"Digested {len(source_digest['sources'])} PDF source(s)."}


def ingest_sources_skill(*, session: ShellSession, sources: list[str] | None = None) -> dict[str, Any]:
    used_sources = list(sources or session.selected_pdf_paths())
    result = ingest_sources([Path(path) for path in used_sources], workspace=session.cwd)
    return {
        "indexed": result["indexed"],
        "warnings": result["warnings"],
        "reply": f"Indexed {len(result['indexed'])} source(s) into {session.cwd / '.ppt-agent' / 'data' / 'sources'}.",
    }


def index_source_skill(*, session: ShellSession, source: str) -> dict[str, Any]:
    result = index_source(Path(source), workspace=session.cwd)
    return {
        "source_id": result["source_id"],
        "metadata": result["metadata"],
        "digest": result["digest"],
        "warnings": result["warnings"],
        "reply": f"Indexed source {Path(source).name} as {result['source_id']}.",
    }


def retrieve_source_context_skill(
    *,
    session: ShellSession,
    sources: list[str] | None = None,
    query: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    used_sources = list(sources or session.selected_pdf_paths())
    result = retrieve_source_context([Path(path) for path in used_sources], workspace=session.cwd, query=query, limit=limit)
    return {"contexts": result["contexts"], "warnings": result["warnings"], "reply": f"Retrieved {len(result['contexts'])} source chunk(s)."}


def retrieve_project_memory_skill(*, session: ShellSession, query: str = "", limit: int = 20) -> dict[str, Any]:
    result = retrieve_project_memory(session.cwd, query=query, limit=limit)
    return {
        **result,
        "reply": f"Retrieved {len(result['preferences'])} preference(s) and {len(result['accepted_outputs'])} accepted output trace(s).",
    }


def record_project_memory_skill(
    *,
    session: ShellSession,
    feedback: str,
    category: str | None = None,
    source: str = "user_feedback",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = record_project_memory(session.cwd, feedback=feedback, category=category, source=source, metadata=metadata)
    return {"preference": result["preference"], "path": result["path"], "reply": f"Recorded project preference: {feedback}"}


def record_execution_trace_skill(
    *,
    session: ShellSession,
    event: str,
    payload: dict[str, Any] | None = None,
    trace_type: str = "execution",
) -> dict[str, Any]:
    result = record_execution_trace(session.cwd, event=event, payload=payload, trace_type=trace_type)
    return {"path": result["path"], "reply": f"Recorded {trace_type} trace: {event}"}


def retrieve_failure_patterns_skill(*, session: ShellSession, query: str = "", limit: int = 20) -> dict[str, Any]:
    result = retrieve_failure_patterns(session.cwd, query=query, limit=limit)
    return {"failure_patterns": result["failure_patterns"], "reply": f"Retrieved {len(result['failure_patterns'])} failure pattern(s)."}


def validate_plan_skill(*, session: ShellSession, plan_path: str | None = None) -> dict[str, Any]:
    target = Path(plan_path or session.latest_plan_path or "")
    report = validate_plan_document(target)
    return {"report": report.model_dump(mode="json"), "reply": f"Validation format: {report.format}."}


def migrate_plan_skill(*, session: ShellSession, input_path: str, output_path: str) -> dict[str, Any]:
    result = migrate_plan_document(Path(input_path), Path(output_path))
    session.latest_plan_path = output_path
    return {"result": result.model_dump(mode="json"), "reply": f"Migrated plan to {output_path}."}


def build_ppt_skill(*, session: ShellSession, plan_path: str | None = None, output_path: str | None = None) -> dict[str, Any]:
    target = Path(plan_path or session.latest_plan_path or "")
    document = read_plan_document(target)
    out = Path(output_path or (session.output_dir / "shell-deck.pptx"))
    artifact = build_pptx(document.spec, out)
    session.latest_ppt_path = str(artifact.path)
    session.last_build_status = "completed"
    session.pending_action = None
    record_execution_trace_skill(
        session=session,
        event="ppt_built",
        trace_type="accepted_output",
        payload={"plan_path": str(target), "ppt_path": str(artifact.path)},
    )
    return {"ppt_path": str(artifact.path), "reply": f"Wrote PPTX to {artifact.path}."}


def build_html_deck_skill(
    *,
    session: ShellSession,
    plan_path: str | None = None,
    skill_name: str | None = None,
    output_path: str | None = None,
    theme: str | None = None,
) -> dict[str, Any]:
    target = Path(plan_path or session.latest_plan_path or "")
    document = read_plan_document(target)
    out = Path(output_path or (session.output_dir / f"{_artifact_stem(session, document.spec)}.html"))
    if session.output_dir.resolve() not in [out.resolve().parent, *out.resolve().parents]:
        raise ValueError("build_html_deck can only write inside the output directory")
    template_path = _skill_template_path(session, skill_name or (document.payload.get("applied_skills") or [None])[0])
    references = _skill_reference_paths(session, skill_name or (document.payload.get("applied_skills") or [None])[0])
    html_path = build_html_deck(document.spec, out, template_path=template_path, theme=theme or document.payload.get("theme"), references=references)
    html_errors = validate_html_deck(
        html_path.read_text(encoding="utf-8"),
        expected_slides=len(document.spec.slides),
        requested_min_slides=session.draft_request.min_slides,
    )
    if html_errors:
        session.last_build_status = "failed"
        record_execution_trace_skill(
            session=session,
            event="html_deck_qa_failed",
            trace_type="qa_failure",
            payload={"plan_path": str(target), "html_path": str(html_path), "warnings": html_errors},
        )
        return {
            "ok": False,
            "html_path": str(html_path),
            "warnings": html_errors,
            "reply": "HTML deck QA failed: " + "; ".join(html_errors),
        }
    session.latest_html_path = str(html_path)
    session.latest_ppt_path = str(html_path)
    session.last_build_status = "completed"
    session.pending_action = None
    record_execution_trace_skill(
        session=session,
        event="html_deck_built",
        trace_type="accepted_output",
        payload={"plan_path": str(target), "html_path": str(html_path)},
    )
    return {"html_path": str(html_path), "reply": f"Wrote HTML deck to {html_path}."}


def run_from_plan_skill(*, session: ShellSession, plan_path: str | None = None, output_path: str | None = None) -> dict[str, Any]:
    return build_ppt_skill(session=session, plan_path=plan_path, output_path=output_path)


def show_current_plan_skill(*, session: ShellSession) -> dict[str, Any]:
    if not session.latest_plan_path:
        return {"reply": "No current plan is available."}
    document = read_plan_document(Path(session.latest_plan_path))
    return {
        "reply": _plan_summary(document.spec, used_sources=session.latest_plan_sources),
        "plan_path": session.latest_plan_path,
        "sources": session.latest_plan_sources,
    }


def revise_plan_skill(*, session: ShellSession, revision: str) -> dict[str, Any]:
    topic = revision if not session.current_request else f"{session.current_request}; revision: {revision}"
    return generate_plan_skill(session=session, topic=topic)


def list_generated_files_skill(*, session: ShellSession) -> dict[str, Any]:
    return {
        "plan_path": session.latest_plan_path,
        "ppt_path": session.latest_ppt_path,
        "reply": f"Latest plan: {session.latest_plan_path or 'none'}, latest ppt: {session.latest_ppt_path or 'none'}.",
    }


def _plan_summary(
    spec: PptSpec,
    *,
    used_sources: list[str] | None = None,
    requested_min_slides: int | None = None,
    output_format: str | None = None,
    applied_skills: list[str] | None = None,
) -> str:
    names = [Path(path).name for path in (used_sources or [])]
    if not names:
        source_summary = "Source PDFs: none selected."
    else:
        source_summary = f"Source PDFs: {', '.join(names)}."
    minimum_summary = ""
    if requested_min_slides:
        minimum_summary = f" Requested minimum slides: {requested_min_slides}."
    skill_summary = f" Applied skill: {', '.join(applied_skills)}." if applied_skills else ""
    format_summary = f" Output format: {output_format}." if output_format else ""
    return (
        f"Plan '{spec.title}' for {spec.audience} with {len(spec.slides)} slides."
        f" Topic: {spec.title}. Audience: {spec.audience}. Slides: {len(spec.slides)}."
        f"{minimum_summary}{skill_summary}{format_summary} {source_summary}"
    )


def _normalize_spec(spec: PptSpec, *, topic: str, audience: str, minimum_slides: int) -> PptSpec:
    normalized = spec.model_copy(update={"audience": audience})
    if minimum_slides <= len(normalized.slides):
        return normalized

    slides = list(normalized.slides)
    for index in range(len(slides) + 1, minimum_slides + 1):
        slides.append(_make_appendix_slide(topic=topic, audience=audience, index=index - len(normalized.slides)))
    return normalized.model_copy(update={"slides": slides})


def _make_appendix_slide(*, topic: str, audience: str, index: int) -> SlideSpec:
    is_research = any(token in audience.lower() for token in ("research", "graduate", "student")) or "\u7814\u7a76\u751f" in audience
    if is_research:
        title = f"\u7814\u7a76\u8865\u5145 {index}"
        objective = "\u8865\u5145\u7814\u7a76\u89c6\u89d2\u4e0b\u7684\u80cc\u666f\u3001\u5047\u8bbe\u548c\u53ef\u9a8c\u8bc1\u89c2\u70b9\u3002"
        core_message = f"{topic}\u9700\u8981\u7528\u66f4\u7cfb\u7edf\u7684\u8bba\u8bc1\u548c\u8865\u5145\u6750\u6599\u6765\u652f\u6491\u7814\u7a76\u751f\u53d7\u4f17\u7684\u9605\u8bfb\u9884\u671f\u3002"
        bullets = [
            "\u8865\u5145\u7406\u8bba\u80cc\u666f\u3001\u7814\u7a76\u52a8\u673a\u6216\u95ee\u9898\u754c\u5b9a\u3002",
            "\u5c55\u5f00\u6837\u672c\u3001\u573a\u666f\u6216\u5bf9\u6bd4\u8bbe\u7f6e\u7684\u7ec6\u8282\u3002",
            "\u7ed9\u51fa\u540e\u7eed\u53ef\u9a8c\u8bc1\u7684\u95ee\u9898\u6216\u7814\u7a76\u8def\u5f84\u3002",
        ]
        supporting_points = [
            "\u5b66\u672f\u53d7\u4f17\u66f4\u5173\u6ce8\u8bba\u8bc1\u7684\u5b8c\u6574\u6027\u3002",
            "\u9644\u5f55\u9875\u53ef\u4ee5\u6269\u5c55\u7ec6\u8282\u800c\u4e0d\u538b\u7f29\u4e3b\u7ebf\u53d9\u4e8b\u3002",
        ]
        style_tags = ["research", "appendix"]
    else:
        title = f"Supporting Appendix {index}"
        objective = "Provide supporting detail without breaking the main decision flow."
        core_message = f"This appendix adds supporting context for {topic} and keeps the main deck aligned to the requested scope."
        bullets = [
            "Capture supporting assumptions, examples, or edge cases behind the recommendation.",
            "Document delivery considerations, dependencies, or implementation notes.",
            "Extend the deck with reference material that can be used during discussion.",
        ]
        supporting_points = [
            "Appendix slides preserve executive flow while adding depth.",
            "The generated plan now meets the requested minimum slide count.",
        ]
        style_tags = ["appendix", "supporting-detail"]

    return SlideSpec(
        title=title,
        objective=objective,
        core_message=core_message,
        bullets=bullets,
        supporting_points=supporting_points,
        visual_type="three_card_summary",
        layout_hint="three_card_summary",
        style_tags=style_tags,
    )


def _digest_one_pdf(path: Path) -> dict[str, Any]:
    text, warnings = _extract_pdf_text(path)
    cleaned = " ".join(text.split())
    digest = {
        "path": str(path),
        "name": path.name,
        "title": _guess_title(cleaned, path),
        "abstract": _section_text(cleaned, "abstract"),
        "authors": "unknown",
        "problem": _keyword_window(cleaned, ("problem", "challenge", "问题")),
        "motivation": _keyword_window(cleaned, ("motivation", "背景", "动机")),
        "method": _keyword_window(cleaned, ("method", "approach", "方法")),
        "system": _keyword_window(cleaned, ("system", "architecture", "系统")),
        "algorithm": _keyword_window(cleaned, ("algorithm", "算法")),
        "experiments": _keyword_window(cleaned, ("experiment", "evaluation", "实验")),
        "datasets": _keyword_window(cleaned, ("dataset", "data set", "数据集")),
        "metrics": _keyword_window(cleaned, ("metric", "指标")),
        "results": _keyword_window(cleaned, ("result", "结果")),
        "limitations": _keyword_window(cleaned, ("limitation", "限制", "threat")),
        "figures_tables": _keyword_window(cleaned, ("figure", "table", "图", "表")),
        "warnings": warnings,
    }
    for key, value in list(digest.items()):
        if key not in {"path", "name", "warnings"} and not value:
            digest[key] = "unknown"
    return digest


def _extract_pdf_text(path: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text, warnings
        warnings.append(f"{path.name}: no extractable PDF text")
    except Exception as exc:
        warnings.append(f"{path.name}: PDF text extraction failed: {exc}")
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), warnings
    except OSError as exc:
        warnings.append(f"{path.name}: fallback text read failed: {exc}")
        return "", warnings


def _guess_title(text: str, path: Path) -> str:
    for line in text.split(". ")[:3]:
        candidate = line.strip()
        if 8 <= len(candidate) <= 160:
            return candidate
    return path.stem


def _section_text(text: str, section: str) -> str:
    lower = text.lower()
    index = lower.find(section)
    if index < 0:
        return ""
    return text[index : index + 700].strip()


def _keyword_window(text: str, keywords: tuple[str, ...]) -> str:
    lower = text.lower()
    for keyword in keywords:
        index = lower.find(keyword.lower())
        if index >= 0:
            return text[index : index + 500].strip()
    return ""


def _activate_user_skill_context(session: ShellSession, skill_name: str) -> None:
    if skill_name not in session.enabled_user_skills:
        return
    record = next((item for item in session.user_skill_records if item.get("name") == skill_name), None)
    if not record:
        return
    skill_md_path = record.get("skill_md_path")
    if not skill_md_path:
        return
    path = Path(skill_md_path)
    try:
        session.active_skill_context = path.read_text(encoding="utf-8")
    except OSError:
        return
    session.active_skill_name = skill_name
    session.draft_request.skill_root = record.get("skill_root")
    session.draft_request.skill_md_path = str(path)


def _skill_template_path(session: ShellSession, skill_name: str | None) -> Path | None:
    record = _skill_record(session, skill_name)
    if not record:
        return None
    root = record.get("skill_root")
    if not root:
        return None
    candidate = Path(root) / "assets" / "template.html"
    return candidate if candidate.exists() else None


def _skill_reference_paths(session: ShellSession, skill_name: str | None) -> list[Path]:
    record = _skill_record(session, skill_name)
    root = Path(record["skill_root"]) if record and record.get("skill_root") else None
    if not root:
        return []
    references = root / "references"
    if not references.exists():
        return []
    return sorted(references.glob("*.md"))


def _skill_record(session: ShellSession, skill_name: str | None) -> dict | None:
    if not skill_name:
        return None
    return next((item for item in session.user_skill_records if item.get("name") == skill_name), None)


def _artifact_stem(session: ShellSession, spec: PptSpec) -> str:
    selected = session.selected_pdf_names()
    if len(selected) == 1:
        return Path(selected[0]).stem
    return spec.title.replace(" ", "-")[:40] or "shell-deck"
