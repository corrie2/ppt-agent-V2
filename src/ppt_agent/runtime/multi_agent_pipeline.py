from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import httpx
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from ppt_agent.domain.models import DeckIntent, PptSpec, SlideContent, SlideSpec
from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.runtime.agent_llm import AgentLlmConfig, generate_agent_json, load_agent_llm_config
from ppt_agent.runtime.agent_skills import AgentSkillAssignments, assign_skills_to_agents, skill_context_for_agent
from ppt_agent.runtime.renderer_engineer import (
    renderer_engineer_agent_with_llm,
    validate_renderer_engineer_report,
    write_renderer_scripts,
)

DEFAULT_PAGE_COUNT = 15
MAX_VISIBLE_BULLETS = 3


@dataclass(frozen=True)
class PipelineResult:
    spec: PptSpec
    task_dir: Path
    artifacts: dict[str, str]


class PipelineStageStatus(BaseModel):
    name: str
    agent: str
    status: str = "pending"
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class PipelineQaIssue(BaseModel):
    id: str
    severity: str
    agent: str
    slide_no: int | None = None
    message: str
    suggested_fix: str | None = None


class PipelineQaReport(BaseModel):
    ok: bool
    issues: list[PipelineQaIssue] = Field(default_factory=list)


class EvaluationIssue(BaseModel):
    severity: str = "warning"
    agent: str | None = None
    message: str
    suggested_fix: str | None = None


class StageEvaluation(BaseModel):
    stage: str
    target_agents: list[str] = Field(default_factory=list)
    score: float = 1.0
    severity: str = "pass"
    findings: list[str] = Field(default_factory=list)
    issues: list[EvaluationIssue] = Field(default_factory=list)
    requires_rework: bool = False
    rework_target: str | None = None


class EvaluationReport(BaseModel):
    ok: bool = True
    requires_rework: bool = False
    evaluations: list[StageEvaluation] = Field(default_factory=list)


class BriefArtifact(BaseModel):
    agent: str
    topic: str
    audience: str
    purpose: str
    language: str
    page_count: int
    tone: str
    style_keywords: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class OutlineSlideArtifact(BaseModel):
    slide_no: int
    type: str
    title: str
    goal: str


class OutlineArtifact(BaseModel):
    agent: str
    slides: list[OutlineSlideArtifact]


class ContentSlideArtifact(BaseModel):
    slide_no: int
    title: str
    message: str
    bullets: list[str] = Field(default_factory=list)
    speaker_notes: str = ""


class ContentArtifact(BaseModel):
    agent: str
    slides: list[ContentSlideArtifact]


class ChartArtifact(BaseModel):
    slide_no: int
    type: str
    title: str
    data: list[dict] = Field(default_factory=list)
    unit: str = ""


class DesignChartArtifact(BaseModel):
    agent: str
    theme: dict
    charts: list[ChartArtifact] = Field(default_factory=list)


class SlideIrArtifact(BaseModel):
    slide_no: int
    type: str
    layout: str
    visual_type: str
    title: str
    message: str
    bullets: list[str] = Field(default_factory=list)
    notes: str = ""
    chart: dict | None = None


class SlidesIrArtifact(BaseModel):
    deck: dict
    theme: dict
    slides: list[SlideIrArtifact]
    task_dir: str | None = None
    supervisor_repairs: list[dict] = Field(default_factory=list)


class PageDesignSlideArtifact(BaseModel):
    slide_no: int
    layout: str
    visual_priority: str = "balanced"
    hierarchy: list[str] = Field(default_factory=list)
    figure_strategy: str = "none"
    density: str = "standard"
    speaker_focus: str = ""
    renderer_notes: str = ""


class PageDesignArtifact(BaseModel):
    agent: str = "page_designer"
    design_system: dict = Field(default_factory=dict)
    slides: list[PageDesignSlideArtifact]


def build_multi_agent_plan_spec(
    intent: DeckIntent,
    *,
    task_root: Path | None = None,
    llm_config: AgentLlmConfig | None = None,
) -> PipelineResult:
    """Run the first version of the PPT multi-agent production pipeline.

    The workers are deterministic in this MVP, but each stage writes its own
    structured artifact so the orchestration contract is already in place.
    """

    root = task_root or Path.cwd() / ".ppt-agent" / "tasks"
    task_dir = root / _task_id(intent.topic)
    intermediate_dir = task_dir / "intermediate"
    build_dir = task_dir / "build"
    input_dir = task_dir / "input"
    for directory in (input_dir, intermediate_dir, build_dir):
        directory.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "user_request": str(input_dir / "user_request.json"),
        "task_plan": str(task_dir / "task_plan.json"),
        "brief": str(intermediate_dir / "brief.json"),
        "outline": str(intermediate_dir / "outline.json"),
        "content": str(intermediate_dir / "content.json"),
        "design_chart": str(intermediate_dir / "design_chart.json"),
        "review_report": str(build_dir / "review_report.json"),
        "evaluation_report": str(build_dir / "evaluation_report.json"),
        "render_review_report": str(build_dir / "render_review_report.json"),
        "slides_ir": str(build_dir / "slides_ir.json"),
        "page_design": str(build_dir / "page_design.json"),
        "renderer_engineer_report": str(build_dir / "renderer_engineer_report.json"),
        "renderer_scripts": str(build_dir / "renderer_scripts"),
    }
    resolved_llm_config = llm_config or load_agent_llm_config()
    skill_assignments = assign_skills_to_agents(Path.cwd())
    stages = _initial_task_plan(artifacts)

    graph = create_multi_agent_graph()
    final_state = graph.invoke(
        {
            "intent": intent,
            "task_dir": str(task_dir),
            "artifacts": artifacts,
            "llm_config": resolved_llm_config,
            "skill_assignments": skill_assignments,
            "transitions": [],
        }
    )

    user_request = final_state["user_request"]
    brief = final_state["brief"]
    outline = final_state["outline"]
    content = final_state["content"]
    design_chart = final_state["design_chart"]
    slides_ir = final_state["slides_ir"]
    page_design = final_state["page_design"]
    renderer_engineer_report = final_state["renderer_engineer_report"]
    review_report = PipelineQaReport.model_validate(final_state["review_report"])
    spec = PptSpec.model_validate(final_state["spec"])
    render_review_report = PipelineQaReport.model_validate(final_state["render_review_report"])
    evaluation_report = EvaluationReport.model_validate(final_state["evaluation_report"])

    _write_stage_output(stages, "user_request", artifacts["user_request"], user_request)
    _write_stage_output(stages, "brief", artifacts["brief"], brief, _validate_brief(brief))
    _write_stage_output(stages, "brief_outline", artifacts["outline"], outline, _validate_outline(outline, brief))
    _write_stage_output(stages, "content", artifacts["content"], content, _validate_content(content, outline))
    _write_stage_output(stages, "design_chart", artifacts["design_chart"], design_chart, _validate_design_chart(design_chart, outline))
    _write_stage_output(stages, "supervisor", artifacts["slides_ir"], slides_ir, _validate_slides_ir(slides_ir, brief))
    _write_stage_output(stages, "page_designer", artifacts["page_design"], page_design, _validate_page_design(page_design, slides_ir))
    _write_stage_output(
        stages,
        "renderer_engineer",
        artifacts["renderer_engineer_report"],
        renderer_engineer_report,
        validate_renderer_engineer_report(renderer_engineer_report, page_design),
    )
    write_renderer_scripts(renderer_engineer_report, Path(artifacts["renderer_scripts"]))
    _write_stage_output(
        stages,
        "qa",
        artifacts["review_report"],
        review_report.model_dump(mode="json"),
        [issue.message for issue in review_report.issues],
    )
    _write_stage_output(
        stages,
        "render_review",
        artifacts["render_review_report"],
        render_review_report.model_dump(mode="json"),
        [issue.message for issue in render_review_report.issues],
    )
    _write_stage_output(
        stages,
        "evaluator",
        artifacts["evaluation_report"],
        evaluation_report.model_dump(mode="json"),
        [issue.message for evaluation in evaluation_report.evaluations for issue in evaluation.issues],
    )
    _write_json(Path(artifacts["task_plan"]), _task_plan_payload(intent, stages, artifacts, skill_assignments))

    return PipelineResult(spec=spec, task_dir=task_dir, artifacts=artifacts)


