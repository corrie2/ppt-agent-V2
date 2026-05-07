import json

from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import generate_plan_skill, register_default_skills
from ppt_agent.shell.session import ShellSession
from ppt_agent.storage import project_memory
from ppt_agent.storage.project_memory import (
    ensure_project_memory,
    looks_like_user_preference,
    record_execution_trace,
    record_project_memory,
    retrieve_failure_patterns,
    retrieve_project_memory,
)


def test_project_memory_initializes_expected_files(tmp_path):
    root = ensure_project_memory(tmp_path)

    assert (root / "user_preferences.json").exists()
    assert (root / "execution_traces.jsonl").exists()
    assert (root / "qa_failures.jsonl").exists()
    assert (root / "accepted_outputs.jsonl").exists()


def test_record_and_retrieve_user_preferences(tmp_path):
    record_project_memory(tmp_path, feedback="不要空方框")
    record_project_memory(tmp_path, feedback="正文太多")
    record_project_memory(tmp_path, feedback="要研究生风格")

    memory = retrieve_project_memory(tmp_path, query="研究生风格")
    preferences = memory["preferences"]

    assert any(item["preference"] == "不要空方框" for item in preferences)
    assert any(item["category"] == "style" for item in preferences)
    assert looks_like_user_preference("要研究生风格")


def test_record_and_retrieve_failure_patterns(tmp_path):
    record_execution_trace(tmp_path, event="html qa failed", trace_type="qa_failure", payload={"code": "empty_box"})

    failures = retrieve_failure_patterns(tmp_path, query="empty box")

    assert failures["failure_patterns"][0]["event"] == "html qa failed"


def test_retrieve_project_memory_prefers_long_term_memory_when_enabled(monkeypatch, tmp_path):
    record_project_memory(tmp_path, feedback="Legacy preference")

    def fake_read(workspace, *, memory_types, query, limit):
        if memory_types == ["user_preference"]:
            return [{"preference": "Vector preference", "category": "style"}]
        if memory_types == ["accepted_output"]:
            return [{"event": "vector accepted"}]
        return None

    monkeypatch.setattr(project_memory, "_maybe_read_long_term_memory", fake_read)

    memory = retrieve_project_memory(tmp_path, query="preference")

    assert memory["preferences"] == [{"preference": "Vector preference", "category": "style"}]
    assert memory["accepted_outputs"] == [{"event": "vector accepted"}]


def test_retrieve_project_memory_falls_back_when_long_term_read_fails(monkeypatch, tmp_path):
    record_project_memory(tmp_path, feedback="Legacy preference")
    monkeypatch.setattr(project_memory, "_maybe_read_long_term_memory", lambda *args, **kwargs: None)

    memory = retrieve_project_memory(tmp_path, query="legacy")

    assert memory["preferences"][0]["preference"] == "Legacy preference"


def test_retrieve_failure_patterns_prefers_long_term_memory(monkeypatch, tmp_path):
    record_execution_trace(tmp_path, event="legacy qa failed", trace_type="qa_failure", payload={"code": "legacy"})

    def fake_read(workspace, *, memory_types, query, limit):
        assert memory_types == ["qa_failure"]
        return [{"event": "vector qa failed", "payload": {"code": "vector"}}]

    monkeypatch.setattr(project_memory, "_maybe_read_long_term_memory", fake_read)

    failures = retrieve_failure_patterns(tmp_path, query="qa")

    assert failures["failure_patterns"] == [{"event": "vector qa failed", "payload": {"code": "vector"}}]


def test_project_memory_does_not_double_write_when_vector_memory_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("PPT_AGENT_VECTOR_MEMORY", raising=False)
    monkeypatch.delenv("PPT_AGENT_MEMORY_DATABASE_URL", raising=False)

    result = record_project_memory(tmp_path, feedback="Prefer concise slides")

    assert "long_term_memory" not in result


def test_project_memory_double_writes_preference_when_enabled(monkeypatch, tmp_path):
    captured = {}

    def fake_write(workspace, **kwargs):
        captured["workspace"] = workspace
        captured["kwargs"] = kwargs
        return {"status": "written", "record_id": "record-1", "embedding_id": "embedding-1"}

    monkeypatch.setattr(project_memory, "_maybe_write_long_term_memory", fake_write)

    result = record_project_memory(tmp_path, feedback="Prefer concise slides", category="style", source="user_feedback")

    assert result["preference"]["preference"] == "Prefer concise slides"
    assert result["long_term_memory"]["status"] == "written"
    assert captured["workspace"] == tmp_path
    assert captured["kwargs"]["memory_type"] == "user_preference"
    assert captured["kwargs"]["title"] == "style preference"
    assert captured["kwargs"]["content"] == "Prefer concise slides"
    assert captured["kwargs"]["source_type"] == "user_feedback"
    assert "preference" in captured["kwargs"]["tags"]


def test_execution_trace_double_write_failure_preserves_jsonl(monkeypatch, tmp_path):
    def fake_write(*args, **kwargs):
        return {"status": "failed", "error": "database unavailable"}

    monkeypatch.setattr(project_memory, "_maybe_write_long_term_memory", fake_write)

    result = record_execution_trace(tmp_path, event="qa failed", trace_type="qa_failure", payload={"code": "empty_box"})
    traces = [
        json.loads(line)
        for line in (tmp_path / ".ppt-agent" / "memory" / "qa_failures.jsonl").read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]

    assert traces[0]["event"] == "qa failed"
    assert result["long_term_memory"] == {"status": "failed", "error": "database unavailable"}


def test_sensitive_execution_trace_is_not_written_to_long_term_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("PPT_AGENT_VECTOR_MEMORY", "1")
    monkeypatch.setenv("PPT_AGENT_MEMORY_DATABASE_URL", "postgresql://example")

    result = record_execution_trace(
        tmp_path,
        event="received credentials",
        payload={"api_key": "sk-test-secret"},
    )
    traces = [
        json.loads(line)
        for line in (tmp_path / ".ppt-agent" / "memory" / "execution_traces.jsonl").read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]

    assert traces[0]["payload"] == {"api_key": "sk-test-secret"}
    assert result["long_term_memory"] == {
        "status": "skipped",
        "reason": "sensitive memory content is not written to PostgreSQL",
    }


def test_generate_plan_retrieves_memory_before_planning(tmp_path):
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    registry.invoke("record_project_memory", feedback="不要空方框")
    registry.invoke("record_execution_trace", event="prior QA failed", trace_type="qa_failure", payload={"code": "empty_box"})

    result = generate_plan_skill(session=session, topic="AI Sales Enablement", plan_path=str(tmp_path / "plan.json"))
    payload = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))

    assert result["plan_path"] == str(tmp_path / "plan.json")
    assert payload["metadata"]["project_memory"]["preferences"][0]["preference"] == "不要空方框"
    assert payload["metadata"]["project_memory"]["failure_patterns"][0]["event"] == "prior QA failed"
