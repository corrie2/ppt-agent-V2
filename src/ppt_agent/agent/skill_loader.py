from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ppt_agent.agent.skill_manifest import UserSkillManifest


@dataclass
class LoadedUserSkill:
    name: str
    path: Path
    skill_md_path: Path | None
    manifest_path: Path | None
    manifest: UserSkillManifest | None = None
    markdown: str = ""
    source: str = "project"
    enabled: bool = False
    validation_errors: list[str] = field(default_factory=list)
    is_claude_compatible: bool = False
    skill_root: Path | None = None
    assets_dir: Path | None = None
    references_dir: Path | None = None
    scripts_dir: Path | None = None
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
    security_warnings: list[str] = field(default_factory=list)


def project_skill_dir(root: Path) -> Path:
    return root / ".ppt-agent" / "skills"


def user_skill_dir() -> Path:
    return Path.home() / ".ppt-agent" / "skills"


def skill_search_paths(root: Path) -> list[tuple[str, Path]]:
    return [
        ("project", project_skill_dir(root)),
        ("project-claude", root / ".claude" / "skills"),
        ("user", user_skill_dir()),
        ("user-claude", Path.home() / ".claude" / "skills"),
    ]


def load_user_skills(root: Path) -> list[LoadedUserSkill]:
    loaded: list[LoadedUserSkill] = []
    for source, base in skill_search_paths(root):
        if not base.exists():
            continue
        for skill_dir in sorted([path for path in base.iterdir() if path.is_dir()], key=lambda item: item.name.lower()):
            loaded.append(load_user_skill(skill_dir, source=source))
    return loaded


def _child_with_exact_name(directory: Path, name: str) -> Path | None:
    if not directory.exists() or not directory.is_dir():
        return None
    for child in directory.iterdir():
        if child.name == name:
            return child
    return None


def load_user_skill(path: Path, *, source: str = "project") -> LoadedUserSkill:
    skill_dir = path if path.is_dir() else path.parent
    claude_skill_md = _child_with_exact_name(skill_dir, "SKILL.md")
    legacy_skill_md = _child_with_exact_name(skill_dir, "skill.md")
    skill_md = claude_skill_md or legacy_skill_md or (skill_dir / "skill.md")
    manifest_path = skill_dir / "skill.json"
    errors: list[str] = []
    markdown = ""
    manifest_data: dict[str, Any] = {}

    if skill_md.exists():
        markdown = skill_md.read_text(encoding="utf-8")
    else:
        errors.append("missing skill.md")

    raw_frontmatter = _parse_frontmatter(markdown) if markdown else {}
    is_claude_compatible = claude_skill_md is not None
    security_warnings = _security_warnings(raw_frontmatter)

    if manifest_path.exists():
        try:
            manifest_data = {**_claude_frontmatter_to_manifest(raw_frontmatter, skill_dir), **json.loads(manifest_path.read_text(encoding="utf-8"))}
        except json.JSONDecodeError as exc:
            errors.append(f"invalid skill.json: {exc}")
    elif markdown:
        manifest_data = _claude_frontmatter_to_manifest(raw_frontmatter, skill_dir) if is_claude_compatible else raw_frontmatter
        if not manifest_data:
            errors.append("missing skill.json and skill.md frontmatter")
    else:
        errors.append("missing skill.json")

    manifest: UserSkillManifest | None = None
    name = manifest_data.get("name") or skill_dir.name
    if manifest_data:
        try:
            manifest = UserSkillManifest.model_validate(manifest_data)
            name = manifest.name
            if is_claude_compatible:
                manifest.claude_compatible = True
            if manifest.type == "executable":
                errors.append("executable user skills are not enabled")
        except ValidationError as exc:
            errors.extend(error["msg"] for error in exc.errors())

    enabled = manifest is not None and manifest.type == "markdown" and not errors
    return LoadedUserSkill(
        name=name,
        path=skill_dir,
        skill_md_path=skill_md if skill_md.exists() else None,
        manifest_path=manifest_path if manifest_path.exists() else None,
        manifest=manifest,
        markdown=_strip_frontmatter(markdown),
        source=source,
        enabled=enabled,
        validation_errors=errors,
        is_claude_compatible=is_claude_compatible or bool(manifest.claude_compatible if manifest else False),
        skill_root=skill_dir,
        assets_dir=skill_dir / "assets" if (skill_dir / "assets").exists() else None,
        references_dir=skill_dir / "references" if (skill_dir / "references").exists() else None,
        scripts_dir=skill_dir / "scripts" if (skill_dir / "scripts").exists() else None,
        raw_frontmatter=raw_frontmatter,
        security_warnings=security_warnings,
    )


def _parse_frontmatter(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}

    data: dict[str, Any] = {}
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value:
            data[key] = _parse_scalar(value)
            index += 1
            continue
        items: list[str] = []
        index += 1
        while index < end and lines[index].startswith("  - "):
            items.append(lines[index][4:].strip())
            index += 1
        data[key] = items
    return data


def _claude_frontmatter_to_manifest(frontmatter: dict[str, Any], skill_dir: Path) -> dict[str, Any]:
    if not frontmatter:
        return {}
    allowed_tools = _as_list(frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools"))
    mapped_tools = _map_allowed_tools(allowed_tools)
    return {
        "name": frontmatter.get("name") or skill_dir.name,
        "description": frontmatter.get("description", ""),
        "when_to_use": frontmatter.get("when_to_use") or frontmatter.get("when-to-use"),
        "type": "markdown",
        "input_schema": {"type": "object", "properties": {}},
        "allowed_builtin_skills": mapped_tools,
        "requires_approval": False,
        "is_read_only": True,
        "argument_hint": frontmatter.get("argument-hint") or frontmatter.get("argument_hint"),
        "paths": _as_list(frontmatter.get("paths")),
        "preferred_model": frontmatter.get("model"),
        "effort": frontmatter.get("effort"),
        "claude_compatible": True,
        "allowed_tools": allowed_tools,
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _map_allowed_tools(allowed_tools: list[str]) -> list[str]:
    mapped: list[str] = []
    for tool in allowed_tools:
        lowered = tool.lower()
        if lowered == "read":
            mapped.extend(["scan_workspace", "list_sources", "show_current_plan"])
        elif lowered in {"write", "edit"}:
            mapped.extend(["generate_plan", "revise_plan"])
    return sorted(set(mapped))


def _security_warnings(frontmatter: dict[str, Any]) -> list[str]:
    allowed_tools = _as_list(frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools"))
    risky = [tool for tool in allowed_tools if tool.lower() not in {"read", "write", "edit"}]
    if not risky:
        return []
    return [
        "ignored unsupported Claude allowed-tools: "
        + ", ".join(risky)
        + "; only safe built-in skill mappings are available"
    ]


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _strip_frontmatter(markdown: str) -> str:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return markdown
    return "\n".join(lines[end + 1 :]).lstrip()