def create_multi_agent_graph():
    graph = StateGraph(dict)
    graph.add_node("supervisor_start", supervisor_start_node)
    graph.add_node("brief_outline", brief_outline_node)
    graph.add_node("brief_outline_eval", brief_outline_eval_node)
    graph.add_node("content", content_node)
    graph.add_node("content_rework", content_node)
    graph.add_node("content_eval", content_eval_node)
    graph.add_node("design_chart", design_chart_node)
    graph.add_node("design_chart_rework", design_chart_node)
    graph.add_node("design_chart_eval", design_chart_eval_node)
    graph.add_node("supervisor_merge", supervisor_merge_node)
    graph.add_node("slides_ir_eval", slides_ir_eval_node)
    graph.add_node("qa", qa_node)
    graph.add_node("supervisor_repair", supervisor_repair_node)
    graph.add_node("page_designer", page_designer_node)
    graph.add_node("renderer_engineer", renderer_engineer_node)
    graph.add_node("page_generator", page_generator_node)
    graph.add_node("render_review", render_review_node)
    graph.add_node("final_eval", final_eval_node)

    graph.set_entry_point("supervisor_start")
    graph.add_edge("supervisor_start", "brief_outline")
    graph.add_edge("brief_outline", "brief_outline_eval")
    graph.add_conditional_edges(
        "brief_outline_eval",
        _after_brief_outline_eval,
        {"brief_outline": "brief_outline", "content": "content", "design_chart": "design_chart"},
    )
    graph.add_edge(["content", "design_chart"], "content_eval")
    graph.add_edge("content_rework", "content_eval")
    graph.add_conditional_edges(
        "content_eval",
        _after_content_eval,
        {"content_rework": "content_rework", "design_chart_eval": "design_chart_eval"},
    )
    graph.add_edge("design_chart_rework", "design_chart_eval")
    graph.add_conditional_edges(
        "design_chart_eval",
        _after_design_chart_eval,
        {"design_chart_rework": "design_chart_rework", "supervisor_merge": "supervisor_merge"},
    )
    graph.add_edge("supervisor_merge", "slides_ir_eval")
    graph.add_conditional_edges(
        "slides_ir_eval",
        _after_slides_ir_eval,
        {"supervisor_merge": "supervisor_merge", "qa": "qa"},
    )
    graph.add_conditional_edges(
        "qa",
        _after_multi_agent_qa,
        {"supervisor_repair": "supervisor_repair", "page_designer": "page_designer"},
    )
    graph.add_edge("supervisor_repair", "page_designer")
    graph.add_edge("page_designer", "renderer_engineer")
    graph.add_edge("renderer_engineer", "page_generator")
    graph.add_edge("page_generator", "render_review")
    graph.add_edge("render_review", "final_eval")
    graph.add_conditional_edges(
        "final_eval",
        _after_final_eval,
        {"renderer_engineer": "renderer_engineer", "page_generator": "page_generator", "render_review": "render_review", END: END},
    )
    return graph.compile()


def supervisor_start_node(state: dict[str, Any]) -> dict[str, Any]:
    intent = DeckIntent.model_validate(state["intent"])
    user_request = _user_request(intent)
    return {"user_request": user_request, "transitions": [*state.get("transitions", []), "supervisor_start"]}


def brief_outline_node(state: dict[str, Any]) -> dict[str, Any]:
    user_request = state["user_request"]
    config = state["llm_config"]
    skill_assignments = state["skill_assignments"]
    brief = _llm_or_fallback(
        "brief_outline",
        BriefArtifact,
        system_prompt=_brief_prompt(),
        user_payload={
            "user_request": user_request,
            "assigned_skills": skill_context_for_agent(skill_assignments, "brief_outline"),
        },
        fallback=lambda: _brief_agent(user_request),
        config=config,
    )
    outline = _llm_or_fallback(
        "brief_outline",
        OutlineArtifact,
        system_prompt=_outline_prompt(),
        user_payload={
            "brief": brief,
            "assigned_skills": skill_context_for_agent(skill_assignments, "brief_outline"),
        },
        fallback=lambda: _brief_outline_agent(brief),
        config=config,
    )
    return {"brief": brief, "outline": outline, "transitions": [*state.get("transitions", []), "brief_outline"]}


def brief_outline_eval_node(state: dict[str, Any]) -> dict[str, Any]:
    evaluation = _evaluate_stage_with_llm(
        state,
        stage="brief_outline_eval",
        target_agents=["brief_outline"],
        payload={"brief": state["brief"], "outline": state["outline"]},
        fallback=lambda: _evaluate_brief_outline(state["brief"], state["outline"]),
    )
    return _append_evaluation_update(state, evaluation)


def content_node(state: dict[str, Any]) -> dict[str, Any]:
    brief = state["brief"]
    outline = state["outline"]
    config = state["llm_config"]
    skill_assignments = state["skill_assignments"]
    content = _llm_or_fallback(
        "content",
        ContentArtifact,
        system_prompt=_content_prompt(),
        user_payload={
            "brief": brief,
            "outline": outline,
            "assigned_skills": skill_context_for_agent(skill_assignments, "content"),
        },
        fallback=lambda: _content_agent(brief, outline),
        config=config,
    )
    return {"content": content}


def design_chart_node(state: dict[str, Any]) -> dict[str, Any]:
    brief = state["brief"]
    outline = state["outline"]
    config = state["llm_config"]
    skill_assignments = state["skill_assignments"]
    design_chart = _llm_or_fallback(
        "design_chart",
        DesignChartArtifact,
        system_prompt=_design_chart_prompt(),
        user_payload={
            "brief": brief,
            "outline": outline,
            "assigned_skills": skill_context_for_agent(skill_assignments, "design_chart"),
        },
        fallback=lambda: _design_chart_agent(brief, outline),
        config=config,
    )
    return {"design_chart": design_chart}


def content_eval_node(state: dict[str, Any]) -> dict[str, Any]:
    evaluation = _evaluate_stage_with_llm(
        state,
        stage="content_eval",
        target_agents=["content"],
        payload={"brief": state["brief"], "outline": state["outline"], "content": state["content"]},
        fallback=lambda: _evaluate_content(state["content"], state["outline"]),
    )
    return _append_evaluation_update(state, evaluation)


def design_chart_eval_node(state: dict[str, Any]) -> dict[str, Any]:
    evaluation = _evaluate_stage_with_llm(
        state,
        stage="design_chart_eval",
        target_agents=["design_chart"],
        payload={"brief": state["brief"], "outline": state["outline"], "design_chart": state["design_chart"]},
        fallback=lambda: _evaluate_design_chart(state["design_chart"], state["outline"]),
    )
    return _append_evaluation_update(state, evaluation)


def supervisor_merge_node(state: dict[str, Any]) -> dict[str, Any]:
    brief = state["brief"]
    outline = state["outline"]
    content = state["content"]
    design_chart = state["design_chart"]
    config = state["llm_config"]
    skill_assignments = state["skill_assignments"]
    slides_ir = _llm_or_fallback(
        "supervisor",
        SlidesIrArtifact,
        system_prompt=_supervisor_prompt(),
        user_payload={
            "brief": brief,
            "outline": outline,
            "content": content,
            "design_chart": design_chart,
            "all_skills": skill_context_for_agent(skill_assignments, "supervisor"),
            "skill_assignments": skill_assignments.names_by_agent(),
        },
        fallback=lambda: _supervisor_agent(brief, outline, content, design_chart),
        config=config,
    )
    slides_ir["task_dir"] = state["task_dir"]
    return {"slides_ir": slides_ir, "transitions": [*state.get("transitions", []), "supervisor_merge"]}


def slides_ir_eval_node(state: dict[str, Any]) -> dict[str, Any]:
    evaluation = _evaluate_stage_with_llm(
        state,
        stage="slides_ir_eval",
        target_agents=["supervisor"],
        payload={"brief": state["brief"], "outline": state["outline"], "content": state["content"], "design_chart": state["design_chart"], "slides_ir": state["slides_ir"]},
        fallback=lambda: _evaluate_slides_ir(state["slides_ir"], state["brief"]),
    )
    return _append_evaluation_update(state, evaluation)


def qa_node(state: dict[str, Any]) -> dict[str, Any]:
    report = _qa_agent_with_llm(state["slides_ir"], state["llm_config"], state["skill_assignments"])
    return {"review_report": report.model_dump(mode="json"), "transitions": [*state.get("transitions", []), "qa"]}


def supervisor_repair_node(state: dict[str, Any]) -> dict[str, Any]:
    report = PipelineQaReport.model_validate(state["review_report"])
    slides_ir = _supervisor_repair_agent_with_llm(
        state["slides_ir"],
        report,
        state["llm_config"],
        state["skill_assignments"],
    )
    slides_ir["task_dir"] = state["task_dir"]
    return {"slides_ir": slides_ir, "transitions": [*state.get("transitions", []), "supervisor_repair"]}


def page_designer_node(state: dict[str, Any]) -> dict[str, Any]:
    page_design = _page_designer_agent_with_llm(
        state["slides_ir"],
        state["llm_config"],
        state["skill_assignments"],
    )
    return {"page_design": page_design, "transitions": [*state.get("transitions", []), "page_designer"]}


