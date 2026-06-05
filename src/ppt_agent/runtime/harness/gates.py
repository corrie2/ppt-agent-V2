from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Severity = Literal["info", "warning", "error"]


class GateIssue(BaseModel):
    severity: Severity = "warning"
    rule: str
    message: str
    path: str | None = None
    suggested_fix: str | None = None


class GateResult(BaseModel):
    stage: str
    status: Literal["passed", "needs_rework", "failed"] = "passed"
    score: float = 1.0
    issues: list[GateIssue] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    next_action: Literal["continue", "rework", "stop"] = "continue"


def run_quality_gates(stage: str, payload: dict[str, Any] | list[Any] | None, *, context: dict[str, Any] | None = None) -> GateResult:
    context = context or {}
    issues: list[GateIssue] = []
    metrics: dict[str, Any] = {}
    payload = payload or {}

    if stage in {"brief_outline", "brief"}:
        brief = payload.get("brief", payload) if isinstance(payload, dict) else {}
        outline = payload.get("outline", {}) if isinstance(payload, dict) else {}
        _required(issues, brief, ["topic", "audience", "page_count"], "brief")
        slides = outline.get("slides") or []
        metrics["slide_count"] = len(slides)
        if not slides:
            issues.append(_issue("error", "min_slide_count", "outline has no slides", "outline.slides"))
        for index, slide in enumerate(slides, start=1):
            if not slide.get("title"):
                issues.append(_issue("error", "no_empty_title", f"outline slide {index} missing title", f"outline.slides[{index - 1}].title"))

    if stage == "content":
        slides = _slides(payload)
        metrics["slide_count"] = len(slides)
        for index, slide in enumerate(slides, start=1):
            if not slide.get("title"):
                issues.append(_issue("error", "no_empty_title", f"content slide {index} missing title", f"slides[{index - 1}].title"))
            if not (slide.get("message") or slide.get("core_message")):
                issues.append(_issue("warning", "no_empty_message", f"content slide {index} missing message", f"slides[{index - 1}].message"))
            bullets = slide.get("bullets") or (slide.get("content") or {}).get("bullets") or []
            if len(bullets) > 3:
                issues.append(_issue("warning", "max_bullets_per_slide", f"content slide {index} has {len(bullets)} bullets", f"slides[{index - 1}].bullets", "Compress to at most three visible bullets."))

    if stage == "design_chart":
        if isinstance(payload, dict) and not (payload.get("theme") or {}).get("colors"):
            issues.append(_issue("warning", "required_fields", "design_chart missing theme colors", "theme.colors"))

    if stage in {"slides_ir", "page_design", "page_generator"}:
        slides = _slides(payload)
        metrics["slide_count"] = len(slides)
        if not slides:
            issues.append(_issue("error", "min_slide_count", f"{stage} has no slides", "slides"))
        for index, slide in enumerate(slides, start=1):
            if not slide.get("title") and stage != "page_design":
                issues.append(_issue("error", "no_empty_title", f"{stage} slide {index} missing title", f"slides[{index - 1}].title"))
            if stage == "page_design" and slide.get("layout") in {"", None}:
                issues.append(_issue("error", "supported_layout", f"page_design slide {index} missing layout", f"slides[{index - 1}].layout"))

    if stage in {"qa", "render_review", "visual_quality"}:
        raw_issues = payload.get("issues") if isinstance(payload, dict) else []
        metrics["reported_issues"] = len(raw_issues or [])
        for item in raw_issues or []:
            severity = item.get("severity", "warning")
            issues.append(
                _issue(
                    "error" if severity == "error" else "warning",
                    "reported_issue",
                    item.get("message", "reported quality issue"),
                    item.get("path"),
                    item.get("suggested_fix"),
                )
            )
        if isinstance(payload, dict) and payload.get("ok") is False and not issues:
            issues.append(_issue("warning", "reported_issue", f"{stage} reported ok=false"))

    if context.get("evidence_required") and stage in {"slides_ir", "page_generator"}:
        slides = _slides(payload)
        missing = [index for index, slide in enumerate(slides, start=1) if not (slide.get("citations") or slide.get("evidence_refs"))]
        if missing:
            issues.append(
                _issue(
                    "warning",
                    "citation_required_when_evidence",
                    f"slides missing evidence refs: {missing[:8]}",
                    "slides",
                    "Attach evidence refs to grounded slides or mark grounding warnings.",
                )
            )

    return _result(stage, issues, metrics)


def _slides(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("slides"), list):
            return payload["slides"]
        if isinstance(payload.get("spec"), dict) and isinstance(payload["spec"].get("slides"), list):
            return payload["spec"]["slides"]
    return []


def _required(issues: list[GateIssue], payload: dict[str, Any], fields: list[str], prefix: str) -> None:
    for field in fields:
        if not payload.get(field):
            issues.append(_issue("error", "required_fields", f"{prefix} missing {field}", f"{prefix}.{field}"))


def _issue(severity: Severity, rule: str, message: str, path: str | None = None, suggested_fix: str | None = None) -> GateIssue:
    return GateIssue(severity=severity, rule=rule, message=message, path=path, suggested_fix=suggested_fix)


def _result(stage: str, issues: list[GateIssue], metrics: dict[str, Any]) -> GateResult:
    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    score = max(0.0, 1.0 - error_count * 0.35 - warning_count * 0.08)
    if error_count:
        status = "failed"
        next_action = "stop"
    elif score < 0.75 or warning_count:
        status = "needs_rework"
        next_action = "rework"
    else:
        status = "passed"
        next_action = "continue"
    return GateResult(stage=stage, status=status, score=round(score, 2), issues=issues, metrics=metrics, next_action=next_action)
