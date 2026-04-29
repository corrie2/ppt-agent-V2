import json
import tomllib
from pathlib import Path

from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import generate_plan_skill, register_default_skills
from ppt_agent.domain.models import DeckIntent, PptSpec, SlideSpec
from ppt_agent.nodes.qa import qa_node
from ppt_agent.shell.app import run_shell
from ppt_agent.shell.session import PendingAction, ShellSession
from ppt_agent.storage.plan_io import read_plan_document
from ppt_agent.storage.project_memory import record_execution_trace, record_project_memory


def test_project_declares_pdf_extraction_dependency():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert any(dependency.startswith("pypdf") for dependency in payload["project"]["dependencies"])


def test_pdf_source_generates_digest_without_network(tmp_path):
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text(
        "Local Paper. Abstract local digest. Problem local problem. Method local method. "
        "Experiments local setup. Results local result. Limitations local limitation.",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    result = registry.invoke("digest_pdf_sources", sources=[str(source)])
    digest = result["source_digest"]["sources"][0]

    assert digest["path"] == str(source.resolve())
    assert "local digest" in digest["abstract"]
    assert "local setup" in digest["experiments"]
    assert "local result" in digest["results"]


def test_generate_plan_consumes_precomputed_source_digest_and_context(monkeypatch, tmp_path):
    captured: dict = {}

    class CapturingGraph:
        def invoke(self, state: dict) -> dict:
            captured.update(state)
            return {
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
                "spec": {
                    "title": "Paper",
                    "audience": "research",
                    "slides": [
                        {
                            "title": "Evidence Slide",
                            "objective": "Explain grounded evidence.",
                            "core_message": "This slide uses caller-provided evidence.",
                            "bullets": ["Digest is supplied."],
                            "supporting_points": ["Context is supplied."],
                            "visual_type": "editorial_diagram",
                            "layout_hint": "two_column_text_image",
                            "evidence_refs": ["precomputed:digest"],
                        }
                    ],
                },
            }

    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: CapturingGraph())
    session = ShellSession.create(tmp_path)
    source_digest = {"sources": [{"source_id": "precomputed", "title": "Precomputed Paper"}], "warnings": []}
    source_context = [{"source_id": "precomputed", "chunk_id": "c1", "text": "precomputed context"}]

    generate_plan_skill(
        session=session,
        topic="Paper",
        source_digest=source_digest,
        source_context=source_context,
        plan_path=str(tmp_path / "plan.json"),
    )

    assert captured["intent"]["source_digest"]["sources"][0]["source_id"] == "precomputed"
    assert captured["intent"]["source_context"][0]["chunk_id"] == "c1"


def test_generated_academic_slides_have_evidence_refs(tmp_path):
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text(
        "SIEVE Paper. Abstract SIEVE studies retrieval. Problem latency is high. "
        "Method filters graph candidates. Experiments evaluate latency. Results report lower work.",
        encoding="utf-8",
    )

    result = generate_plan_skill(session=session, topic="Paper explainer", sources=[str(source)], audience="graduate students")
    document = read_plan_document(Path(result["plan_path"]))

    assert document.spec.slides
    assert all(slide.evidence_refs for slide in document.spec.slides)


def test_qa_rejects_experimental_conclusion_without_evidence():
    spec = PptSpec(
        title="Academic Deck",
        audience="graduate students",
        source_digest={"sources": [{"title": "Paper", "results": "evaluation is discussed without numeric claims"}]},
        slides=[
            SlideSpec(
                title="Experiment Result",
                objective="Explain experiment result.",
                core_message="The method improves accuracy by 42%.",
                bullets=["It significantly outperforms all baselines."],
                supporting_points=["The result is stated as a concrete conclusion."],
                visual_type="comparison_table",
                layout_hint="comparison_table",
                evidence_refs=["paper:digest"],
            )
        ],
    )

    result = qa_node({"spec": spec.model_dump(mode="json")})
    codes = {issue["code"] for issue in result["qa_issues"]}

    assert "unsupported_specific_conclusion" in codes


def test_user_feedback_is_written_to_memory(tmp_path):
    session = ShellSession.create(tmp_path)
    registry = SkillRegistry()
    register_default_skills(registry, session=session)

    registry.invoke("record_project_memory", feedback="不要空方框")
    payload = json.loads((tmp_path / ".ppt-agent" / "memory" / "user_preferences.json").read_text(encoding="utf-8-sig"))

    assert payload["preferences"][0]["preference"] == "不要空方框"