def renderer_engineer_node(state: dict[str, Any]) -> dict[str, Any]:
    report = renderer_engineer_agent_with_llm(
        state["slides_ir"],
        state["page_design"],
        state["llm_config"],
        state["skill_assignments"],
    )
    return {"renderer_engineer_report": report, "transitions": [*state.get("transitions", []), "renderer_engineer"]}


def page_generator_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = _page_generator_agent(
        state["slides_ir"],
        DeckIntent.model_validate(state["intent"]),
        page_design=state.get("page_design"),
    )
    return {"spec": spec.model_dump(mode="json"), "transitions": [*state.get("transitions", []), "page_generator"]}


def render_review_node(state: dict[str, Any]) -> dict[str, Any]:
    report = _render_review_agent_with_llm(
        state["slides_ir"],
        state.get("page_design"),
        state.get("renderer_engineer_report"),
        PptSpec.model_validate(state["spec"]),
        state["llm_config"],
        state["skill_assignments"],
    )
    return {"render_review_report": report.model_dump(mode="json"), "transitions": [*state.get("transitions", []), "render_review"]}


def final_eval_node(state: dict[str, Any]) -> dict[str, Any]:
    evaluation = _evaluate_stage_with_llm(
        state,
        stage="final_eval",
        target_agents=["qa", "renderer_engineer", "page_generator", "render_review"],
        payload={
            "slides_ir": state["slides_ir"],
            "page_design": state.get("page_design"),
            "renderer_engineer_report": state.get("renderer_engineer_report"),
            "review_report": state["review_report"],
            "ppt_spec_summary": _ppt_spec_summary(PptSpec.model_validate(state["spec"])),
            "render_review_report": state["render_review_report"],
        },
        fallback=lambda: _evaluate_final(state),
    )
    return _append_evaluation_update(state, evaluation)


def _after_multi_agent_qa(state: dict[str, Any]) -> str:
    report = PipelineQaReport.model_validate(state["review_report"])
    return "page_designer" if report.ok else "supervisor_repair"


def _after_brief_outline_eval(state: dict[str, Any]) -> str | list[str]:
    return "brief_outline" if _should_rework(state, "brief_outline_eval") else ["content", "design_chart"]


def _after_content_eval(state: dict[str, Any]) -> str:
    return "content_rework" if _should_rework(state, "content_eval") else "design_chart_eval"


def _after_design_chart_eval(state: dict[str, Any]) -> str:
    return "design_chart_rework" if _should_rework(state, "design_chart_eval") else "supervisor_merge"


def _after_slides_ir_eval(state: dict[str, Any]) -> str:
    return "supervisor_merge" if _should_rework(state, "slides_ir_eval") else "qa"


def _after_final_eval(state: dict[str, Any]) -> str:
    if not _should_rework(state, "final_eval"):
        return END
    target = _latest_evaluation(state).rework_target
    if target == "renderer_engineer":
        return "renderer_engineer"
    return "render_review" if target == "render_review" else "page_generator"


def _evaluate_stage_with_llm(
    state: dict[str, Any],
    *,
    stage: str,
    target_agents: list[str],
    payload: dict,
    fallback: Callable[[], StageEvaluation],
) -> StageEvaluation:
    deterministic = fallback()
    if deterministic.severity == "pass" and not deterministic.requires_rework:
        return deterministic
    llm_payload = _llm_or_none(
        "evaluator",
        StageEvaluation,
        system_prompt=_evaluator_prompt(stage=stage, target_agents=target_agents),
        user_payload={
            **payload,
            "stage": stage,
            "target_agents": target_agents,
            "all_skills": skill_context_for_agent(state["skill_assignments"], "supervisor"),
            "skill_assignments": state["skill_assignments"].names_by_agent(),
            "rework_policy": _rework_policy(),
            "rule_evaluation": deterministic.model_dump(mode="json"),
        },
        config=state["llm_config"],
    )
    if llm_payload is not None:
        return StageEvaluation.model_validate(llm_payload)
    return deterministic


def _append_evaluation_update(state: dict[str, Any], evaluation: StageEvaluation) -> dict[str, Any]:
    report = EvaluationReport.model_validate(state.get("evaluation_report") or {})
    evaluations = [*report.evaluations, evaluation]
    requires_rework = any(_evaluation_requires_rework(item) for item in evaluations)
    counts = dict(state.get("rework_counts") or {})
    rework_decisions = dict(state.get("rework_decisions") or {})
    allowed_rework = _evaluation_requires_rework(evaluation) and int(counts.get(evaluation.stage, 0)) < 1
    rework_decisions[evaluation.stage] = allowed_rework
    if allowed_rework:
        counts[evaluation.stage] = int(counts.get(evaluation.stage, 0)) + 1
    updated_report = EvaluationReport(
        ok=not any(item.severity == "error" for item in evaluations),
        requires_rework=requires_rework,
        evaluations=evaluations,
    )
    return {
        "evaluation_report": updated_report.model_dump(mode="json"),
        "rework_counts": counts,
        "rework_decisions": rework_decisions,
        "transitions": [*state.get("transitions", []), evaluation.stage],
    }


def _should_rework(state: dict[str, Any], stage: str) -> bool:
    evaluation = _latest_evaluation(state)
    if evaluation.stage != stage or not _evaluation_requires_rework(evaluation):
        return False
    decisions = dict(state.get("rework_decisions") or {})
    return bool(decisions.get(stage, False))


def _latest_evaluation(state: dict[str, Any]) -> StageEvaluation:
    report = EvaluationReport.model_validate(state.get("evaluation_report") or {})
    if not report.evaluations:
        return StageEvaluation(stage="none")
    return report.evaluations[-1]


def _evaluation_requires_rework(evaluation: StageEvaluation) -> bool:
    return (
        evaluation.requires_rework
        or evaluation.severity == "error"
        or evaluation.score < _rework_policy()["block_score_below"]
        or any(issue.severity == "error" for issue in evaluation.issues)
    )


def _rework_policy() -> dict:
    return {"max_rework_per_stage": 1, "block_on_error": True, "block_score_below": 0.75}


def _user_request(intent: DeckIntent) -> dict:
    return {
        "topic": intent.topic.strip(),
        "audience": intent.audience,
        "tone": intent.tone,
        "language": "zh-CN",
        "page_count": DEFAULT_PAGE_COUNT,
        "output_path": intent.output_path,
        "source_digest": intent.source_digest,
        "source_context": intent.source_context,
        "applied_skills": intent.applied_skills,
        "project_preferences": intent.project_preferences,
        "failure_patterns": intent.failure_patterns,
    }


def _brief_prompt() -> str:
    return (
        "Use deepseek-v4-flash role behavior unless configured otherwise. "
        "Create the brief artifact. Required JSON keys: agent, topic, audience, purpose, "
        "language, page_count, tone, style_keywords, success_criteria. "
        f"Set page_count to {DEFAULT_PAGE_COUNT} unless the input explicitly overrides it. "
        "Do not create slides here."
    )


def _outline_prompt() -> str:
    return (
        "Use deepseek-v4-flash role behavior unless configured otherwise. "
        "Create an outline artifact with exactly brief.page_count slides. "
        "Required JSON: {agent: 'brief_outline', slides: [{slide_no, type, title, goal}]}. "
        "Slide numbers must start at 1 and be continuous. Keep titles concise and presentation-ready."
    )


def _content_prompt() -> str:
    return (
        "Use deepseek-v4-flash role behavior unless configured otherwise. "
        "Create the content artifact only. Required JSON: {agent: 'content', slides: "
        "[{slide_no, title, message, bullets, speaker_notes}]}. "
        f"Each slide must have one message and at most {MAX_VISIBLE_BULLETS} bullets. "
        "Do not change slide numbers or add/remove slides."
    )


def _design_chart_prompt() -> str:
    return (
        "Use deepseek-v4-flash role behavior unless configured otherwise. "
        "Create the design_chart artifact only. Required JSON keys: agent, theme, charts. "
        "theme must include name, colors, font, and layout_rules. "
        "charts may reference only existing slide_no values from outline. "
        "Do not write slide content."
    )


def _supervisor_prompt() -> str:
    return (
        "You are the decision-making Supervisor Agent and should use deepseek-v4-pro when available. "
        "Merge worker artifacts into slides_ir. Required JSON keys: deck, theme, slides. "
        "deck must include title, audience, language, size, page_count. "
        "Each slide must include slide_no, type, layout, visual_type, title, message, bullets, notes. "
        "Use worker outputs, resolve conflicts, keep slide numbers continuous, and keep bullets to at most 3. "
        "Do not output PptSpec; only output slides_ir."
    )


def _qa_prompt() -> str:
    return (
        "Use deepseek-v4-flash role behavior unless configured otherwise. "
        "Review slides_ir for structural and presentation quality issues. "
        "Return JSON matching {ok: boolean, issues: [{id, severity, agent, slide_no, message, suggested_fix}]}. "
        "Use severity 'error' only for issues that block page generation; use 'warning' for quality improvements."
    )


