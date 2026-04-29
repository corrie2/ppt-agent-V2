from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ppt_agent.agent.skill_loader import LoadedUserSkill, load_user_skills
from ppt_agent.agent.skill_registry import SkillDefinition, SkillRegistry
from ppt_agent.shell.session import ShellSession


class UserSkillInput(BaseModel):
    model_config = ConfigDict(extra="allow")


def reload_user_skills(registry: SkillRegistry, *, session: ShellSession) -> list[str]:
    loaded = load_user_skills(session.cwd)
    session.user_skill_records = [_record(skill) for skill in loaded]
    previous_enabled = set(session.enabled_user_skills)
    session.available_user_skills = [skill.name for skill in loaded if skill.enabled and skill.manifest]
    session.enabled_user_skills = [name for name in session.enabled_user_skills if name in session.available_user_skills]
    warnings: list[str] = []
    removed = sorted(previous_enabled - set(session.enabled_user_skills))
    if removed:
        warnings.append(f"Disabled missing user skills: {', '.join(removed)}.")
    registered_names = set(registry.names())
    seen_user_names: set[str] = set()

    for skill in loaded:
        if not skill.enabled or not skill.manifest:
            continue
        if skill.name in registered_names:
            existing = registry.get(skill.name)
            if existing.source == "built-in":
                warnings.append(f"Skipping user skill '{skill.name}': built-in skill cannot be overridden.")
                continue
            if existing.source != "built-in":
                continue
        if skill.name in seen_user_names:
            warnings.append(f"Skipping duplicate user skill '{skill.name}': project skill takes precedence.")
            continue
        registry.register(user_skill_definition(skill))
        seen_user_names.add(skill.name)
    return warnings


def user_skill_definition(skill: LoadedUserSkill) -> SkillDefinition:
    manifest = skill.manifest
    assert manifest is not None
    return SkillDefinition(
        name=manifest.name,
        description=manifest.description,
        input_schema=UserSkillInput,
        callable=lambda **kwargs: invoke_markdown_skill(skill, **kwargs),
        is_read_only=manifest.is_read_only,
        requires_approval=manifest.requires_approval,
        max_result_chars=manifest.max_result_chars,
        source=skill.source,
        skill_type=manifest.type,
        when_to_use=manifest.when_to_use,
        path=str(skill.path),
        enabled=skill.enabled,
        validation_errors=skill.validation_errors,
        is_claude_compatible=skill.is_claude_compatible,
        skill_root=str(skill.skill_root) if skill.skill_root else None,
        skill_md_path=str(skill.skill_md_path) if skill.skill_md_path else None,
        assets_dir=str(skill.assets_dir) if skill.assets_dir else None,
        references_dir=str(skill.references_dir) if skill.references_dir else None,
        scripts_dir=str(skill.scripts_dir) if skill.scripts_dir else None,
        raw_frontmatter=skill.raw_frontmatter,
        allowed_tools=manifest.allowed_tools,
        security_warnings=skill.security_warnings,
    )


def invoke_markdown_skill(skill: LoadedUserSkill, **kwargs: Any) -> dict[str, Any]:
    manifest = skill.manifest
    assert manifest is not None
    return {
        "ok": True,
        "message": f"Loaded markdown skill: {manifest.name}",
        "reply": f"Loaded markdown skill: {manifest.name}",
        "skill_name": manifest.name,
        "skill_markdown": skill.markdown,
        "allowed_builtin_skills": manifest.allowed_builtin_skills,
        "arguments": kwargs,
        "data": {
            "name": manifest.name,
            "markdown": skill.markdown,
            "allowed_builtin_skills": manifest.allowed_builtin_skills,
            "claude_compatible": manifest.claude_compatible,
            "argument_hint": manifest.argument_hint,
            "paths": manifest.paths,
            "preferred_model": manifest.preferred_model,
            "effort": manifest.effort,
            "allowed_tools": manifest.allowed_tools,
            "security_warnings": skill.security_warnings,
            "arguments": kwargs,
        },
        "next_suggested_action": "continue_agent_loop",
    }


def _record(skill: LoadedUserSkill) -> dict[str, Any]:
    manifest = skill.manifest
    return {
        "name": skill.name,
        "type": manifest.type if manifest else "unknown",
        "source": skill.source,
        "path": str(skill.path),
        "skill_md_path": str(skill.skill_md_path) if skill.skill_md_path else None,
        "manifest_path": str(skill.manifest_path) if skill.manifest_path else None,
        "description": manifest.description if manifest else "",
        "when_to_use": manifest.when_to_use if manifest else None,
        "allowed_builtin_skills": manifest.allowed_builtin_skills if manifest else [],
        "input_schema": manifest.input_schema if manifest else {},
        "requires_approval": manifest.requires_approval if manifest else False,
        "is_read_only": manifest.is_read_only if manifest else True,
        "enabled": skill.enabled,
        "validation_errors": skill.validation_errors,
        "claude_compatible": skill.is_claude_compatible,
        "skill_root": str(skill.skill_root) if skill.skill_root else str(skill.path),
        "skill_md_path": str(skill.skill_md_path) if skill.skill_md_path else None,
        "assets_dir": str(skill.assets_dir) if skill.assets_dir else None,
        "references_dir": str(skill.references_dir) if skill.references_dir else None,
        "scripts_dir": str(skill.scripts_dir) if skill.scripts_dir else None,
        "raw_frontmatter": skill.raw_frontmatter,
        "argument_hint": manifest.argument_hint if manifest else None,
        "paths": manifest.paths if manifest else [],
        "preferred_model": manifest.preferred_model if manifest else None,
        "effort": manifest.effort if manifest else None,
        "allowed_tools": manifest.allowed_tools if manifest else [],
        "security_warnings": skill.security_warnings,
    }
