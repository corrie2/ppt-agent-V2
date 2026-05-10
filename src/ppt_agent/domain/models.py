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


class SlideContent(BaseModel):
    bullets: list[str] = Field(default_factory=list)
    figure_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)


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
    approved: bool = False
    transitions: list[str] = Field(default_factory=list)
    asset_warnings: list[str] = Field(default_factory=list)
    spec: PptSpec | None = None
    artifact: Artifact | None = None
    qa_issues: list[QaIssue] = Field(default_factory=list)
    repair_attempts: int = 0