def _page_designer_prompt() -> str:
    return (
        "You are the Page Designer Agent and should use deepseek-v4-flash when available. "
        "Make page-level design decisions only; do not rewrite slide titles, messages, or bullets. "
        "Return JSON matching {agent, design_system, slides}. "
        "Each slide item must include slide_no, layout, visual_priority, hierarchy, figure_strategy, density, "
        "speaker_focus, renderer_notes. "
        "Choose layouts that a deterministic renderer can implement: hero, title-bullets, two_column_text_image, "
        "figure_caption, two_column_figure, comparison_table, process_timeline, three_card_summary, big_quote. "
        "Use figure_caption/two_column_figure when a slide has or should emphasize figures. "
        "Optimize for graduate seminar readability: clear hierarchy, low text density, strong figure treatment."
    )


def _render_review_prompt() -> str:
    return (
        "You are the Render Review Agent and should use deepseek-v4-flash when available. "
        "Review the Page Generator mapping from slides_ir to PptSpec. "
        "Do not rewrite content, do not change slides_ir, do not produce PPTX. "
        "Return JSON matching {ok: boolean, issues: [{id, severity, agent, slide_no, message, suggested_fix}]}. "
        "Focus on lost titles, lost messages, lost bullets, unsupported or missing layouts, missing notes, "
        "chart/metric mapping gaps, and risks that would cause blank or incomplete slides."
    )


def _evaluator_prompt(*, stage: str, target_agents: list[str]) -> str:
    return (
        "You are the Evaluator Agent and should use deepseek-v4-flash when available. "
        "Evaluate only; do not rewrite artifacts and do not generate slide content. "
        f"Current evaluation stage: {stage}. Target agents: {', '.join(target_agents)}. "
        "Return JSON matching {stage, target_agents, score, severity, findings, issues, requires_rework, rework_target}. "
        "Use severity pass, warning, or error. Set requires_rework true for blocking errors, score below 0.75, "
        "schema-breaking output, missing required fields, task drift, unauthorized skill use, or downstream blockers. "
        "Set rework_target to the specific agent that should redo work when possible."
    )


def _supervisor_repair_prompt() -> str:
    return (
        "You are the decision-making Supervisor Agent and should use deepseek-v4-pro when available. "
        "Repair slides_ir according to review_report while preserving deck intent. "
        "Return valid slides_ir JSON with deck, theme, slides, and optional supervisor_repairs. "
        "Keep slide numbers continuous, every title/message non-empty, layouts supported, and bullets to at most 3."
    )


def _ppt_spec_summary(spec: PptSpec) -> dict:
    return {
        "title": spec.title,
        "audience": spec.audience,
        "theme": spec.theme,
        "slide_count": len(spec.slides),
        "slides": [
            {
                "id": slide.id,
                "role": slide.role,
                "title": slide.title,
                "message": slide.message,
                "layout": slide.layout,
                "layout_hint": slide.layout_hint,
                "bullets": slide.bullets,
                "content_bullets": slide.content.bullets,
                "metrics": slide.content.metrics,
                "speaker_notes_present": bool(slide.speaker_notes.strip()),
            }
            for slide in spec.slides
        ],
    }


def _brief_agent(request: dict) -> dict:
    topic = request["topic"]
    return {
        "agent": "brief",
        "topic": topic,
        "audience": request.get("audience") or "general business audience",
        "purpose": "用于正式汇报和决策沟通",
        "language": request.get("language") or "zh-CN",
        "page_count": int(request.get("page_count") or DEFAULT_PAGE_COUNT),
        "tone": request.get("tone") or "专业、清晰、可信",
        "style_keywords": ["商务", "清晰", "科技感"],
        "success_criteria": [
            "结构完整，能从背景推进到方案和行动",
            "每页有明确主张，正文不过载",
            "最终可以直接交给页面生成器合成 PPT",
        ],
    }


def _brief_outline_agent(brief: dict) -> dict:
    topic = brief["topic"]
    templates = [
        ("cover", topic, "建立主题和汇报场景"),
        ("agenda", "汇报结构", "让听众理解整份材料的推进逻辑"),
        ("context", "背景与变化", "说明为什么现在需要关注该主题"),
        ("problem", "核心问题", "归纳当前最需要解决的业务或技术问题"),
        ("impact", "影响与机会", "把问题转化为业务影响和改进空间"),
        ("insight", "关键洞察", "提炼支撑方案设计的判断"),
        ("solution", "总体方案", "给出面向目标受众的整体解决思路"),
        ("architecture", "能力架构", "拆解方案的关键组成模块"),
        ("workflow", "运行流程", "说明方案如何在实际工作中运转"),
        ("use_case", "典型场景", "展示优先落地的高价值应用场景"),
        ("data_chart", "指标与数据", "用指标解释预期变化和衡量方式"),
        ("roadmap", "实施路线", "给出阶段化推进计划"),
        ("risk", "风险与治理", "说明风险、约束和控制机制"),
        ("investment", "资源与分工", "明确推进所需资源和角色责任"),
        ("closing", "决策与下一步", "收束为明确决策和行动清单"),
    ]
    slide_count = min(max(int(brief.get("page_count") or DEFAULT_PAGE_COUNT), 1), len(templates))
    slides = []
    for index, (slide_type, title, goal) in enumerate(templates[:slide_count], start=1):
        slides.append(
            {
                "slide_no": index,
                "type": slide_type,
                "title": title,
                "goal": goal,
            }
        )
    return {"agent": "brief_outline", "slides": slides}


def _content_agent(brief: dict, outline: dict) -> dict:
    topic = brief["topic"]
    content_slides = []
    for slide in outline["slides"]:
        slide_type = slide["type"]
        title = slide["title"]
        content_slides.append(
            {
                "slide_no": slide["slide_no"],
                "title": title,
                "message": _message_for_slide(topic, slide_type, title),
                "bullets": _bullets_for_slide(topic, slide_type),
                "speaker_notes": f"本页围绕“{title}”展开，强调与“{topic}”的关系，并为下一页做铺垫。",
            }
        )
    return {"agent": "content", "slides": content_slides}


def _design_chart_agent(brief: dict, outline: dict) -> dict:
    chart_slides = []
    for slide in outline["slides"]:
        if slide["type"] in {"impact", "data_chart", "roadmap"}:
            chart_slides.append(
                {
                    "slide_no": slide["slide_no"],
                    "type": "bar" if slide["type"] != "roadmap" else "timeline",
                    "title": slide["title"],
                    "data": [
                        {"label": "当前", "value": 35},
                        {"label": "目标", "value": 78},
                    ],
                    "unit": "%",
                }
            )
    return {
        "agent": "design_chart",
        "theme": {
            "name": "business-tech",
            "colors": {
                "background": "#F7F9FB",
                "primary": "#1F4E79",
                "accent": "#00A6A6",
                "text": "#1F2933",
            },
            "font": {"title": "Microsoft YaHei", "body": "Microsoft YaHei"},
            "layout_rules": {
                "max_bullets_per_slide": 3,
                "title_position": "top",
                "prefer_layouts": ["title_cover", "two_column_text_image", "three_card_summary", "process_timeline", "comparison_table"],
            },
        },
        "charts": chart_slides,
    }


def _supervisor_agent(brief: dict, outline: dict, content: dict, design_chart: dict) -> dict:
    content_by_slide = {slide["slide_no"]: slide for slide in content["slides"]}
    chart_by_slide = {chart["slide_no"]: chart for chart in design_chart["charts"]}
    slides = []
    for outline_slide in outline["slides"]:
        slide_no = outline_slide["slide_no"]
        content_slide = content_by_slide[slide_no]
        layout = _layout_for_type(outline_slide["type"])
        visual_type = _visual_for_type(outline_slide["type"])
        slide = {
            "slide_no": slide_no,
            "type": outline_slide["type"],
            "layout": layout,
            "visual_type": visual_type,
            "title": content_slide["title"],
            "message": content_slide["message"],
            "bullets": content_slide["bullets"][:3],
            "notes": content_slide["speaker_notes"],
        }
        if slide_no in chart_by_slide:
            slide["chart"] = chart_by_slide[slide_no]
        slides.append(slide)
    return {
        "deck": {
            "title": brief["topic"],
            "audience": brief["audience"],
            "language": brief["language"],
            "size": "16:9",
            "page_count": len(slides),
        },
        "theme": design_chart["theme"],
        "slides": slides,
    }


def _page_designer_agent_with_llm(
    slides_ir: dict,
    config: AgentLlmConfig,
    skill_assignments: AgentSkillAssignments,
) -> dict:
    llm_payload = _llm_or_none(
        "page_designer",
        PageDesignArtifact,
        system_prompt=_page_designer_prompt(),
        user_payload={
            "slides_ir": slides_ir,
            "assigned_skills": skill_context_for_agent(skill_assignments, "page_designer"),
        },
        config=config,
    )
    if llm_payload is not None:
        return llm_payload
    return _page_designer_agent(slides_ir)


