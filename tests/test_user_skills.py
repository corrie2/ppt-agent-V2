from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from ppt_agent.agent.chat_agent import ChatAgent
from ppt_agent.agent.skill_loader import load_user_skills
from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import register_default_skills
from ppt_agent.agent.user_skills import reload_user_skills
from ppt_agent.cli.main import app
from ppt_agent.shell.app import run_shell
from ppt_agent.shell.session import ShellSession


def _write_json_skill(base: Path, name: str = "paper-teaching-deck", description: str = "Generate a teaching deck.") -> Path:
    skill_dir = base / ".ppt-agent" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": name,
                "description": description,
                "when_to_use": "Use for teaching decks.",
                "type": "markdown",
                "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}},
                "allowed_builtin_skills": ["scan_workspace", "generate_plan", "build_ppt"],
                "requires_approval": False,
                "is_read_only": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (skill_dir / "skill.md").write_text("# Skill\n\nFull workflow instructions.\n", encoding="utf-8")
    return skill_dir


def _write_claude_skill(base: Path, name: str = "claude-deck") -> Path:
    skill_dir = base / ".ppt-agent" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "assets").mkdir()
    (skill_dir / "references").mkdir()
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "build.ps1").write_text("New-Item pwned.txt", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        """---
description: Generate a Claude-style teaching deck.
when_to_use: Use when a user wants a research-paper teaching deck.
allowed-tools: Read, Write, Edit, Bash
argument-hint: topic and audience
paths:
  - assets/**
  - references/**
model: opus
effort: high
---

# Claude Full Instructions

Use the source paper to prepare a graduate-level deck.
""",
        encoding="utf-8",
    )
    return skill_dir


def test_loads_user_skill_from_project_skill_json(tmp_path):
    _write_json_skill(tmp_path)

    skills = load_user_skills(tmp_path)

    assert len(skills) == 1
    assert skills[0].enabled is True
    assert skills[0].manifest.name == "paper-teaching-deck"


def test_loads_user_skill_from_markdown_frontmatter(tmp_path):
    skill_dir = tmp_path / ".ppt-agent" / "skills" / "frontmatter-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(
        """---
name: frontmatter-skill
description: Loaded from frontmatter.
when_to_use: Use for frontmatter tests.
type: markdown
requires_approval: false
is_read_only: true
allowed_builtin_skills:
  - scan_workspace
  - generate_plan
---

# Frontmatter Skill
""",
        encoding="utf-8",
    )

    skills = load_user_skills(tmp_path)

    assert skills[0].enabled is True
    assert skills[0].manifest.description == "Loaded from frontmatter."
    assert "generate_plan" in skills[0].manifest.allowed_builtin_skills


def test_loads_claude_skill_from_skill_md_frontmatter(tmp_path):
    _write_claude_skill(tmp_path)

    skills = load_user_skills(tmp_path)
    skill = skills[0]

    assert skill.enabled is True
    assert skill.name == "claude-deck"
    assert skill.is_claude_compatible is True
    assert skill.skill_md_path and skill.skill_md_path.name == "SKILL.md"
    assert skill.assets_dir is not None
    assert skill.references_dir is not None
    assert skill.scripts_dir is not None
    assert skill.raw_frontmatter["allowed-tools"] == "Read, Write, Edit, Bash"
    assert "scan_workspace" in skill.manifest.allowed_builtin_skills
    assert "generate_plan" in skill.manifest.allowed_builtin_skills
    assert "Bash" in skill.manifest.allowed_tools
    assert skill.security_warnings


def test_skill_json_overrides_claude_frontmatter(tmp_path):
    skill_dir = _write_claude_skill(tmp_path)
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": "overridden",
                "description": "Manifest wins.",
                "when_to_use": "Use override.",
                "type": "markdown",
                "allowed_builtin_skills": ["show_current_plan"],
            }
        ),
        encoding="utf-8",
    )

    skills = load_user_skills(tmp_path)

    assert skills[0].name == "overridden"
    assert skills[0].manifest.description == "Manifest wins."
    assert skills[0].manifest.allowed_builtin_skills == ["show_current_plan"]


def test_invalid_and_executable_user_skills_do_not_register(tmp_path):
    invalid = tmp_path / ".ppt-agent" / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "skill.md").write_text("# Missing manifest", encoding="utf-8")
    executable = tmp_path / ".ppt-agent" / "skills" / "exec"
    executable.mkdir(parents=True)
    (executable / "skill.json").write_text(
        json.dumps({"name": "exec", "description": "bad", "type": "executable"}),
        encoding="utf-8",
    )
    (executable / "skill.md").write_text("# Exec", encoding="utf-8")
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    reload_user_skills(registry, session=session)

    assert "invalid" not in registry.names()
    assert "exec" not in registry.names()
    assert any(record["name"] == "exec" and not record["enabled"] for record in session.user_skill_records)


def test_user_skill_registers_and_invocation_loads_markdown_only_then(tmp_path):
    _write_json_skill(tmp_path, description="Short summary only.")
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    prompt = ChatAgent()._system_prompt(registry)
    assert "Short summary only." in prompt
    assert "Full workflow instructions." not in prompt

    result = registry.invoke("paper-teaching-deck", topic="SIEVE")
    assert "Full workflow instructions." in result["skill_markdown"]
    assert result["next_suggested_action"] == "continue_agent_loop"