def test_next_generation_retrieves_memory(monkeypatch, tmp_path):
    captured: dict = {}

    class CapturingGraph:
        def invoke(self, state: dict) -> dict:
            captured.update(state)
            return {
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
                "spec": {
                    "title": "Memory Deck",
                    "audience": "general business audience",
                    "slides": [
                        {
                            "title": "Memory",
                            "objective": "Use memory.",
                            "core_message": "The planner receives project preferences.",
                            "bullets": ["Preference is supplied."],
                            "supporting_points": ["Memory is retrieved before planning."],
                            "visual_type": "three_card_summary",
                            "layout_hint": "three_card_summary",
                        }
                    ],
                },
            }

    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: CapturingGraph())
    session = ShellSession.create(tmp_path)
    record_project_memory(tmp_path, feedback="正文太多")

    generate_plan_skill(session=session, topic="Memory Deck", plan_path=str(tmp_path / "plan.json"))

    assert captured["intent"]["project_preferences"][0]["preference"] == "正文太多"


def test_guizang_generation_reads_historical_failure_and_preference(monkeypatch, tmp_path):
    captured: dict = {}

    class CapturingGraph:
        def invoke(self, state: dict) -> dict:
            captured.update(state)
            return {
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
                "spec": {
                    "title": "Guizang Deck",
                    "audience": "graduate students",
                    "output_format": "html",
                    "applied_skills": ["guizang-ppt-skill"],
                    "slides": [
                        {
                            "title": "Guizang",
                            "objective": "Use memory.",
                            "core_message": "The planner receives guizang memory.",
                            "bullets": ["Preference and failures are supplied."],
                            "supporting_points": ["No script execution is required."],
                            "visual_type": "editorial_diagram",
                            "layout_hint": "editorial_diagram",
                        }
                    ],
                },
            }

    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: CapturingGraph())
    session = ShellSession.create(tmp_path)
    record_project_memory(tmp_path, feedback="不要空方框")
    record_execution_trace(tmp_path, event="guizang qa failed", trace_type="qa_failure", payload={"code": "empty_box"})

    generate_plan_skill(
        session=session,
        topic="Guizang Deck",
        output_format="html",
        applied_skills=["guizang-ppt-skill"],
        plan_path=str(tmp_path / "plan.json"),
    )
    payload = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))

    assert captured["intent"]["project_preferences"][0]["preference"] == "不要空方框"
    assert captured["intent"]["failure_patterns"][0]["event"] == "guizang qa failed"
    assert payload["metadata"]["project_memory"]["failure_patterns"][0]["event"] == "guizang qa failed"


def test_approve_after_pending_build_writes_execution_trace(monkeypatch, tmp_path):
    monkeypatch.setattr("ppt_agent.agent.skills.create_agent_graph", lambda: _SmallGraph())
    outputs: list[str] = []
    session = ShellSession.create(tmp_path)
    source = session.input_dir / "paper.pdf"
    source.write_text("Paper. Abstract source. Method local. Results local.", encoding="utf-8")
    registry = SkillRegistry()
    register_default_skills(registry, session=session)
    plan_result = generate_plan_skill(session=session, topic="Approve Trace", sources=[str(source)])
    session.pending_action = PendingAction(
        skill_name="build_ppt",
        arguments={"plan_path": plan_result["plan_path"], "output_path": str(session.output_dir / "approved.pptx")},
        description="build pending ppt",
    )

    inputs = iter(["2", "/approve", "/exit"])
    run_shell(input_fn=lambda prompt: next(inputs), output_fn=outputs.append, session=session, registry=registry)

    trace_path = tmp_path / ".ppt-agent" / "memory" / "accepted_outputs.jsonl"
    traces = [json.loads(line) for line in trace_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert session.latest_ppt_path
    assert any(item["event"] == "ppt_built" for item in traces)


class _SmallGraph:
    def invoke(self, state: dict) -> dict:
        return {
            "mode": "plan",
            "approved": False,
            "transitions": ["plan"],
            "spec": {
                "title": "Small Deck",
                "audience": "general business audience",
                "slides": [
                    {
                        "title": "Small Deck",
                        "objective": "Frame the request.",
                        "core_message": "A local plan can be reviewed before approval.",
                        "bullets": ["Use local source.", "Wait for approval.", "Build after approval."],
                        "supporting_points": ["Approval gate remains explicit.", "No network is required."],
                        "visual_type": "three_card_summary",
                        "layout_hint": "three_card_summary",
                    }
                ],
            },
        }