def _page_designer_agent(slides_ir: dict) -> dict:
    slides = slides_ir.get("slides") or []
    designed = []
    for item in slides:
        layout = _page_design_layout(item)
        visual_priority = _visual_priority(item)
        designed.append(
            {
                "slide_no": item["slide_no"],
                "layout": layout,
                "visual_priority": visual_priority,
                "hierarchy": _hierarchy_for_layout(layout),
                "figure_strategy": _figure_strategy(item, layout),
                "density": "low" if item["type"] in {"cover", "closing"} else "standard",
                "speaker_focus": item.get("message") or item.get("title") or "",
                "renderer_notes": _renderer_notes(item, layout),
            }
        )
    return {
        "agent": "page_designer",
        "design_system": {
            "role": "LLM-ready page design artifact",
            "audience": (slides_ir.get("deck") or {}).get("audience"),
            "principles": [
                "one main message per slide",
                "figures receive primary visual space",
                "text pages use cards or timelines instead of plain bullet lists",
                "page_generator must preserve content and apply layout decisions deterministically",
            ],
        },
        "slides": designed,
    }


def _page_generator_agent(slides_ir: dict, intent: DeckIntent, *, page_design: dict | None = None) -> PptSpec:
    design_by_slide = {
        slide["slide_no"]: slide
        for slide in (page_design or {}).get("slides", [])
        if isinstance(slide, dict) and slide.get("slide_no") is not None
    }
    slides = []
    for item in slides_ir["slides"]:
        design = design_by_slide.get(item["slide_no"], {})
        layout = design.get("layout") or item["layout"]
        chart = item.get("chart")
        content = SlideContent(
            bullets=item.get("bullets") or [],
            metrics=[chart] if chart else [],
            visual_reason=design.get("renderer_notes") or "由 Page Designer Agent 生成页面设计决策。",
        )
        slides.append(
            SlideSpec(
                id=f"slide-{item['slide_no']:03d}",
                role=item["type"],
                title=item["title"],
                message=item.get("message") or "",
                layout=layout,
                content=content,
                objective=item.get("message") or "",
                speaker_notes=item.get("notes") or "",
                visual_type=item["visual_type"],
                layout_hint=layout,
                style_tags=["multi-agent", "business", "clear", "technology"],
                visual_spec={
                    "source_agent": "page_designer",
                    "slide_type": item["type"],
                    "page_design": design,
                    "upstream_layout": item["layout"],
                },
                grounding_status="ungrounded" if not intent.source_digest else "partial",
            )
        )
    return PptSpec(
        schema_version=2,
        title=slides_ir["deck"]["title"],
        audience=slides_ir["deck"]["audience"],
        goal="通过多智能体流水线生成结构化演示文稿。",
        narrative="从背景、问题、洞察、方案、落地、治理到决策逐步推进。",
        theme="executive_blue",
        slides=slides,
        source_digest=intent.source_digest
        or {
            "type": "multi_agent_pipeline",
            "task_dir": slides_ir.get("task_dir"),
            "page_count": slides_ir["deck"]["page_count"],
            "agents": ["supervisor", "brief_outline", "content", "design_chart", "page_designer", "renderer_engineer", "page_generator", "qa"],
        },
        applied_skills=[*intent.applied_skills, "multi_agent_pipeline"],
        output_format=intent.output_format,
    )


def _qa_agent(slides_ir: dict) -> PipelineQaReport:
    issues: list[PipelineQaIssue] = []
    deck = slides_ir.get("deck") or {}
    slides = slides_ir.get("slides") or []
    expected_count = int(deck.get("page_count") or 0)
    if expected_count and len(slides) != expected_count:
        issues.append(
            PipelineQaIssue(
                id="deck:page_count_mismatch",
                severity="error",
                agent="qa",
                message=f"slides_ir contains {len(slides)} slides but deck.page_count is {expected_count}.",
                suggested_fix="Align slides_ir.slides length with deck.page_count.",
            )
        )
    for index, slide in enumerate(slides, start=1):
        slide_no = slide.get("slide_no") or index
        if slide_no != index:
            issues.append(
                PipelineQaIssue(
                    id=f"slide-{index:03d}:non_sequential_slide_no",
                    severity="error",
                    agent="qa",
                    slide_no=slide_no,
                    message="Slide numbers must be continuous and ordered.",
                    suggested_fix="Supervisor should renumber slides before page generation.",
                )
            )
        if not str(slide.get("title") or "").strip():
            issues.append(
                PipelineQaIssue(
                    id=f"slide-{slide_no:03d}:missing_title",
                    severity="error",
                    agent="qa",
                    slide_no=slide_no,
                    message="Slide title is empty.",
                    suggested_fix="Content Agent should provide a concise title.",
                )
            )
        if not str(slide.get("message") or "").strip():
            issues.append(
                PipelineQaIssue(
                    id=f"slide-{slide_no:03d}:missing_message",
                    severity="error",
                    agent="qa",
                    slide_no=slide_no,
                    message="Slide message is empty.",
                    suggested_fix="Content Agent should provide the main point for the slide.",
                )
            )
        bullets = [bullet for bullet in slide.get("bullets") or [] if str(bullet).strip()]
        if not bullets and slide.get("type") != "cover":
            issues.append(
                PipelineQaIssue(
                    id=f"slide-{slide_no:03d}:missing_bullets",
                    severity="warning",
                    agent="qa",
                    slide_no=slide_no,
                    message="Non-cover slide has no bullets.",
                    suggested_fix="Content Agent should add one to three body bullets.",
                )
            )
        if len(bullets) > MAX_VISIBLE_BULLETS:
            issues.append(
                PipelineQaIssue(
                    id=f"slide-{slide_no:03d}:too_many_bullets",
                    severity="warning",
                    agent="qa",
                    slide_no=slide_no,
                    message=f"Slide has {len(bullets)} bullets.",
                    suggested_fix=f"Keep visible bullets to {MAX_VISIBLE_BULLETS} or fewer.",
                )
            )
        if not str(slide.get("layout") or "").strip():
            issues.append(
                PipelineQaIssue(
                    id=f"slide-{slide_no:03d}:missing_layout",
                    severity="error",
                    agent="qa",
                    slide_no=slide_no,
                    message="Slide layout is empty.",
                    suggested_fix="Supervisor should assign a supported layout before page generation.",
                )
            )
    return PipelineQaReport(ok=not any(issue.severity == "error" for issue in issues), issues=issues)


def _qa_agent_with_llm(
    slides_ir: dict,
    config: AgentLlmConfig,
    skill_assignments: AgentSkillAssignments,
) -> PipelineQaReport:
    deterministic = _qa_agent(slides_ir)
    llm_payload = _llm_or_none(
        "qa",
        PipelineQaReport,
        system_prompt=_qa_prompt(),
        user_payload={
            "slides_ir": slides_ir,
            "deterministic_report": deterministic.model_dump(mode="json"),
            "assigned_skills": skill_context_for_agent(skill_assignments, "qa"),
        },
        config=config,
    )
    if llm_payload is None:
        return deterministic
    llm_report = PipelineQaReport.model_validate(llm_payload)
    merged = {issue.id: issue for issue in deterministic.issues}
    for issue in llm_report.issues:
        merged.setdefault(issue.id, issue)
    return PipelineQaReport(ok=not any(issue.severity == "error" for issue in merged.values()), issues=list(merged.values()))


def _render_review_agent_with_llm(
    slides_ir: dict,
    page_design: dict | None,
    renderer_engineer_report: dict | None,
    spec: PptSpec,
    config: AgentLlmConfig,
    skill_assignments: AgentSkillAssignments,
) -> PipelineQaReport:
    deterministic = _render_review_agent(slides_ir, spec, page_design=page_design)
    llm_payload = _llm_or_none(
        "render_review",
        PipelineQaReport,
        system_prompt=_render_review_prompt(),
        user_payload={
            "slides_ir": slides_ir,
            "page_design": page_design,
            "renderer_engineer_report": renderer_engineer_report,
            "ppt_spec_summary": _ppt_spec_summary(spec),
            "deterministic_report": deterministic.model_dump(mode="json"),
            "assigned_skills": skill_context_for_agent(skill_assignments, "render_review"),
        },
        config=config,
    )
    if llm_payload is None:
        return deterministic
    llm_report = PipelineQaReport.model_validate(llm_payload)
    merged = {issue.id: issue for issue in deterministic.issues}
    for issue in llm_report.issues:
        merged.setdefault(issue.id, issue)
    return PipelineQaReport(ok=not any(issue.severity == "error" for issue in merged.values()), issues=list(merged.values()))


