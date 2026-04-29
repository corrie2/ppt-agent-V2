from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field

SkillHandler = Callable[..., dict[str, Any]]


class EmptySkillInput(BaseModel):
    pass


class SkillResult(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    next_suggested_action: str | None = None


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    input_schema: type[BaseModel]
    callable: SkillHandler
    is_read_only: bool = False
    requires_approval: bool = False
    result_schema: type[BaseModel] | None = None
    max_result_chars: int = 12000
    source: str = "built-in"
    skill_type: str = "builtin"
    when_to_use: str | None = None
    path: str | None = None
    enabled: bool = True
    validation_errors: list[str] | None = None
    is_claude_compatible: bool = False
    skill_root: str | None = None
    skill_md_path: str | None = None
    assets_dir: str | None = None
    references_dir: str | None = None
    scripts_dir: str | None = None
    raw_frontmatter: dict[str, Any] | None = None
    allowed_tools: list[str] | None = None
    security_warnings: list[str] | None = None


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillDefinition:
        return self._skills[name]

    def names(self) -> list[str]:
        return list(self._skills)

    def validate_arguments(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = arguments or {}
        model = self.get(name).input_schema
        validated = model.model_validate(payload)
        return validated.model_dump(mode="json", exclude_none=True)

    def describe(self) -> list[dict[str, Any]]:
        descriptions: list[dict[str, Any]] = []
        for skill in self._skills.values():
            descriptions.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "input_schema": skill.input_schema.model_json_schema(),
                    "result_schema": (skill.result_schema or SkillResult).model_json_schema(),
                    "is_read_only": skill.is_read_only,
                    "requires_approval": skill.requires_approval,
                    "max_result_chars": skill.max_result_chars,
                    "source": skill.source,
                    "type": skill.skill_type,
                    "when_to_use": skill.when_to_use,
                    "path": skill.path,
                    "enabled": skill.enabled,
                    "validation_errors": skill.validation_errors or [],
                    "claude_compatible": skill.is_claude_compatible,
                    "skill_root": skill.skill_root,
                    "skill_md_path": skill.skill_md_path,
                    "assets_dir": skill.assets_dir,
                    "references_dir": skill.references_dir,
                    "scripts_dir": skill.scripts_dir,
                    "raw_frontmatter": skill.raw_frontmatter or {},
                    "allowed_tools": skill.allowed_tools or [],
                    "security_warnings": skill.security_warnings or [],
                }
            )
        return descriptions

    def invoke(self, name: str, **kwargs: Any) -> dict[str, Any]:
        arguments = self.validate_arguments(name, kwargs)
        result = self.get(name).callable(**arguments)
        return self._normalize_result(result)

    def _normalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if "ok" in result and "message" in result and "data" in result:
            return result
        data = {key: value for key, value in result.items() if key not in {"reply", "ok", "message", "warnings", "next_suggested_action"}}
        normalized = {
            "ok": result.get("ok", True),
            "message": result.get("message") or result.get("reply", ""),
            "data": result.get("data", data),
            "warnings": result.get("warnings", []),
            "next_suggested_action": result.get("next_suggested_action"),
        }
        return {**normalized, **result}
