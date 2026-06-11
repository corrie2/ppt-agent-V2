from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class AgentMode(StrEnum):
    PLAN = "plan"
    EXECUTE = "execute"


class DeckIntent(BaseModel):
    topic: str
    audience: str = "general business audience"
    tone: str = "clear and pragmatic"
    output_path: str = "deck.pptx"
    source_digest: dict[str, Any] | None = None
    source_context: list[dict[str, Any]] = Field(default_factory=list)
    active_skill_context: str | None = None
    applied_skills: list[str] = Field(default_factory=list)
    output_format: str = "pptx"
    project_preferences: list[dict[str, Any]] = Field(default_factory=list)
    failure_patterns: list[dict[str, Any]] = Field(default_factory=list)


class VisualCallout(BaseModel):
    label: str = ""
    text: str = ""
    detail: str = ""  # Support alternative field name from LLM
    target: str | None = None
    evidence_id: str | None = None
    
    def model_post_init(self, __context) -> None:
        # Sync detail to text if text is empty
        if not self.text and self.detail:
            self.text = self.detail


class ResultSummary(BaseModel):
    metric: str | None = None
    finding: str = ""
    evidence_id: str | None = None


class SlideContent(BaseModel):
    bullets: list[str] = Field(default_factory=list)
    figure_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    visual_reason: str = ""
    callouts: list[VisualCallout] = Field(default_factory=list)
    result_summary: list[ResultSummary] = Field(default_factory=list)
    grounding_status: str = "grounded"

    @model_validator(mode="before")
    @classmethod
    def coerce_list_fields(cls, data: Any) -> Any:
        """Normalize fields that expect lists: None→[], dict→[dict], string metrics→[dict]."""
        _LIST_FIELDS = ("bullets", "figure_ids", "table_ids", "metrics",
                        "callouts", "result_summary")
        if isinstance(data, dict):
            # Generic: coerce None→[] for all list fields
            for fld in _LIST_FIELDS:
                if data.get(fld) is None:
                    data[fld] = [] if fld != "metrics" else []

            # result_summary: dict→[dict]
            rs = data.get("result_summary")
            if isinstance(rs, dict):
                data["result_summary"] = [rs]

            # callouts: dict→[dict]
            co = data.get("callouts")
            if isinstance(co, dict):
                data["callouts"] = [co]

            # metrics: string→[{...}], string items→[{...}]
            mt = data.get("metrics")
            if isinstance(mt, str):
                data["metrics"] = [{"finding": mt}]
            elif isinstance(mt, list):
                data["metrics"] = [
                    {"finding": m} if isinstance(m, str) else m
                    for m in mt
                ]

            # figure_ids / table_ids: coerce string→[string]
            for id_field in ("figure_ids", "table_ids"):
                val = data.get(id_field)
                if isinstance(val, str):
                    data[id_field] = [val]
        return data


class Citation(BaseModel):
    evidence_id: str
    page: int | None = None
    source_file: str | None = None


