from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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


class SlideSpec(BaseModel):
    title: str
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


class PptSpec(BaseModel):
    title: str
    audience: str
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