def _render_review_agent(slides_ir: dict, spec: PptSpec, *, page_design: dict | None = None) -> PipelineQaReport:
    issues: list[PipelineQaIssue] = []
    ir_slides = slides_ir.get("slides") or []
    design_by_slide = {
        slide.get("slide_no"): slide
        for slide in (page_design or {}).get("slides", [])
        if isinstance(slide, dict)
    }
    if len(ir_slides) != len(spec.slides):
        issues.append(
            PipelineQaIssue(
                id="render:slide_count_mismatch",
                severity="error",
                agent="render_review",
                message=f"slides_ir has {len(ir_slides)} slides but PptSpec has {len(spec.slides)} slides.",
                suggested_fix="Page Generator must preserve slide count.",
            )
        )
    for index, ir_slide in enumerate(ir_slides, start=1):
        if index > len(spec.slides):
            break
        spec_slide = spec.slides[index - 1]
        slide_no = ir_slide.get("slide_no") or index
        if ir_slide.get("title") != spec_slide.title:
            issues.append(
                PipelineQaIssue(
                    id=f"render:slide-{slide_no:03d}:title_mismatch",
                    severity="error",
                    agent="render_review",
                    slide_no=slide_no,
                    message="Page Generator changed or dropped the slide title.",
                    suggested_fix="Map slides_ir.title directly into SlideSpec.title.",
                )
            )
        if (ir_slide.get("message") or "") != (spec_slide.message or spec_slide.core_message):
            issues.append(
                PipelineQaIssue(
                    id=f"render:slide-{slide_no:03d}:message_mismatch",
                    severity="warning",
                    agent="render_review",
                    slide_no=slide_no,
                    message="Page Generator message mapping differs from slides_ir.message.",
                    suggested_fix="Mirror slides_ir.message into SlideSpec.message/core_message.",
                )
            )
        ir_bullets = [bullet for bullet in ir_slide.get("bullets") or [] if str(bullet).strip()]
        if ir_bullets != spec_slide.bullets:
            issues.append(
                PipelineQaIssue(
                    id=f"render:slide-{slide_no:03d}:bullet_mismatch",
                    severity="warning",
                    agent="render_review",
                    slide_no=slide_no,
                    message="Page Generator bullet mapping differs from slides_ir.bullets.",
                    suggested_fix="Preserve visible bullets in SlideSpec.content.bullets and SlideSpec.bullets.",
                )
            )
        if not spec_slide.layout:
            issues.append(
                PipelineQaIssue(
                    id=f"render:slide-{slide_no:03d}:missing_layout",
                    severity="error",
                    agent="render_review",
                    slide_no=slide_no,
                    message="Generated SlideSpec has no layout.",
                    suggested_fix="Map slides_ir.layout into SlideSpec.layout/layout_hint.",
                )
            )
        expected_layout = (design_by_slide.get(slide_no) or {}).get("layout")
        if expected_layout and spec_slide.layout != expected_layout:
            issues.append(
                PipelineQaIssue(
                    id=f"render:slide-{slide_no:03d}:page_design_layout_mismatch",
                    severity="warning",
                    agent="render_review",
                    slide_no=slide_no,
                    message="Page Generator did not apply the Page Designer layout.",
                    suggested_fix="Map page_design.layout into SlideSpec.layout/layout_hint.",
                )
            )
    return PipelineQaReport(ok=not any(issue.severity == "error" for issue in issues), issues=issues)


def _supervisor_repair_agent(slides_ir: dict, report: PipelineQaReport) -> dict:
    repaired = json.loads(json.dumps(slides_ir, ensure_ascii=False))
    slides = repaired.get("slides") or []
    for index, slide in enumerate(slides, start=1):
        slide["slide_no"] = index
        slide["title"] = str(slide.get("title") or f"第 {index} 页").strip()
        slide["message"] = str(slide.get("message") or slide["title"]).strip()
        slide["layout"] = str(slide.get("layout") or _layout_for_type(slide.get("type") or "")).strip()
        slide["visual_type"] = str(slide.get("visual_type") or _visual_for_type(slide.get("type") or "")).strip()
        bullets = [str(bullet).strip() for bullet in slide.get("bullets") or [] if str(bullet).strip()]
        if not bullets and slide.get("type") != "cover":
            bullets = [slide["message"]]
        slide["bullets"] = bullets[:MAX_VISIBLE_BULLETS]
    repaired["deck"]["page_count"] = len(slides)
    repaired.setdefault("supervisor_repairs", [])
    repaired["supervisor_repairs"].extend(issue.model_dump(mode="json") for issue in report.issues)
    return repaired


def _supervisor_repair_agent_with_llm(
    slides_ir: dict,
    report: PipelineQaReport,
    config: AgentLlmConfig,
    skill_assignments: AgentSkillAssignments,
) -> dict:
    llm_payload = _llm_or_none(
        "supervisor",
        SlidesIrArtifact,
        system_prompt=_supervisor_repair_prompt(),
        user_payload={
            "slides_ir": slides_ir,
            "review_report": report.model_dump(mode="json"),
            "all_skills": skill_context_for_agent(skill_assignments, "supervisor"),
            "skill_assignments": skill_assignments.names_by_agent(),
        },
        config=config,
    )
    if llm_payload is not None:
        return llm_payload
    return _supervisor_repair_agent(slides_ir, report)


def _llm_or_fallback(
    agent_name: str,
    model: type[BaseModel],
    *,
    system_prompt: str,
    user_payload: dict,
    fallback: Callable[[], dict],
    config: AgentLlmConfig,
) -> dict:
    llm_payload = _llm_or_none(agent_name, model, system_prompt=system_prompt, user_payload=user_payload, config=config)
    return llm_payload if llm_payload is not None else fallback()


def _llm_or_none(
    agent_name: str,
    model: type[BaseModel],
    *,
    system_prompt: str,
    user_payload: dict,
    config: AgentLlmConfig,
) -> dict | None:
    try:
        payload = generate_agent_json(
            agent_name,
            system_prompt=system_prompt,
            user_payload=user_payload,
            config=config,
        )
        if payload is None:
            return None
        return model.model_validate(payload).model_dump(mode="json")
    except (httpx.HTTPError, PlannerConfigError, ValueError, ValidationError):
        if config.fallback_to_deterministic:
            return None
        raise


def _initial_task_plan(artifacts: dict[str, str]) -> list[PipelineStageStatus]:
    return [
        PipelineStageStatus(name="user_request", agent="supervisor", output_files=[artifacts["user_request"]]),
        PipelineStageStatus(name="brief", agent="brief_outline", input_files=[artifacts["user_request"]], output_files=[artifacts["brief"]]),
        PipelineStageStatus(name="brief_outline", agent="brief_outline", input_files=[artifacts["brief"]], output_files=[artifacts["outline"]]),
        PipelineStageStatus(name="content", agent="content", input_files=[artifacts["outline"]], output_files=[artifacts["content"]]),
        PipelineStageStatus(name="design_chart", agent="design_chart", input_files=[artifacts["brief"], artifacts["outline"]], output_files=[artifacts["design_chart"]]),
        PipelineStageStatus(name="supervisor", agent="supervisor", input_files=[artifacts["brief"], artifacts["outline"], artifacts["content"], artifacts["design_chart"]], output_files=[artifacts["slides_ir"]]),
        PipelineStageStatus(name="evaluator", agent="evaluator", input_files=[artifacts["brief"], artifacts["outline"], artifacts["content"], artifacts["design_chart"], artifacts["slides_ir"]], output_files=[artifacts["evaluation_report"]]),
        PipelineStageStatus(name="qa", agent="qa", input_files=[artifacts["slides_ir"]], output_files=[artifacts["review_report"]]),
        PipelineStageStatus(name="page_designer", agent="page_designer", input_files=[artifacts["slides_ir"], artifacts["review_report"]], output_files=[artifacts["page_design"]]),
        PipelineStageStatus(name="renderer_engineer", agent="renderer_engineer", input_files=[artifacts["slides_ir"], artifacts["page_design"]], output_files=[artifacts["renderer_engineer_report"], artifacts["renderer_scripts"]]),
        PipelineStageStatus(name="render_review", agent="render_review", input_files=[artifacts["slides_ir"], artifacts["page_design"], artifacts["renderer_engineer_report"]], output_files=[artifacts["render_review_report"]]),
    ]


def _write_stage_output(
    stages: list[PipelineStageStatus],
    stage_name: str,
    path: str,
    payload: dict,
    issues: list[str] | None = None,
) -> None:
    _write_json(Path(path), payload)
    for stage in stages:
        if stage.name == stage_name:
            stage.status = "completed" if not issues else "completed_with_warnings"
            stage.issues = issues or []
            return


