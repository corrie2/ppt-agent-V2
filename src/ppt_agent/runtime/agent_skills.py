from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from ppt_agent.agent.skill_loader import LoadedUserSkill, load_user_skills


AGENT_NAMES = {
    "supervisor",
    "brief_outline",
    "content",
    "design_chart",
    "qa",
    "render_review",
    "page_designer",
    "renderer_engineer",
    "visual_quality_evaluator",
    "page_generator",
}


class AgentSkillSummary(BaseModel):
    name: str
    description: str = ""
    when_to_use: str | None = None
    source: str
    path: str
    skill_md_path: str | None = None
    agent_scope: list[str] = Field(default_factory=list)
    markdown_excerpt: str = ""


class AgentSkillAssignments(BaseModel):
    supervisor_catalog: list[AgentSkillSummary] = Field(default_factory=list)
    assignments: dict[str, list[AgentSkillSummary]] = Field(default_factory=dict)

    def names_by_agent(self) -> dict[str, list[str]]:
        return {
            agent: [skill.name for skill in skills]
            for agent, skills in self.assignments.items()
        }


def assign_skills_to_agents(root: Path, *, max_excerpt_chars: int = 1200) -> AgentSkillAssignments:
    enabled_skills = [
        skill
        for skill in load_user_skills(root)
        if skill.enabled and skill.manifest is not None
    ]
    catalog = [_summary(skill, max_excerpt_chars=max_excerpt_chars) for skill in enabled_skills]
    assignments = {agent: [] for agent in sorted(AGENT_NAMES)}
    assignments["supervisor"] = list(catalog)

    for skill, summary in zip(enabled_skills, catalog, strict=False):
        scoped_agents = _explicit_scope(skill)
        if scoped_agents:
            target_agents = scoped_agents
        else:
            target_agents = _infer_scope(skill)
        for agent in target_agents:
            if agent == "supervisor" or agent not in assignments:
                continue
            assignments[agent].append(summary)

    assignments["page_generator"] = []
    _apply_manual_assignments(root, assignments, catalog)
    return AgentSkillAssignments(supervisor_catalog=catalog, assignments=assignments)


def skill_context_for_agent(assignments: AgentSkillAssignments, agent_name: str) -> str:
    skills = assignments.assignments.get(agent_name, [])
    if not skills:
        return "No skills assigned."
    parts = []
    for skill in skills:
        header = f"Skill: {skill.name}\nDescription: {skill.description}"
        if skill.when_to_use:
            header += f"\nWhen to use: {skill.when_to_use}"
        if skill.markdown_excerpt:
            header += f"\nInstructions:\n{skill.markdown_excerpt}"
        parts.append(header)
    return "\n\n---\n\n".join(parts)


def _summary(skill: LoadedUserSkill, *, max_excerpt_chars: int) -> AgentSkillSummary:
    manifest = skill.manifest
    assert manifest is not None
    markdown = " ".join(skill.markdown.split())
    if len(markdown) > max_excerpt_chars:
        markdown = markdown[: max_excerpt_chars - 3].rstrip() + "..."
    return AgentSkillSummary(
        name=manifest.name,
        description=manifest.description,
        when_to_use=manifest.when_to_use,
        source=skill.source,
        path=str(skill.path),
        skill_md_path=str(skill.skill_md_path) if skill.skill_md_path else None,
        agent_scope=_explicit_scope(skill),
        markdown_excerpt=markdown,
    )


def _explicit_scope(skill: LoadedUserSkill) -> list[str]:
    manifest = skill.manifest
    if manifest is None:
        return []
    return [agent for agent in manifest.agent_scope if agent in AGENT_NAMES]


def _infer_scope(skill: LoadedUserSkill) -> list[str]:
    manifest = skill.manifest
    assert manifest is not None
    text = " ".join(
        [
            skill.name,
            manifest.description,
            manifest.when_to_use or "",
            " ".join(manifest.allowed_builtin_skills),
            skill.markdown[:1000],
        ]
    ).lower()

    targets: set[str] = set()
    if _contains_any(text, ("outline", "structure", "story", "narrative", "audience", "brief", "research", "industry", "framework", "大纲", "结构", "受众", "研究")):
        targets.add("brief_outline")
    if _contains_any(text, ("write", "copy", "speaker", "notes", "tone", "executive", "content", "文案", "讲稿", "表达", "标题")):
        targets.add("content")
    if _contains_any(text, ("design", "visual", "chart", "layout", "theme", "brand", "color", "font", "图表", "视觉", "版式", "配色", "品牌")):
        targets.add("design_chart")
        targets.add("page_designer")
        targets.add("visual_quality_evaluator")
    if _contains_any(text, ("qa", "review", "check", "quality", "fact", "citation", "grammar", "审查", "检查", "质量", "事实", "错别字")):
        targets.add("qa")
        targets.add("visual_quality_evaluator")
    if _contains_any(text, ("render", "pptx", "powerpoint", "mapping", "slide xml", "blank slide", "渲染", "空白页", "映射")):
        targets.add("render_review")
        targets.add("renderer_engineer")
        targets.add("visual_quality_evaluator")
    if _contains_any(text, ("code", "script", "python", "renderer", "layout function", "代码", "脚本", "函数", "渲染器")):
        targets.add("renderer_engineer")

    if not targets:
        targets.update({"brief_outline", "content", "qa"})
    return sorted(targets)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _apply_manual_assignments(root: Path, assignments: dict[str, list[AgentSkillSummary]], catalog: list[AgentSkillSummary]) -> None:
    path = root / ".ppt-agent" / "agents" / "skills.json"
    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_name = {skill.name: skill for skill in catalog}
    for agent, skill_names in raw.items():
        if agent not in assignments or agent == "supervisor":
            continue
        if not isinstance(skill_names, list):
            continue
        selected = [by_name[name] for name in skill_names if isinstance(name, str) and name in by_name]
        assignments[agent] = selected if agent != "page_generator" else []
