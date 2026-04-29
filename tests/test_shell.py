from pathlib import Path

from ppt_agent.agent.chat_agent import ChatAgent, RouterDecision, SkillCall
from ppt_agent.agent.skill_registry import EmptySkillInput, SkillDefinition, SkillRegistry, SkillResult
from ppt_agent.agent.skills import BuildPptInput, GeneratePlanInput, build_ppt_skill, generate_plan_skill, register_default_skills
from ppt_agent.agent.user_skills import reload_user_skills
from ppt_agent.nodes.qa import qa_node
from ppt_agent.runtime.html_deck import validate_html_deck
from ppt_agent.runtime.workspace import scan_workspace
from ppt_agent.shell.app import run_shell
from ppt_agent.shell.session import (
    DEFAULT_ASSISTANT_MODEL,
    DEFAULT_ASSISTANT_PROVIDER,
    PendingAction,
    PendingUserRequest,
    ShellSession,
)
from ppt_agent.storage.llm_settings import save_api_key
from ppt_agent.storage.plan_io import read_plan_document


class _FakeGraph:
    def invoke(self, state: dict) -> dict:
        return {
            "mode": "plan",
            "approved": False,
            "transitions": ["plan", "asset_plan", "asset_resolve"],
            "spec": {
                "title": "AI Sales Enablement",
                "audience": "leadership",
                "theme": "executive_blue",
                "slides": [
                    {
                        "title": "Cover",
                        "objective": "Frame the proposal.",
                        "core_message": "Approve the AI sales enablement pilot.",
                        "bullets": ["Why now", "What changes", "Decision needed"],
                        "supporting_points": ["Revenue productivity", "Manager visibility"],
                        "speaker_notes": "",
                        "visual_type": "hero_image",
                        "image_query": "sales leadership meeting",
                        "image_prompt": "",
                        "image_caption": "",
                        "image_rationale": "",
                        "layout_hint": "title_cover",
                        "style_tags": ["executive"],
                        "visual_spec": {"asset_kind": "image", "visual_required": True},
                        "resolved_asset": {},
                    }
                ],
            },
        }


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def respond(self, session: ShellSession, message: str, registry: SkillRegistry | None = None) -> RouterDecision:
        self.calls.append(message)
        return RouterDecision(
            reply="Scanning and planning.",
            skill_calls=[
                SkillCall(name="scan_workspace", arguments={"max_depth": 3}),
                SkillCall(name="generate_plan", arguments={"topic": message}),
            ],
        )


class _ScanOnlyAgent:
    def __init__(self, request: PendingUserRequest) -> None:
        self.request = request
        self.calls: list[str] = []

    def respond(self, session: ShellSession, message: str, registry: SkillRegistry | None = None) -> RouterDecision:
        self.calls.append(message)
        session.pending_user_request = self.request
        return RouterDecision(
            reply="I will scan first.",
            skill_calls=[SkillCall(name="scan_workspace", arguments={"max_depth": 3})],
        )


class _FixedDateTime:
    @classmethod
    def now(cls):
        from datetime import datetime

        return datetime(2026, 4, 25, 9, 30, 45)


def _register_source_skills(registry: SkillRegistry, session: ShellSession) -> None:
    registry.register(
        SkillDefinition(
            name="scan_workspace",
            description="scan",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {
                "files": [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)],
                "reply": "scan done",
            },
        )
    )
    registry.register(
        SkillDefinition(
            name="list_sources",
            description="list",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {
                "files": [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)],
                "reply": "Found source files.",
            },
        )
    )


def _write_guizang_skill(root: Path) -> Path:
    skill_dir = root / ".ppt-agent" / "skills" / "guizang-ppt-skill"
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "references").mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
description: Generate an editorial single HTML magazine deck.
allowed-tools: Read, Write
---

# Guizang PPT Skill