def _task_plan_payload(
    intent: DeckIntent,
    stages: list[PipelineStageStatus],
    artifacts: dict[str, str],
    skill_assignments: AgentSkillAssignments,
) -> dict:
    return {
        "topic": intent.topic,
        "created_by": "multi_agent_pipeline",
        "default_page_count": DEFAULT_PAGE_COUNT,
        "artifacts": artifacts,
        "skill_policy": {
            "supervisor_can_read_all_skills": True,
            "workers_can_use_only_assigned_skills": True,
            "page_designer_can_use_design_skills": True,
            "renderer_engineer_can_read_renderer_code": True,
            "renderer_engineer_can_propose_scripts": True,
            "page_generator_uses_skills": False,
        },
        "skill_catalog": [skill.model_dump(mode="json") for skill in skill_assignments.supervisor_catalog],
        "skill_assignments": skill_assignments.names_by_agent(),
        "ownership": {
            artifacts["user_request"]: "supervisor",
            artifacts["task_plan"]: "supervisor",
            artifacts["brief"]: "brief_outline",
            artifacts["outline"]: "brief_outline",
            artifacts["content"]: "content",
            artifacts["design_chart"]: "design_chart",
            artifacts["slides_ir"]: "supervisor",
            artifacts["page_design"]: "page_designer",
            artifacts["renderer_engineer_report"]: "renderer_engineer",
            artifacts["renderer_scripts"]: "renderer_engineer",
            artifacts["review_report"]: "qa",
            artifacts["evaluation_report"]: "evaluator",
            artifacts["render_review_report"]: "render_review",
        },
        "rules": [
            "Only the owning agent may write its artifact.",
            "Worker agents exchange data through JSON artifacts, not direct mutation.",
            "Page Designer owns page_design.json and may decide layouts, visual hierarchy, density, and renderer notes without rewriting content.",
            "Renderer Engineer owns renderer_engineer_report.json, can inspect renderer code, and can propose renderer extensions or scripts without mutating slide content.",
            "Only the page_generator stage converts slides_ir plus page_design into PptSpec/PPTX-ready data.",
            "QA issues are reported in review_report.json; Supervisor owns repair decisions.",
            "Render Review only reports Page Generator mapping issues; it must not mutate slides_ir, PptSpec, or PPTX.",
            "Evaluator only reports quality gates and rework recommendations; Supervisor and graph routing own rework decisions.",
        ],
        "stages": [stage.model_dump(mode="json") for stage in stages],
    }


def _validate_brief(brief: dict) -> list[str]:
    issues = _model_validation_issues(BriefArtifact, brief, "brief")
    for field in ("topic", "audience", "page_count", "language"):
        if not brief.get(field):
            issues.append(f"brief missing {field}")
    if int(brief.get("page_count") or 0) <= 0:
        issues.append("brief page_count must be positive")
    return issues


def _validate_outline(outline: dict, brief: dict) -> list[str]:
    issues = _model_validation_issues(OutlineArtifact, outline, "outline")
    slides = outline.get("slides") or []
    if len(slides) != int(brief.get("page_count") or 0):
        issues.append("outline slide count differs from brief page_count")
    seen = set()
    for slide in slides:
        slide_no = slide.get("slide_no")
        if slide_no in seen:
            issues.append(f"duplicate outline slide_no {slide_no}")
        seen.add(slide_no)
        if not slide.get("title"):
            issues.append(f"outline slide {slide_no} missing title")
    return issues


def _validate_content(content: dict, outline: dict) -> list[str]:
    issues = _model_validation_issues(ContentArtifact, content, "content")
    outline_numbers = {slide.get("slide_no") for slide in outline.get("slides") or []}
    content_numbers = {slide.get("slide_no") for slide in content.get("slides") or []}
    if outline_numbers != content_numbers:
        issues.append("content slide numbers differ from outline")
    for slide in content.get("slides") or []:
        if not slide.get("message"):
            issues.append(f"content slide {slide.get('slide_no')} missing message")
        if len(slide.get("bullets") or []) > MAX_VISIBLE_BULLETS:
            issues.append(f"content slide {slide.get('slide_no')} has more than {MAX_VISIBLE_BULLETS} bullets")
    return issues


def _validate_design_chart(design_chart: dict, outline: dict) -> list[str]:
    issues = _model_validation_issues(DesignChartArtifact, design_chart, "design_chart")
    if not (design_chart.get("theme") or {}).get("colors"):
        issues.append("design_chart missing theme colors")
    outline_numbers = {slide.get("slide_no") for slide in outline.get("slides") or []}
    for chart in design_chart.get("charts") or []:
        if chart.get("slide_no") not in outline_numbers:
            issues.append(f"chart references unknown slide {chart.get('slide_no')}")
    return issues


def _validate_slides_ir(slides_ir: dict, brief: dict) -> list[str]:
    issues = _model_validation_issues(SlidesIrArtifact, slides_ir, "slides_ir")
    slides = slides_ir.get("slides") or []
    if len(slides) != int(brief.get("page_count") or 0):
        issues.append("slides_ir slide count differs from brief page_count")
    for index, slide in enumerate(slides, start=1):
        if slide.get("slide_no") != index:
            issues.append(f"slides_ir slide {index} has non-sequential slide_no")
        if not slide.get("layout"):
            issues.append(f"slides_ir slide {index} missing layout")
    return issues


def _validate_page_design(page_design: dict, slides_ir: dict) -> list[str]:
    issues = _model_validation_issues(PageDesignArtifact, page_design, "page_design")
    ir_numbers = {slide.get("slide_no") for slide in slides_ir.get("slides") or []}
    design_numbers = {slide.get("slide_no") for slide in page_design.get("slides") or []}
    if ir_numbers != design_numbers:
        issues.append("page_design slide numbers differ from slides_ir")
    supported = {
        "hero",
        "title-bullets",
        "two_column_text_image",
        "figure_caption",
        "two_column_figure",
        "comparison_table",
        "process_timeline",
        "three_card_summary",
        "big_quote",
    }
    for slide in page_design.get("slides") or []:
        if slide.get("layout") not in supported:
            issues.append(f"page_design slide {slide.get('slide_no')} uses unsupported layout {slide.get('layout')}")
    return issues


def _model_validation_issues(model: type[BaseModel], payload: dict, label: str) -> list[str]:
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return [f"{label} schema error: {error['loc']} {error['msg']}" for error in exc.errors()]
    return []


def _evaluate_brief_outline(brief: dict, outline: dict) -> StageEvaluation:
    issues = [
        EvaluationIssue(severity="error" if "missing" in issue or "differs" in issue else "warning", agent="brief_outline", message=issue)
        for issue in [*_validate_brief(brief), *_validate_outline(outline, brief)]
    ]
    return _stage_evaluation(
        stage="brief_outline_eval",
        target_agents=["brief_outline"],
        issues=issues,
        findings=["Brief and outline were checked for schema, page count, titles, and sequential slide numbers."],
        rework_target="brief_outline",
    )


def _evaluate_content(content: dict, outline: dict) -> StageEvaluation:
    issues = [
        EvaluationIssue(severity="error" if "missing" in issue or "differ" in issue else "warning", agent="content", message=issue)
        for issue in _validate_content(content, outline)
    ]
    return _stage_evaluation(
        stage="content_eval",
        target_agents=["content"],
        issues=issues,
        findings=["Content artifact was checked immediately after Content Agent completed."],
        rework_target="content",
    )


def _evaluate_design_chart(design_chart: dict, outline: dict) -> StageEvaluation:
    issues = [
        EvaluationIssue(severity="error" if "unknown" in issue else "warning", agent="design_chart", message=issue)
        for issue in _validate_design_chart(design_chart, outline)
    ]
    return _stage_evaluation(
        stage="design_chart_eval",
        target_agents=["design_chart"],
        issues=issues,
        findings=["Design/chart artifact was checked immediately after Design + Chart Agent completed."],
        rework_target="design_chart",
    )


def _evaluate_slides_ir(slides_ir: dict, brief: dict) -> StageEvaluation:
    issues = [
        EvaluationIssue(severity="error" if "missing" in issue or "differs" in issue else "warning", agent="supervisor", message=issue)
        for issue in _validate_slides_ir(slides_ir, brief)
    ]
    return _stage_evaluation(
        stage="slides_ir_eval",
        target_agents=["supervisor"],
        issues=issues,
        findings=["slides_ir was checked as the single merge point before page generation."],
        rework_target="supervisor",
    )


