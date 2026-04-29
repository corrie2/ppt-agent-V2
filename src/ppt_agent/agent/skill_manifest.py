from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class UserSkillManifest(BaseModel):
    name: str
    description: str
    when_to_use: str | None = None
    type: Literal["markdown", "executable"] = "markdown"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_builtin_skills: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    is_read_only: bool = True
    max_result_chars: int = 8000
    argument_hint: str | None = None
    paths: list[str] = Field(default_factory=list)
    preferred_model: str | None = None
    effort: str | None = None
    claude_compatible: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