Build a horizontal editorial HTML deck.
""",
        encoding="utf-8",
    )
    (skill_dir / "assets" / "template.html").write_text(
        "<!doctype html><html><head><title>{{title}}</title></head><body class=\"{{theme}}\"><div id=\"deck\"><!-- SLIDES_HERE --></div><script>document.addEventListener('keydown',()=>{})</script></body></html>",
        encoding="utf-8",
    )
    (skill_dir / "references" / "style.md").write_text("Use editorial pacing.", encoding="utf-8")
    return skill_dir


def test_shell_startup_can_enable_ai_assistant_mode(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["1", "/status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "Enable AI assistant mode?" in text
    assert "AI assistant mode enabled." in text
    assert session.assistant_enabled is True
    assert session.assistant_provider == DEFAULT_ASSISTANT_PROVIDER
    assert session.assistant_model == DEFAULT_ASSISTANT_MODEL
    assert "mode: ai assistant" in text


def test_shell_startup_can_enter_manual_cli_mode(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["2", "/status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "Manual CLI mode enabled." in text
    assert session.assistant_enabled is False
    assert "mode: manual cli" in text


def test_ai_mode_routes_natural_language_to_chat_agent(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    agent = _FakeAgent()
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: {"plan_path": str(session.output_dir / "shell-plan.json"), "reply": "plan ready", "sources": []},
        )
    )

    inputs = iter(["1", "create a sales enablement deck", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=registry,
    )

    assert agent.calls == ["create a sales enablement deck"]


def test_ai_mode_model_identity_query_uses_session_config_without_calling_agent(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    agent = _FakeAgent()

    inputs = iter(["1", "你是什么模型", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "当前 AI assistant mode 已开启。" in text
    assert "assistant enabled: true" in text
    assert f"Provider: {DEFAULT_ASSISTANT_PROVIDER}" in text
    assert f"Model: {DEFAULT_ASSISTANT_MODEL}" in text
    assert "Key configured: yes" in text


def test_ai_mode_english_model_identity_query_uses_session_config(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["1", "what model are you", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "assistant enabled: true" in text
    assert f"Provider: {DEFAULT_ASSISTANT_PROVIDER}" in text
    assert f"Model: {DEFAULT_ASSISTANT_MODEL}" in text
    assert "Key configured: no" in text


def test_datetime_query_uses_local_handler_without_calling_agent(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.shell.commands.datetime", _FixedDateTime)
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["1", "\u4eca\u5929\u662f\u51e0\u53f7", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "\u4eca\u5929\u662f 2026-04-25" in text
    assert "\u5f53\u524d\u65f6\u95f4\uff1a09:30:45" in text


def test_manual_mode_natural_language_does_not_call_llm(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["2", "create a sales enablement deck", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "Current mode is manual CLI." in text


def test_ai_on_command_enables_ai_mode(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)

    inputs = iter(["2", "/ai on", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert session.assistant_enabled is True
    assert "AI assistant mode enabled." in text
    assert f"provider/model: {DEFAULT_ASSISTANT_PROVIDER}/{DEFAULT_ASSISTANT_MODEL}" in text


def test_ai_off_command_disables_ai_mode(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)

    inputs = iter(["1", "/ai off", "/status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert session.assistant_enabled is False
    assert "Manual CLI mode enabled." in text
    assert "mode: manual cli" in text


def test_ai_status_shows_provider_model_and_key_status(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)

    inputs = iter(["2", "/ai status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "assistant enabled: false" in text
    assert f"provider: {DEFAULT_ASSISTANT_PROVIDER}" in text
    assert f"model: {DEFAULT_ASSISTANT_MODEL}" in text
    assert "key configured: no" in text


def test_status_shows_current_mode_and_assistant_details(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)

    inputs = iter(["1", "/status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "mode: ai assistant" in text
    assert f"provider: {DEFAULT_ASSISTANT_PROVIDER}" in text
    assert f"model: {DEFAULT_ASSISTANT_MODEL}" in text
    assert "key configured: no" in text


def test_missing_deepseek_key_shows_clear_prompt_without_crash(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)

    inputs = iter(["1", "create a deck", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=ChatAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "No API key configured for deepseek." in text
    assert "ppt-agent llm set-key deepseek --api-key <your-key>" in text
    assert "AI assistant mode is enabled, but no API key is configured for deepseek." in text


def test_workspace_scanner_discovers_supported_files(tmp_path):
    (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    (tmp_path / "c.pptx").write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "d.md").write_text("# doc", encoding="utf-8")

    files = scan_workspace(tmp_path, max_depth=3)
    names = {item.name for item in files}

    assert {"a.pdf", "b.json", "c.pptx", "d.md"} <= names
    assert any(item.relative_path == "a.pdf" for item in files)
    assert all(item.modified_time for item in files)


def test_workspace_scanner_skips_unreadable_files(monkeypatch, tmp_path):
    good = tmp_path / "good.pdf"
    bad = tmp_path / "bad.pdf"
    good.write_text("good", encoding="utf-8")
    bad.write_text("bad", encoding="utf-8")
    original_stat = Path.stat

    def fake_stat(path: Path, *args, **kwargs):
        if path == bad:
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    files = scan_workspace(tmp_path, max_depth=3)

    assert {item.name for item in files} == {"good.pdf"}


def test_agent_routes_request_to_scan_and_generate_plan_when_one_pdf_selected(tmp_path):
    agent = ChatAgent()
    session = ShellSession.create(tmp_path)
    session.enable_assistant()
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    pdf_path = session.input_dir / "sales.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(pdf_path)]

    decision = agent._route_with_fallback(session, "根据当前目录里的 PDF，做一份 10 页的 AI 销售赋能方案，先给我计划再生成", registry=None)

    names = [call.name for call in decision.skill_calls]
    assert "generate_plan" in names
    generate_plan = next(call for call in decision.skill_calls if call.name == "generate_plan")
    assert generate_plan.arguments["slides"] == 10
    assert generate_plan.arguments["sources"] == [str(pdf_path)]


def test_agent_asks_user_to_select_when_multiple_pdfs_exist(tmp_path):
    agent = ChatAgent()
    session = ShellSession.create(tmp_path)
    session.enable_assistant()
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    (session.input_dir / "a.pdf").write_text("a", encoding="utf-8")
    (session.input_dir / "b.pdf").write_text("b", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]

    decision = agent._route_with_fallback(session, "根据当前目录里的 PDF 生成一份方案", registry=None)

    assert not any(call.name == "generate_plan" for call in decision.skill_calls)
    assert any(call.name == "list_sources" for call in decision.skill_calls)
    assert "/select 1,2" in decision.reply


def test_generate_plan_skill_writes_plan_to_output_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    session = ShellSession.create(tmp_path)

    result = generate_plan_skill(session=session, topic="AI Sales Enablement")

    assert "plan_path" in result
    assert Path(result["plan_path"]).exists()
    assert Path(result["plan_path"]).parent == session.output_dir
    assert session.latest_plan_path == result["plan_path"]
    assert "Source PDFs: none selected." in result["plan_summary"]


def test_ingest_sources_writes_source_store_and_digest(tmp_path):
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text(
        "Paper Title. Abstract This paper studies retrieval. Problem search is expensive. "
        "Method builds a selective graph. Experiments evaluate latency. Results improve recall. "
        "Limitations include memory overhead.",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    result = registry.invoke("ingest_sources", sources=[str(source)])

    source_id = result["indexed"][0]["source_id"]
    source_dir = tmp_path / ".ppt-agent" / "data" / "sources" / source_id
    assert (source_dir / "metadata.json").exists()
    assert (source_dir / "text.jsonl").exists()
    assert (source_dir / "chunks.jsonl").exists()
    assert (source_dir / "digest.json").exists()
    digest = result["indexed"][0]["digest"]
    for field in ("title", "abstract", "problem", "method", "experiments", "results", "limitations"):
        assert digest[field] != "unknown"


def test_select_pdf_indexes_source_store(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text("Abstract source. Problem search. Method graph. Experiments latency. Results faster. Limitations memory.", encoding="utf-8")
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    inputs = iter(["2", "/files", "/select 1", "/exit"])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)

    source_root = tmp_path / ".ppt-agent" / "data" / "sources"
    assert source_root.exists()
    assert list(source_root.glob("*/digest.json"))


def test_generate_plan_skill_uses_source_store_digest(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state: dict) -> dict:
            captured.update(state)
            return _FakeGraph().invoke(state)

    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: FakeGraph())
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text(
        "Actual Paper. Abstract grounded abstract. Problem grounded problem. Method grounded method. "
        "Experiments grounded experiments. Results grounded results. Limitations grounded limitations.",
        encoding="utf-8",
    )

    result = generate_plan_skill(session=session, topic="Paper explainer", sources=[str(source)])
    document = read_plan_document(Path(result["plan_path"]))

    assert document.payload["source_digest"]["sources"][0]["path"] == str(source.resolve())
    assert "grounded abstract" in document.payload["source_digest"]["sources"][0]["abstract"]
    assert document.payload["request"]["source_context"]
    assert document.payload["source_digest"]["retrieved_context"]
    assert captured["intent"]["source_digest"]["sources"]
    assert captured["intent"]["source_context"]
    assert document.spec.grounding_warnings == document.payload["grounding_warnings"]
    memory_path = tmp_path / ".ppt-agent" / "data" / "memory" / "events.jsonl"
    assert memory_path.exists()
    assert "plan_generated" in memory_path.read_text(encoding="utf-8")


def test_retrieve_source_context_returns_indexed_chunks(tmp_path):
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text("Alpha context. Method graph filtering. Results graph latency improves.", encoding="utf-8")
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    registry.invoke("ingest_sources", sources=[str(source)])

    result = registry.invoke("retrieve_source_context", sources=[str(source)], query="graph latency", limit=2)

    assert result["contexts"]
    assert "graph" in result["contexts"][0]["text"].lower()


def test_generate_plan_skill_does_not_use_assistant_planner_without_workspace_key(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state: dict) -> dict:
            captured.update(state)
            return _FakeGraph().invoke(state)

    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: FakeGraph())
    session = ShellSession.create(tmp_path)
    session.enable_assistant()

    generate_plan_skill(session=session, topic="AI Sales Enablement")

    assert captured["planner_provider"] is None
    assert captured["planner_model"] is None


def test_build_ppt_skill_can_be_called(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    session = ShellSession.create(tmp_path)
    plan_result = generate_plan_skill(session=session, topic="AI Sales Enablement")

    result = build_ppt_skill(session=session, plan_path=plan_result["plan_path"], output_path=str(tmp_path / "deck.pptx"))

    assert Path(result["ppt_path"]).exists()
    assert session.latest_ppt_path == result["ppt_path"]
    assert session.last_build_status == "completed"


def test_build_status_query_returns_completed_paths_and_sources(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(selected)]
    plan_result = generate_plan_skill(session=session, topic="AI Sales Enablement", sources=[str(selected)])
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="build_ppt",
            description="build",
            input_schema=BuildPptInput,
            callable=lambda **kwargs: build_ppt_skill(session=session, **kwargs),
        )
    )
    session.pending_action = PendingAction(
        skill_name="build_ppt",
        arguments={"plan_path": plan_result["plan_path"], "output_path": str(session.output_dir / "shell-deck.pptx")},
        description="build pending ppt",
    )
    agent = _FakeAgent()

    inputs = iter(["1", "/approve", "\u505a\u597d\u4e86\u5417", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=registry,
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "\u5df2\u7ecf\u5b8c\u6210\u3002" in text
    assert str(session.latest_ppt_path) in text
    assert str(session.latest_plan_path) in text
    assert "SIEVE.pdf" in text


def test_shell_does_not_build_without_approve_and_uses_output_dir(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()

    _register_source_skills(registry, session)
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: {"plan_path": str(session.output_dir / "shell-plan.json"), "reply": "plan ready"},
        )
    )

    build_called = {"value": False}

    def fake_build(**kwargs):
        build_called["value"] = True
        return {"ppt_path": str(session.output_dir / "shell-deck.pptx"), "reply": "built"}

    registry.register(
        SkillDefinition(
            name="build_ppt",
            description="build",
            input_schema=BuildPptInput,
            callable=fake_build,
        )
    )

    inputs = iter(["1", "根据当前目录里的 PDF 做一份方案", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert build_called["value"] is False
    assert session.pending_action is not None
    assert Path(session.pending_action.arguments["output_path"]).parent == session.output_dir


def test_pending_action_accepts_continue_as_approve(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()

    build_called = {"value": False}

    def fake_build(**kwargs):
        build_called["value"] = True
        return {"ppt_path": str(session.output_dir / "shell-deck.pptx"), "reply": "Wrote PPTX to output/shell-deck.pptx."}

    registry.register(
        SkillDefinition(
            name="build_ppt",
            description="build",
            input_schema=BuildPptInput,
            callable=fake_build,
        )
    )
    session.pending_action = PendingAction(
        skill_name="build_ppt",
        arguments={"plan_path": "dummy.json", "output_path": str(session.output_dir / "shell-deck.pptx")},
        description="build pending ppt",
    )

    inputs = iter(["2", "继续", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert build_called["value"] is True
    assert "Wrote PPTX to output/shell-deck.pptx." in "\n".join(outputs)


def test_pending_action_accepts_yes_as_approve(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()

    build_called = {"value": False}

    def fake_build(**kwargs):
        build_called["value"] = True
        return {"ppt_path": str(session.output_dir / "shell-deck.pptx"), "reply": "built"}

    registry.register(
        SkillDefinition(
            name="build_ppt",
            description="build",
            input_schema=BuildPptInput,
            callable=fake_build,
        )
    )
    session.pending_action = PendingAction(
        skill_name="build_ppt",
        arguments={"plan_path": "dummy.json", "output_path": str(session.output_dir / "shell-deck.pptx")},
        description="build pending ppt",
    )

    inputs = iter(["2", "yes", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert build_called["value"] is True


def test_approve_writes_build_result_back_to_session(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    ppt_path = str(session.output_dir / "shell-deck.pptx")

    registry.register(
        SkillDefinition(
            name="build_ppt",
            description="build",
            input_schema=BuildPptInput,
            callable=lambda **kwargs: {"ppt_path": ppt_path, "reply": f"Wrote PPTX to {ppt_path}."},
        )
    )
    session.pending_action = PendingAction(
        skill_name="build_ppt",
        arguments={"plan_path": "dummy.json", "output_path": ppt_path},
        description="build pending ppt",
    )

    inputs = iter(["2", "/approve", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert session.latest_ppt_path == ppt_path
    assert session.last_build_status == "completed"
    assert session.pending_action is None


def test_continue_without_pending_action_does_not_trigger_approve(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["2", "继续", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    assert agent.calls == []
    assert "Current mode is manual CLI." in "\n".join(outputs)


def test_continue_without_pending_action_returns_completed_status_when_latest_ppt_exists(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    session.latest_ppt_path = str(session.output_dir / "shell-deck.pptx")
    session.last_build_status = "completed"
    agent = _FakeAgent()

    inputs = iter(["1", "\u7ee7\u7eed", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "\u5f53\u524d\u6ca1\u6709\u5f85\u786e\u8ba4\u64cd\u4f5c" in text
    assert str(session.latest_ppt_path) in text


def test_continue_without_pending_action_and_without_results_guides_next_steps(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["1", "\u7ee7\u7eed", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "/files" in text
    assert "/select" in text


def test_pending_action_blocks_chat_agent_for_unrelated_input(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()
    session.pending_action = PendingAction(
        skill_name="build_ppt",
        arguments={"plan_path": "dummy.json", "output_path": str(session.output_dir / "shell-deck.pptx")},
        description="build pending ppt",
    )

    inputs = iter(["1", "其他无关输入", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "There is a pending build action. Please confirm with /approve or cancel with /cancel." in text


def test_select_command_chooses_multiple_pdfs(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    (session.input_dir / "first.pdf").write_text("1", encoding="utf-8")
    (session.input_dir / "second.pdf").write_text("2", encoding="utf-8")
    registry = SkillRegistry()
    _register_source_skills(registry, session)

    inputs = iter(["2", "/files", "/select 1,2", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert len(session.selected_sources) == 2
    assert "Selected PDFs: first.pdf, second.pdf" in "\n".join(outputs)


def test_files_output_contains_name_size_modified_time_and_pages(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    files = [
        {
            "name": "first.pdf",
            "file_type": "pdf",
            "path": str(session.input_dir / "first.pdf"),
            "size": 2048,
            "relative_path": "first.pdf",
            "modified_time": "2026-04-25 10:30",
            "page_count": 12,
        }
    ]
    registry.register(
        SkillDefinition(
            name="scan_workspace",
            description="scan",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {"files": files, "reply": "scan done"},
        )
    )
    registry.register(
        SkillDefinition(
            name="list_sources",
            description="list",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {"files": files, "reply": "Found source files."},
        )
    )

    inputs = iter(["2", "/files", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "PDF file" in text
    assert "first.pdf" in text
    assert "2.0 KB" in text
    assert "2026-04-25 10:30" in text
    assert "pages:12" in text


def test_files_output_uses_unknown_when_page_count_missing(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    files = [
        {
            "name": "unknown-pages.pdf",
            "file_type": "pdf",
            "path": str(session.input_dir / "unknown-pages.pdf"),
            "size": 512,
            "relative_path": "unknown-pages.pdf",
            "modified_time": "2026-04-25 10:31",
            "page_count": None,
        }
    ]
    registry.register(
        SkillDefinition(
            name="scan_workspace",
            description="scan",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {"files": files, "reply": "scan done"},
        )
    )
    registry.register(
        SkillDefinition(
            name="list_sources",
            description="list",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {"files": files, "reply": "Found source files."},
        )
    )

    inputs = iter(["2", "/files", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert "pages:unknown" in "\n".join(outputs)


def test_files_output_uses_unknown_when_page_count_is_zero(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    files = [
        {
            "name": "zero-pages.pdf",
            "file_type": "pdf",
            "path": str(session.input_dir / "zero-pages.pdf"),
            "size": 512,
            "relative_path": "zero-pages.pdf",
            "modified_time": "2026-04-25 10:31",
            "page_count": 0,
        }
    ]
    registry.register(
        SkillDefinition(
            name="scan_workspace",
            description="scan",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {"files": files, "reply": "scan done"},
        )
    )
    registry.register(
        SkillDefinition(
            name="list_sources",
            description="list",
            input_schema=EmptySkillInput,
            callable=lambda **kwargs: {"files": files, "reply": "Found source files."},
        )
    )

    inputs = iter(["2", "/files", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "pages:unknown" in text
    assert "pages:0" not in text


def test_status_shows_selected_pdf_file_names(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    pdf_path = session.input_dir / "selected.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(pdf_path)]
    registry = SkillRegistry()

    inputs = iter(["2", "/status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "selected pdfs: 1" in text
    assert "selected pdf file names: selected.pdf" in text


def test_generate_plan_output_includes_selected_pdf_names(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "sales.pdf"
    selected.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(selected)]
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: generate_plan_skill(session=session, **kwargs),
        )
    )

    inputs = iter(["1", "根据已选择的 PDF，生成方案", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=ChatAgent(),
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "Source PDFs: sales.pdf." in text
    assert "Plan sources: sales.pdf" in text


def test_scan_continuation_generates_plan_for_requested_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    (session.input_dir / "OTHER.pdf").write_text("pdf", encoding="utf-8")
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    captured: dict = {}

    def capture_generate(**kwargs):
        captured.update(kwargs)
        return generate_plan_skill(session=session, **kwargs)

    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=capture_generate,
        )
    )
    agent = _ScanOnlyAgent(
        PendingUserRequest(
            text="\u505aSIEVE.pdf\uff0c20\u9875\u4ee5\u4e0a\uff0c\u53d7\u4f17\u662f\u7814\u7a76\u751f",
            requested_source_names=["SIEVE.pdf"],
            topic="SIEVE",
            min_slides=20,
            audience="\u7814\u7a76\u751f",
        )
    )

    inputs = iter(["1", "\u505aSIEVE.pdf\uff0c\u4e3b\u9898\u662fSIEVE\uff0c20\u9875\u4ee5\u4e0a\uff0c\u53d7\u4f17\u662f\u7814\u7a76\u751f", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=registry,
    )

    document = read_plan_document(Path(session.latest_plan_path))
    text = "\n".join(outputs)
    assert session.selected_sources == [str(selected)]
    assert captured["sources"] == [str(selected)]
    assert captured["audience"] == "\u7814\u7a76\u751f"
    assert captured["min_slides"] >= 20
    assert len(document.spec.slides) >= 20
    assert "Source PDFs: SIEVE.pdf." in text
    assert "Audience: \u7814\u7a76\u751f" in text
    assert "Slides:" in text
    assert "Plan ready. Run /approve to build the PPT." in text


def test_scan_continuation_matches_pdf_stem(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: generate_plan_skill(session=session, **kwargs),
        )
    )
    agent = _ScanOnlyAgent(
        PendingUserRequest(
            text="\u505aSIEVE",
            requested_source_names=["SIEVE"],
            topic="SIEVE",
        )
    )

    inputs = iter(["1", "\u505aSIEVE", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=registry,
    )

    assert session.selected_sources == [str(selected)]
    assert session.latest_plan_path is None
    assert "Source PDFs: SIEVE.pdf" in "\n".join(outputs)


def test_scan_without_requested_pdf_does_not_continue_to_plan(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    (session.input_dir / "SIEVE.pdf").write_text("pdf", encoding="utf-8")
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    called = {"generate": False}
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: called.__setitem__("generate", True) or {"reply": "plan", "plan_path": "x", "sources": []},
        )
    )
    agent = _ScanOnlyAgent(PendingUserRequest(text="\u6211\u8981\u5236\u4f5cppt"))

    inputs = iter(["1", "\u6211\u8981\u5236\u4f5cppt", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=registry,
    )

    text = "\n".join(outputs)
    assert called["generate"] is False
    assert "PDF sources: SIEVE.pdf" in text
    assert session.pending_action is None


def test_scan_continuation_reports_missing_requested_pdf(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    (session.input_dir / "SIEVE.pdf").write_text("pdf", encoding="utf-8")
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: {"reply": "plan", "plan_path": "x", "sources": []},
        )
    )
    agent = _ScanOnlyAgent(PendingUserRequest(text="\u505aMISSING.pdf", requested_source_names=["MISSING.pdf"]))

    inputs = iter(["1", "\u505aMISSING.pdf", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "Could not find the requested PDF: MISSING.pdf" in text
    assert "Available PDFs: SIEVE.pdf" in text
    assert session.pending_action is None


def test_draft_request_scans_and_generates_with_default_topic(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    (session.input_dir / "Range-Aware Neighborhood Search Graph.pdf").write_text("pdf", encoding="utf-8")
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    registry = SkillRegistry()
    _register_source_skills(registry, session)
    captured: dict = {}

    def capture_generate(**kwargs):
        captured.update(kwargs)
        return generate_plan_skill(session=session, **kwargs)

    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=capture_generate,
        )
    )

    inputs = iter(
        [
            "1",
            "\u7528SIEVE.pdf\uff0c20\u9875\u4ee5\u4e0a\uff0c\u53d7\u4f17\u7814\u7a76\u751f",
            "/exit",
        ]
    )
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    document = read_plan_document(Path(session.latest_plan_path))
    text = "\n".join(outputs)
    assert session.draft_request.requested_pdf_name == "SIEVE.pdf"
    assert session.selected_sources == [str(selected)]
    assert session.draft_request.min_slides == 20
    assert session.draft_request.audience == "\u7814\u7a76\u751f"
    assert session.draft_request.topic == "SIEVE \u8bba\u6587\u4ecb\u7ecd"
    assert captured["sources"] == [str(selected)]
    assert captured["topic"] == "SIEVE \u8bba\u6587\u4ecb\u7ecd"
    assert captured["audience"] == "\u7814\u7a76\u751f"
    assert captured["min_slides"] == 20
    assert len(document.spec.slides) >= 20
    assert "Audience: \u7814\u7a76\u751f" in text
    assert "Source PDFs: SIEVE.pdf." in text
    assert "Slides:" in text
    assert session.pending_action is not None
    assert session.last_loop_state.transition == "approval_required"
    assert session.last_loop_state.terminal_reason == "approval_required"
    assert session.last_loop_state.needs_approval is True


def test_exclude_other_pdf_confirmation_uses_current_selection(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(selected)]
    session.draft_request.merge(
        {
            "selected_sources": [str(selected)],
            "requested_pdf_name": "SIEVE.pdf",
            "audience": "\u7814\u7a76\u751f",
            "min_slides": 20,
        }
    )
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: generate_plan_skill(session=session, **kwargs),
        )
    )

    inputs = iter(["1", "\u4e0d\u5305\u542b\u5176\u4ed6 PDF", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert session.draft_request.exclude_other_sources is True
    assert session.selected_sources == [str(selected)]
    assert session.draft_request.topic == "SIEVE \u8bba\u6587\u4ecb\u7ecd"
    assert session.pending_action is not None


def test_start_without_pending_action_generates_from_draft(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.draft_request.merge(
        {
            "selected_sources": [str(selected)],
            "requested_pdf_name": "SIEVE.pdf",
            "audience": "\u7814\u7a76\u751f",
            "min_slides": 20,
        }
    )
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: generate_plan_skill(session=session, **kwargs),
        )
    )

    inputs = iter(["1", "\u5f00\u59cb", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert session.draft_request.topic == "SIEVE \u8bba\u6587\u4ecb\u7ecd"
    assert session.pending_action is not None


def test_draft_update_outputs_feedback_when_not_ready(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()

    inputs = iter(["1", "20\u9875\u4ee5\u4e0a\uff0c\u53d7\u4f17\u7814\u7a76\u751f", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "\u5df2\u8bb0\u5f55\u5f53\u524d\u9700\u6c42" in text
    assert "Audience: \u7814\u7a76\u751f" in text
    assert "Minimum slides: 20" in text


def test_status_shows_draft_request(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    session.draft_request.merge(
        {
            "requested_pdf_name": "SIEVE.pdf",
            "topic": "\u8bba\u6587\u4ecb\u7ecd",
            "audience": "\u7814\u7a76\u751f",
            "min_slides": 20,
            "selected_sources": [str(selected)],
        }
    )

    inputs = iter(["2", "/status", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "draft requested pdf: SIEVE.pdf" in text
    assert "draft topic: \u8bba\u6587\u4ecb\u7ecd" in text
    assert "draft audience: \u7814\u7a76\u751f" in text
    assert "draft min slides: 20" in text
    assert "draft selected pdfs: SIEVE.pdf" in text


def test_selected_pdf_is_reused_for_followup_planning_request(tmp_path):
    agent = ChatAgent()
    session = ShellSession.create(tmp_path)
    session.enable_assistant()
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    other = session.input_dir / "OTHER.pdf"
    selected.write_text("pdf", encoding="utf-8")
    other.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(selected)]

    decision = agent._route_with_fallback(session, "\u505a\u8fd9\u4e2a PDF \u7684 PPT \u65b9\u6848", registry=None)

    generate_plan = next(call for call in decision.skill_calls if call.name == "generate_plan")
    assert generate_plan.arguments["sources"] == [str(selected)]


def test_selected_pdf_paths_survive_without_discovered_sources(tmp_path):
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    session.selected_sources = [str(selected)]

    assert session.selected_pdf_paths() == [str(selected)]


def test_generate_plan_request_extracts_min_slides_and_audience(tmp_path):
    agent = ChatAgent()
    session = ShellSession.create(tmp_path)
    session.enable_assistant()
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(selected)]

    decision = agent._route_with_fallback(
        session,
        "\u4f7f\u7528 SIEVE\uff0c\u4e3b\u9898\u662f\u8bb2\u89e3pdf\uff0c\u6570\u91cf\u572820\u4ee5\u4e0a\uff0c\u53d7\u4f17\u662f\u7814\u7a76\u751f",
        registry=None,
    )

    generate_plan = next(call for call in decision.skill_calls if call.name == "generate_plan")
    assert generate_plan.arguments["sources"] == [str(selected)]
    assert generate_plan.arguments["min_slides"] >= 20
    assert generate_plan.arguments["audience"] == "\u7814\u7a76\u751f"


def test_generate_plan_skill_enforces_minimum_slides_and_requested_audience(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("pdf", encoding="utf-8")

    result = generate_plan_skill(
        session=session,
        topic="\u8bb2\u89e3pdf",
        min_slides=20,
        audience="\u7814\u7a76\u751f",
        sources=[str(selected)],
    )

    document = read_plan_document(Path(result["plan_path"]))
    assert len(document.spec.slides) >= 20
    assert document.spec.audience == "\u7814\u7a76\u751f"
    assert session.selected_sources == [str(selected)]


def test_ai_mode_model_identity_query_uses_session_config_without_calling_agent(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    save_api_key("deepseek", "sk-test", cwd=tmp_path)
    agent = _FakeAgent()

    inputs = iter(["1", "\u4f60\u662f\u4ec0\u4e48\u6a21\u578b", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "\u5f53\u524d AI assistant mode \u5df2\u5f00\u542f\u3002" in text
    assert "assistant enabled: true" in text
    assert f"Provider: {DEFAULT_ASSISTANT_PROVIDER}" in text
    assert f"Model: {DEFAULT_ASSISTANT_MODEL}" in text
    assert "Key configured: yes" in text


def test_continue_without_pending_action_does_not_trigger_approve(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    agent = _FakeAgent()

    inputs = iter(["2", "\u7ee7\u7eed", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=agent,
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert agent.calls == []
    assert "/files" in text
    assert "/select" in text


def test_preview_without_plan_shows_no_plan_available(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()

    inputs = iter(["2", "/preview", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    assert "no plan available" in "\n".join(outputs)


def test_preview_with_plan_shows_title_slide_count_selected_pdfs_and_slides(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _FakeGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "sales.pdf"
    selected.write_text("pdf", encoding="utf-8")
    session.discovered_sources = [item.model_dump(mode="json") for item in scan_workspace(session.input_dir)]
    session.selected_sources = [str(selected)]
    session.current_request = "AI Sales Enablement"
    generate_plan_skill(session=session, topic="AI Sales Enablement", sources=[str(selected)])
    registry = SkillRegistry()

    inputs = iter(["2", "/preview", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=registry,
    )

    text = "\n".join(outputs)
    assert "request/topic: AI Sales Enablement" in text
    assert "selected pdfs: sales.pdf" in text
    assert "plan title: AI Sales Enablement" in text
    assert "plan slides: 1" in text
    assert "plan source pdfs: sales.pdf" in text


def test_guizang_request_sets_html_output_and_pending_html_build(monkeypatch, tmp_path):
    _write_guizang_skill(tmp_path)
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text(
        "SIEVE Paper. Abstract SIEVE studies selective retrieval. Method graph filtering. Experiments compare latency metrics. Results improve query time.",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    inputs = iter([
        "1",
        "all",
        "用 guizang-ppt-skill，给 SIEVE.pdf 做一份研究生论文讲解 PPT，20 页以上，杂志风，先生成计划。",
        "/exit",
    ])
    outputs: list[str] = []
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)

    assert session.draft_request.output_format == "html"
    assert session.draft_request.applied_skills == ["guizang-ppt-skill"]
    assert session.pending_action is not None
    assert session.pending_action.skill_name == "build_html_deck"
    assert session.pending_action.arguments["output_path"].endswith("SIEVE.html")
    text = "\n".join(outputs)
    assert "Applied skill: guizang-ppt-skill" in text
    assert "Output format: html" in text
    assert not (session.output_dir / "SIEVE.html").exists()


def test_approve_builds_html_deck_without_visual_area_or_raw_image_query(tmp_path):
    _write_guizang_skill(tmp_path)
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text(
        "SIEVE Paper. Abstract SIEVE is a research paper about selective graph search. Method uses filtering. Experiments use latency metrics. Results are reported in the paper.",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    inputs = iter([
        "1",
        "all",
        "用 guizang-ppt-skill，给 SIEVE.pdf 做一份研究生论文讲解 PPT，20 页以上，杂志风，先生成计划。",
        "/approve",
        "/exit",
    ])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=lambda line: None, session=session, registry=registry)

    html_path = session.output_dir / "SIEVE.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "VISUAL AREA" not in html
    assert "image_query" not in html
    assert "image_prompt" not in html
    assert '{"type":' not in html
    assert '{ "type":' not in html
    assert html.count('<section class="slide') >= 20
    assert '<div id="deck"><section class="slide' in html
    assert '<main class="deck">' not in html
    assert "<!-- SLIDES_HERE -->" not in html
    assert 'data-slide-index="20"' in html
    assert "20 / 20" in html
    assert "ArrowRight" in html
    assert "Home" in html
    assert "SIEVE" in html
    assert session.latest_html_path == str(html_path)


def test_html_deck_qa_detects_missing_slide_sections():
    html = '<main class="deck"><section class="slide" data-slide-index="1">Only one</section></main>'

    errors = validate_html_deck(html, expected_slides=20, requested_min_slides=20)

    assert any("expected at least 20" in error for error in errors)
    assert any("requested minimum is 20" in error for error in errors)


def test_research_paper_explanation_parses_graduate_audience_and_digest_grounding(tmp_path):
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text(
        "SIEVE Paper. Abstract This paper presents SIEVE for efficient vector retrieval. Problem search latency is high. Method builds selective filters. Experiments evaluate latency and recall. Results reduce search work.",
        encoding="utf-8",
    )

    result = generate_plan_skill(
        session=session,
        topic="论文讲解",
        sources=[str(selected)],
        audience="研究生",
        min_slides=20,
    )
    document = read_plan_document(Path(result["plan_path"]))
    text = "\n".join(slide["title"] + " " + slide["core_message"] + " " + " ".join(slide["bullets"]) for slide in document.payload["slides"])

    assert document.spec.audience == "研究生"
    assert document.payload["source_digest"]["sources"][0]["title"] != "unknown"
    assert "ROI" not in text
    assert "NeurIPS" not in text
    assert "GitHub star" not in text
    assert not any(slide["title"].startswith("Supporting Appendix") for slide in document.payload["slides"])


def test_preview_and_status_show_html_output_and_applied_skill(tmp_path):
    _write_guizang_skill(tmp_path)
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("Abstract SIEVE paper. Method filtering. Experiments latency. Results reported.", encoding="utf-8")
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)
    generate_plan_skill(
        session=session,
        topic="论文讲解",
        sources=[str(selected)],
        audience="研究生",
        output_format="html",
        applied_skills=["guizang-ppt-skill"],
        theme="magazine",
    )

    outputs: list[str] = []
    inputs = iter(["1", "all", "/preview", "/status", "/exit"])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)
    text = "\n".join(outputs)

    assert "output format: html" in text
    assert "applied skill: guizang-ppt-skill" in text
    assert "draft output format: html" in text
    assert "draft applied skills: guizang-ppt-skill" in text
    assert "enabled user skills: guizang-ppt-skill" in text


def test_startup_user_skill_selection_none_all_and_one(tmp_path):
    _write_guizang_skill(tmp_path)
    _write_guizang_skill(tmp_path / "other-root")
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    inputs = iter(["1", "0", "/exit"])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)
    assert session.enabled_user_skills == []
    assert "Available user skills:" in "\n".join(outputs)

    outputs = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)
    inputs = iter(["1", "all", "/exit"])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)
    assert session.enabled_user_skills == ["guizang-ppt-skill"]

    outputs = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)
    inputs = iter(["1", "1", "/exit"])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)
    assert session.enabled_user_skills == ["guizang-ppt-skill"]


def test_skills_selected_enable_disable_and_status(tmp_path):
    _write_guizang_skill(tmp_path)
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)

    inputs = iter([
        "2",
        "0",
        "/skills selected",
        "/skills enable guizang-ppt-skill",
        "/skills selected",
        "/status",
        "/skills disable guizang-ppt-skill",
        "/skills selected",
        "/exit",
    ])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)
    text = "\n".join(outputs)

    assert "enabled user skills: none" in text
    assert "Enabled user skills: guizang-ppt-skill" in text
    assert "enabled user skills: guizang-ppt-skill" in text


def test_explicit_disabled_skill_request_is_blocked(tmp_path):
    _write_guizang_skill(tmp_path)
    session = ShellSession.create(tmp_path)
    selected = session.input_dir / "SIEVE.pdf"
    selected.write_text("Abstract SIEVE paper.", encoding="utf-8")
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    reload_user_skills(registry, session=session)
    outputs: list[str] = []
    inputs = iter([
        "1",
        "0",
        "用 guizang-ppt-skill 给 SIEVE.pdf 做 HTML deck",
        "/exit",
    ])

    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)

    text = "\n".join(outputs)
    assert "guizang-ppt-skill is installed but not enabled for this session." in text
    assert session.pending_action is None


def test_qa_flags_supporting_appendix_placeholder_for_academic_deck():
    state = {
        "spec": {
            "title": "SIEVE",
            "audience": "研究生",
            "theme": "executive_blue",
            "source_digest": {"sources": [{"title": "SIEVE"}]},
            "slides": [
                {
                    "title": "Supporting Appendix 1",
                    "objective": "Adds context.",
                    "core_message": "This appendix adds supporting context and keeps the main deck aligned.",
                    "bullets": ["More context", "More detail", "More notes"],
                    "supporting_points": [],
                    "visual_type": "three_card_summary",
                }
            ],
        },
        "transitions": [],
    }

    result = qa_node(state)

    assert any(issue["code"] == "placeholder_appendix" for issue in result["qa_issues"])


def test_registry_exposes_input_schema_and_validates_arguments():
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="generate_plan",
            description="plan",
            input_schema=GeneratePlanInput,
            callable=lambda **kwargs: {"reply": kwargs["topic"]},
        )
    )

    skill = registry.get("generate_plan")

    assert "topic" in skill.input_schema.model_json_schema()["properties"]
    assert registry.validate_arguments("generate_plan", {"topic": "AI Sales Enablement", "slides": 10}) == {
        "topic": "AI Sales Enablement",
        "slides": 10,
    }


def test_default_skill_registry_exposes_tool_contract_fields(tmp_path):
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    for description in registry.describe():
        assert "input_schema" in description
        assert "result_schema" in description
        assert "is_read_only" in description
        assert "requires_approval" in description

    assert registry.get("scan_workspace").is_read_only is True
    assert registry.get("show_current_plan").is_read_only is True
    assert registry.get("build_ppt").requires_approval is True
    assert registry.get("run_from_plan").requires_approval is True


def test_skill_result_defaults_are_not_shared():
    first = SkillResult()
    second = SkillResult()

    first.data["path"] = "deck.pptx"
    first.warnings.append("warning")

    assert second.data == {}
    assert second.warnings == []


def test_default_skill_registry_keeps_names_and_flags(tmp_path):
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    assert registry.names() == [
        "scan_workspace",
        "list_sources",
        "generate_plan",
        "ingest_sources",
        "index_source",
        "retrieve_source_context",
        "retrieve_project_memory",
        "record_project_memory",
        "record_execution_trace",
        "retrieve_failure_patterns",
        "digest_pdf_sources",
        "validate_plan",
        "migrate_plan",
        "build_ppt",
        "build_html_deck",
        "run_from_plan",
        "show_current_plan",
        "revise_plan",
        "list_generated_files",
    ]
    assert registry.get("build_ppt").requires_approval is True
    assert registry.get("run_from_plan").requires_approval is True
    assert registry.get("scan_workspace").is_read_only is True
    assert registry.get("list_sources").is_read_only is True
    assert registry.get("digest_pdf_sources").is_read_only is True
    assert registry.get("retrieve_source_context").is_read_only is True
    assert registry.get("retrieve_project_memory").is_read_only is True
    assert registry.get("retrieve_failure_patterns").is_read_only is True
    assert registry.get("validate_plan").is_read_only is True
    assert registry.get("show_current_plan").is_read_only is True


def test_shell_startup_reprompts_after_invalid_assistant_mode_choice(tmp_path):
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)

    inputs = iter(["bad", "2", "/exit"])
    run_shell(
        input_fn=lambda prompt: next(inputs),
        output_fn=outputs.append,
        session=session,
        agent=_FakeAgent(),
        registry=SkillRegistry(),
    )

    text = "\n".join(outputs)
    assert "Please choose 1 or 2." in text
    assert "Manual CLI mode enabled." in text
    assert session.assistant_enabled is False