class SlideSpec(BaseModel):
    id: str | None = None
    role: str = ""
    title: str
    message: str = ""
    layout: str = ""
    content: SlideContent = Field(default_factory=SlideContent)
    citations: list[Citation] = Field(default_factory=list)
    objective: str = ""
    core_message: str = ""
    bullets: list[str] = Field(default_factory=list)
    supporting_points: list[str] = Field(default_factory=list)
    speaker_notes: str = ""
    visual_type: str = ""
    image_query: str = ""
    image_prompt: str = ""
    image_caption: str = ""
    image_rationale: str = ""
    layout_hint: str = ""
    style_tags: list[str] = Field(default_factory=list)
    visual_spec: dict[str, Any] = Field(default_factory=dict)
    resolved_asset: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    grounding_status: str = "ungrounded"
    source_notes: str = ""
    quality_checks: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_llm_output(cls, data: Any) -> Any:
        """Handle common LLM quirks before field validation."""
        if not isinstance(data, dict):
            return data
        # Coerce None→[] for all list fields
        _LIST_FIELDS = (
            "bullets", "supporting_points", "style_tags",
            "evidence_refs", "quality_checks", "citations",
        )
        for fld in _LIST_FIELDS:
            if data.get(fld) is None:
                data[fld] = []
        # Coerce string bullets/supporting_points to list
        for fld in ("bullets", "supporting_points"):
            val = data.get(fld)
            if isinstance(val, str):
                data[fld] = [val]
        # Coerce None string fields to ""
        _STR_FIELDS = (
            "role", "message", "layout", "objective", "core_message",
            "speaker_notes", "visual_type", "image_query", "image_prompt",
            "image_caption", "image_rationale", "layout_hint",
            "grounding_status", "source_notes",
        )
        for fld in _STR_FIELDS:
            if data.get(fld) is None:
                data[fld] = ""
        return data

    @model_validator(mode="after")
    def sync_new_and_legacy_fields(self) -> "SlideSpec":
        if self.message and not self.core_message:
            self.core_message = self.message
        elif self.core_message and not self.message:
            self.message = self.core_message

        if self.layout and not self.layout_hint:
            self.layout_hint = self.layout
        elif self.layout_hint and not self.layout:
            self.layout = self.layout_hint

        if self.content.bullets and not self.bullets:
            self.bullets = list(self.content.bullets)
        elif self.bullets and not self.content.bullets:
            self.content.bullets = list(self.bullets)

        if self.citations and not self.evidence_refs:
            self.evidence_refs = [citation.evidence_id for citation in self.citations]
        elif self.evidence_refs and not self.citations:
            self.citations = [Citation(evidence_id=evidence_id) for evidence_id in self.evidence_refs]

        return self

    @model_validator(mode="after")
    def sanitize_text_fields(self) -> "SlideSpec":
        """Clean LLM artifacts: vertical tabs, control chars, duplicate bullets."""
        # Clean \x0b (vertical tab) → newline in all string fields
        for field_name in ("title", "message", "core_message", "objective",
                           "speaker_notes", "image_caption", "image_rationale"):
            val = getattr(self, field_name, "")
            if val and "\x0b" in val:
                object.__setattr__(self, field_name, val.replace("\x0b", "\n").strip())

        # Clean bullets: deduplicate, remove role labels, strip control chars
        ROLE_LABELS = {
            "title", "problem or motivation", "contribution", "method detail",
            "method overview", "experiment setup", "result", "ablation",
            "conclusion", "takeaways", "limitation", "future work",
        }
        for bullets_attr in ("bullets",):
            raw = getattr(self, bullets_attr, [])
            if not raw:
                continue
            cleaned = []
            seen = set()
            for b in raw:
                if not b:
                    continue
                # Clean control chars
                b = b.replace("\x0b", " ").replace("\x00", "").strip()
                if not b:
                    continue
                # Skip role labels
                if b.lower().rstrip(".") in ROLE_LABELS:
                    continue
                # Deduplicate (by first 80 chars)
                key = b[:80].lower()
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(b)
            object.__setattr__(self, bullets_attr, cleaned)

        return self


class PptSpec(BaseModel):
    schema_version: int = 2
    title: str
    audience: str = "general business audience"
    goal: str | None = None
    narrative: str | None = None
    theme: str = "executive_blue"
    slides: list[SlideSpec]
    source_digest: dict[str, Any] | None = None
    applied_skills: list[str] = Field(default_factory=list)
    output_format: str = "pptx"
    skill_root: str | None = None
    skill_md_path: str | None = None
    grounding_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_llm_output(cls, data: Any) -> Any:
        """Handle common LLM output quirks before field validation."""
        if not isinstance(data, dict):
            return data

        # Ensure slides is a list; default to [] if missing or wrong type
        slides = data.get("slides")
        if slides is None:
            data["slides"] = []
        elif isinstance(slides, dict):
            # LLM might wrap slides in a key like {"slides": {"slides": [...]}}
            if "slides" in slides and isinstance(slides["slides"], list):
                data["slides"] = slides["slides"]
            else:
                data["slides"] = [slides]
        elif not isinstance(slides, list):
            data["slides"] = []

        # Filter out invalid slide entries (non-dict, empty, or missing title)
        filtered = []
        for slide in data["slides"]:
            if isinstance(slide, dict):
                # Ensure title exists — use message or core_message as fallback
                if "title" not in slide or not slide["title"]:
                    slide["title"] = slide.get("message") or slide.get("core_message") or "Untitled Slide"
                filtered.append(slide)
            elif hasattr(slide, 'title'):  # SlideSpec object
                filtered.append(slide)
        data["slides"] = filtered

        # Ensure title exists at top level
        if "title" not in data or not data["title"]:
            # Try to derive from first slide
            if data["slides"]:
                data["title"] = data["slides"][0].get("title", "Untitled Deck")
            else:
                data["title"] = "Untitled Deck"

        # Coerce schema_version to int
        sv = data.get("schema_version")
        if sv is not None and not isinstance(sv, int):
            try:
                data["schema_version"] = int(sv)
            except (ValueError, TypeError):
                data["schema_version"] = 2

        return data


class Artifact(BaseModel):
    path: Path
    kind: str = "pptx"


class QaIssue(BaseModel):
    code: str
    message: str
    severity: str = "warning"


class AgentState(BaseModel):
    intent: DeckIntent
    mode: AgentMode = AgentMode.EXECUTE
    planner_provider: str | None = None
    planner_model: str | None = None
    allow_fallback: bool = False
    approved: bool = False
    transitions: list[str] = Field(default_factory=list)
    asset_warnings: list[str] = Field(default_factory=list)
    spec: PptSpec | None = None
    artifact: Artifact | None = None
    visual_quality_report: dict | None = None
    visual_quality_report_path: str | None = None
    qa_issues: list[QaIssue] = Field(default_factory=list)
    repair_attempts: int = 0