def _evaluate_final(state: dict[str, Any]) -> StageEvaluation:
    review = PipelineQaReport.model_validate(state.get("review_report") or {})
    render = PipelineQaReport.model_validate(state.get("render_review_report") or {})
    renderer_report = state.get("renderer_engineer_report") or {}
    issues = [
        EvaluationIssue(severity=issue.severity, agent="qa", message=issue.message, suggested_fix=issue.suggested_fix)
        for issue in review.issues
        if issue.severity == "error"
    ]
    issues.extend(
        EvaluationIssue(severity=issue.severity, agent="render_review", message=issue.message, suggested_fix=issue.suggested_fix)
        for issue in render.issues
        if issue.severity == "error"
    )
    if renderer_report.get("risk_level") == "high":
        issues.append(
            EvaluationIssue(
                severity="error",
                agent="renderer_engineer",
                message="Renderer Engineer marked renderer implementation risk as high.",
                suggested_fix="Resolve renderer extension plan before regenerating PptSpec.",
            )
        )
    target = (
        "renderer_engineer"
        if any(issue.agent == "renderer_engineer" for issue in issues)
        else "render_review"
        if any(issue.agent == "render_review" for issue in issues)
        else "page_generator"
        if issues
        else None
    )
    return _stage_evaluation(
        stage="final_eval",
        target_agents=["qa", "renderer_engineer", "page_generator", "render_review"],
        issues=issues,
        findings=["Final reports were checked for blocking QA, renderer engineering, or render mapping failures."],
        rework_target=target,
    )


def _stage_evaluation(
    *,
    stage: str,
    target_agents: list[str],
    issues: list[EvaluationIssue],
    findings: list[str],
    rework_target: str | None,
) -> StageEvaluation:
    has_error = any(issue.severity == "error" for issue in issues)
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    score = max(0.0, 1.0 - (0.35 if has_error else 0.0) - warning_count * 0.08)
    requires_rework = has_error or score < _rework_policy()["block_score_below"]
    return StageEvaluation(
        stage=stage,
        target_agents=target_agents,
        score=round(score, 2),
        severity="error" if has_error else "warning" if warning_count else "pass",
        findings=findings,
        issues=issues,
        requires_rework=requires_rework,
        rework_target=rework_target if requires_rework else None,
    )


def _message_for_slide(topic: str, slide_type: str, title: str) -> str:
    messages = {
        "cover": f"{topic}需要被组织成可执行、可衡量、可落地的汇报方案。",
        "agenda": "本次汇报按背景、问题、方案、落地和决策推进。",
        "context": f"{topic}正在受到业务变化、技术成熟和组织效率要求的共同推动。",
        "problem": "当前关键矛盾不是缺少想法，而是缺少结构化推进和可复用机制。",
        "impact": "问题会直接影响效率、质量、成本和管理可见性。",
        "insight": "方案设计应优先解决高频、可衡量、跨团队协作成本高的环节。",
        "solution": f"围绕{topic}建立统一目标、流程、能力和指标体系。",
        "architecture": "能力架构需要把内容、流程、数据、工具和治理连接起来。",
        "workflow": "运行流程应覆盖输入、处理、协作、输出和反馈闭环。",
        "use_case": "优先选择价值清晰、边界明确、能快速验证的场景。",
        "data_chart": "指标体系应同时覆盖过程指标和结果指标。",
        "roadmap": "推进节奏应从试点开始，再扩展到标准化和规模化。",
        "risk": "风险治理需要前置设计，避免在推广阶段集中暴露。",
        "investment": "资源投入要对应到明确角色、交付物和验收标准。",
        "closing": "下一步应聚焦决策确认、责任分配和试点启动。",
    }
    return messages.get(slide_type, title)


def _bullets_for_slide(topic: str, slide_type: str) -> list[str]:
    bullets = {
        "cover": ["明确汇报目标与决策场景", "突出主题价值和落地导向", "建立专业、可信的第一印象"],
        "agenda": ["背景与问题", "方案与能力架构", "路线、治理与决策"],
        "context": ["外部环境推动组织提升响应速度", "内部流程需要降低重复劳动", "技术能力已具备规模化应用基础"],
        "problem": ["信息和流程分散导致协作成本高", "缺少统一指标使管理判断滞后", "经验难以复用，质量依赖个人能力"],
        "impact": ["效率损失会放大到跨团队协作链路", "质量波动会影响客户和管理体验", "缺少数据闭环会削弱持续改进能力"],
        "insight": ["从高频场景切入更容易形成牵引", "标准化输入能提升后续自动化效果", "管理指标要和一线动作保持一致"],
        "solution": [f"围绕{topic}建立统一工作台", "用结构化流程连接人、数据和工具", "通过指标闭环持续优化执行质量"],
        "architecture": ["内容层沉淀标准知识和模板", "流程层编排任务、审批和协作", "数据层支撑度量、反馈和优化"],
        "workflow": ["输入需求和上下文", "生成方案、内容和执行建议", "沉淀结果并反馈到后续迭代"],
        "use_case": ["选择影响大且边界清晰的业务场景", "优先验证时间节省和质量提升", "把试点经验转化为标准模板"],
        "data_chart": ["定义采用率、效率和质量指标", "比较当前状态和目标状态", "按阶段复盘指标变化"],
        "roadmap": ["0-30 天完成范围和数据准备", "31-60 天完成试点和反馈优化", "61-90 天形成推广方案"],
        "risk": ["数据权限和合规要求需要前置确认", "内容质量需要专家校验机制", "推广节奏要匹配组织接受度"],
        "investment": ["明确业务负责人和技术负责人", "配置试点团队和评审机制", "建立可复用的模板和运营规范"],
        "closing": ["确认试点范围和目标指标", "指定负责人和推进节奏", "在固定时间点评估是否扩大投入"],
    }
    return bullets.get(slide_type, [topic, "核心判断", "下一步行动"])


def _layout_for_type(slide_type: str) -> str:
    mapping = {
        "cover": "title_cover",
        "agenda": "process_timeline",
        "problem": "three_card_summary",
        "impact": "comparison_table",
        "architecture": "three_card_summary",
        "workflow": "process_timeline",
        "data_chart": "comparison_table",
        "roadmap": "process_timeline",
        "risk": "comparison_table",
        "investment": "three_card_summary",
        "closing": "three_card_summary",
    }
    return mapping.get(slide_type, "two_column_text_image")


def _page_design_layout(slide: dict) -> str:
    slide_type = slide.get("type", "")
    figure_ids = ((slide.get("content") or {}).get("figure_ids") or slide.get("figure_ids") or [])
    if len(figure_ids) > 1:
        return "two_column_figure"
    if len(figure_ids) == 1 or slide.get("visual_type") == "figure":
        return "figure_caption"
    if slide_type == "cover":
        return "hero"
    if slide_type in {"closing", "takeaway", "summary"}:
        return "big_quote"
    if slide_type in {"agenda", "workflow", "roadmap"}:
        return "process_timeline"
    if slide_type in {"problem", "insight", "solution", "investment"}:
        return "three_card_summary"
    if slide_type in {"impact", "data_chart", "risk"}:
        return "comparison_table"
    return slide.get("layout") or _layout_for_type(slide_type)


def _visual_priority(slide: dict) -> str:
    if slide.get("visual_type") == "figure" or ((slide.get("content") or {}).get("figure_ids")):
        return "figure"
    if slide.get("chart"):
        return "chart"
    if slide.get("type") in {"cover", "closing"}:
        return "message"
    return "balanced"


def _hierarchy_for_layout(layout: str) -> list[str]:
    mapping = {
        "hero": ["deck label", "title", "thesis", "context bullets"],
        "title-bullets": ["section title", "main message", "three cards"],
        "figure_caption": ["section title", "source figure", "interpretation bullets", "caption"],
        "two_column_figure": ["section title", "two source figures", "comparison takeaway", "caption"],
        "big_quote": ["takeaway label", "large conclusion", "supporting cards"],
        "process_timeline": ["section title", "message", "three-step progression"],
        "comparison_table": ["section title", "message", "comparison grid"],
        "three_card_summary": ["section title", "message", "three cards"],
    }
    return mapping.get(layout, ["section title", "message", "supporting content", "visual area"])


def _figure_strategy(slide: dict, layout: str) -> str:
    if layout == "two_column_figure":
        return "compare first two cited figures at equal size"
    if layout == "figure_caption":
        return "use primary figure as the main evidence object"
    return "none"


def _renderer_notes(slide: dict, layout: str) -> str:
    if layout in {"figure_caption", "two_column_figure"}:
        return "Prioritize source figure readability; preserve aspect ratio and keep captions compact."
    if layout == "hero":
        return "Use a restrained academic cover with strong title hierarchy."
    if layout == "big_quote":
        return "Make the conclusion dominant and keep support points short."
    return "Use low-density layout with clear title, message, and at most three supporting points."


def _visual_for_type(slide_type: str) -> str:
    mapping = {
        "cover": "hero_image",
        "agenda": "process_timeline",
        "problem": "three_card_summary",
        "impact": "comparison_table",
        "architecture": "three_card_summary",
        "workflow": "process_timeline",
        "data_chart": "comparison_table",
        "roadmap": "process_timeline",
        "risk": "comparison_table",
        "investment": "three_card_summary",
        "closing": "three_card_summary",
    }
    return mapping.get(slide_type, "workspace_photo")


def _task_id(topic: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in topic.strip())[:40].strip("-")
    slug = slug or "deck"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{slug}-{uuid4().hex[:8]}"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