def test_shell_skills_commands_list_inspect_reload_and_paths(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    _write_json_skill(tmp_path)

    inputs = iter(["2", "0", "/skills reload", "/skills list", "/skills inspect paper-teaching-deck", "/skills paths", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "Skills reloaded." in text
    assert "paper-teaching-deck" in text
    assert "allowed_builtin_skills" in text
    assert ".ppt-agent" in text


def test_shell_inspect_shows_claude_skill_metadata(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    _write_claude_skill(tmp_path)

    inputs = iter(["2", "0", "/skills reload", "/skills list", "/skills inspect claude-deck", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "claude-compatible:yes" in text
    assert "SKILL.md:" in text
    assert "assets: exists" in text
    assert "references: exists" in text
    assert "ignored unsupported Claude allowed-tools" in text


def test_cli_skill_init_and_validate(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        result = runner.invoke(app, ["skill", "init", "demo-skill"])
        assert result.exit_code == 0
        assert Path(".ppt-agent/skills/demo-skill/skill.md").exists()
        assert Path(".ppt-agent/skills/demo-skill/skill.json").exists()

        validate = runner.invoke(app, ["skill", "validate", "demo-skill"])
        assert validate.exit_code == 0
        assert "Validation OK" in validate.output


def test_cli_skill_add_imports_local_claude_skill(tmp_path):
    source = _write_claude_skill(tmp_path / "source-root")
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        result = runner.invoke(app, ["skill", "add", str(source), "--name", "imported-claude"])

        assert result.exit_code == 0
        assert Path(".ppt-agent/skills/imported-claude/SKILL.md").exists()
        assert Path(".ppt-agent/skills/imported-claude/assets").exists()
        assert Path(".ppt-agent/skills/imported-claude/references").exists()


def test_cli_skill_add_imports_git_skill_with_mock_clone(tmp_path, monkeypatch):
    def fake_clone(args, check):
        assert args[:4] == ["git", "clone", "--depth", "1"]
        target = Path(args[-1])
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            """---
description: Git skill.
allowed-tools: Read
---

# Git Skill
""",
            encoding="utf-8",
        )

    monkeypatch.setattr("ppt_agent.cli.main.subprocess.run", fake_clone)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        result = runner.invoke(app, ["skill", "add", "https://github.com/example/skill.git", "--name", "git-skill"])

        assert result.exit_code == 0
        assert Path(".ppt-agent/skills/git-skill/SKILL.md").exists()


def test_cli_skill_add_git_clone_failure_cleans_temp_dir(tmp_path, monkeypatch):
    def fake_clone(args, check):
        target = Path(args[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        raise subprocess.CalledProcessError(returncode=128, cmd=args)

    monkeypatch.setattr("ppt_agent.cli.main.subprocess.run", fake_clone)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        result = runner.invoke(app, ["skill", "add", "https://github.com/example/missing.git"])

        assert result.exit_code == 1
        assert "git clone failed" in result.output
        temp_root = Path(".ppt-agent/tmp")
        assert not temp_root.exists() or not any(temp_root.iterdir())


def test_cli_skill_convert_generates_manifest_for_claude_skill(tmp_path):
    source = _write_claude_skill(tmp_path / "source-root")
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        result = runner.invoke(app, ["skill", "convert", str(source)])

        assert result.exit_code == 0
        assert Path(".ppt-agent/skills/claude-deck/SKILL.md").exists()
        manifest = json.loads(Path(".ppt-agent/skills/claude-deck/skill.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "claude-deck"
        assert manifest["claude_compatible"] is True


def test_user_skill_cannot_bypass_build_approval(tmp_path):
    _write_json_skill(tmp_path)
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    registry.invoke("paper-teaching-deck")

    assert session.latest_ppt_path is None
    assert session.pending_action is None
    assert registry.get("build_ppt").requires_approval is True


def test_claude_skill_prompt_lists_summary_and_invocation_loads_full_markdown(tmp_path):
    _write_claude_skill(tmp_path)
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    prompt = ChatAgent()._system_prompt(registry)
    assert "Generate a Claude-style teaching deck." in prompt
    assert "Claude Full Instructions" not in prompt

    result = registry.invoke("claude-deck", topic="SIEVE")

    assert "Claude Full Instructions" in result["skill_markdown"]
    assert not (tmp_path / "pwned.txt").exists()
    assert session.latest_ppt_path is None
    assert registry.get("build_ppt").requires_approval is True


def test_chat_agent_prompt_lists_only_enabled_user_skills(tmp_path):
    _write_json_skill(tmp_path, name="enabled-skill", description="Enabled summary.")
    _write_json_skill(tmp_path, name="disabled-skill", description="Disabled summary.")
    session = ShellSession.create(tmp_path)
    session.enabled_user_skills = ["enabled-skill"]
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    prompt = ChatAgent()._system_prompt(registry, enabled_user_skills=session.enabled_user_skills)

    assert "Enabled summary." in prompt
    assert "Disabled summary." not in prompt
