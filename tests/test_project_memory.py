import json

from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import generate_plan_skill, register_default_skills
from ppt_agent.shell.session import ShellSession
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
