import json
from pathlib import Path

from typer.testing import CliRunner

from ppt_agent.cli.main import app
from ppt_agent.domain.evidence import EvidencePack, FigureAsset, SectionEvidence, SourceRef


runner = CliRunner()


def test_plan_with_evidence_generates_schema_v2_plan(tmp_path):
    evidence_path = _write_evidence_pack(tmp_path)
    plan_path = tmp_path / "plan.json"

    result = runner.invoke(app, ["plan", "--evidence", str(evidence_path), "--spec", str(plan_path)])

    assert result.exit_code == 0
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["title"] == "Evidence Paper"
    assert payload["slides"]
    assert all(slide["role"] for slide in payload["slides"])
    assert all(slide["message"] for slide in payload["slides"])
    assert all(slide["layout"] for slide in payload["slides"])


def test_plan_with_evidence_uses_deterministic_planner_by_default(monkeypatch, tmp_path):
    evidence_path = _write_evidence_pack(tmp_path)
    plan_path = tmp_path / "plan.json"

    monkeypatch.setattr("ppt_agent.runtime.planner.load_selection", lambda: ("openai", "gpt-test"))
    monkeypatch.setattr("ppt_agent.runtime.planner.load_api_key", lambda provider: "test-key")

    def fail_llm(*args, **kwargs):
        raise AssertionError("evidence pack planning should not call the external LLM by default")

    monkeypatch.setattr("ppt_agent.runtime.planner.generate_plan_with_llm", fail_llm)

    result = runner.invoke(app, ["plan", "--evidence", str(evidence_path), "--spec", str(plan_path)])

    assert result.exit_code == 0
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["title"] == "Evidence Paper"


def test_plan_with_evidence_citations_reference_existing_evidence_ids(tmp_path):
    evidence_path = _write_evidence_pack(tmp_path)
    plan_path = tmp_path / "plan.json"

    result = runner.invoke(app, ["plan", "Evidence Paper", "--evidence", str(evidence_path), "--spec", str(plan_path)])

    assert result.exit_code == 0
    evidence_ids = {"section-problem", "section-method", "section-result", "fig-architecture"}
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    cited_ids = {
        citation["evidence_id"]
        for slide in payload["slides"]
        for citation in slide.get("citations", [])
    }
    legacy_refs = {
        evidence_id
        for slide in payload["slides"]
        for evidence_id in slide.get("evidence_refs", [])
    }

    assert cited_ids
    assert cited_ids <= evidence_ids
    assert legacy_refs <= evidence_ids


def test_plan_with_figure_evidence_generates_figure_with_caption_layout(tmp_path):
    evidence_path = _write_evidence_pack(tmp_path)
    plan_path = tmp_path / "plan.json"

    result = runner.invoke(app, ["plan", "--evidence", str(evidence_path), "--spec", str(plan_path)])

    assert result.exit_code == 0
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    figure_slides = [slide for slide in payload["slides"] if slide["layout"] == "figure_with_caption"]

    assert figure_slides
    assert figure_slides[0]["content"]["figure_ids"] == ["fig-architecture"]
    assert figure_slides[0]["citations"][0]["evidence_id"] == "fig-architecture"


def _write_evidence_pack(tmp_path: Path) -> Path:
    pack = EvidencePack(
        source_files=[
            SourceRef(
                id="source-001",
                source_file="paper.pdf",
                path=str(tmp_path / "paper.pdf"),
                title="Evidence Paper",
            )
        ],
        sections=[
            SectionEvidence(
                id="section-problem",
                source_file="paper.pdf",
                page=1,
                heading="Problem and Motivation",
                text="The problem is inefficient retrieval over long documents.",
            ),
            SectionEvidence(
                id="section-method",
                source_file="paper.pdf",
                page=2,
                heading="Method",
                text="The method builds a range-aware neighborhood search graph.",
            ),
            SectionEvidence(
                id="section-result",
                source_file="paper.pdf",
                page=3,
                heading="Main Results",
                text="The results show improved retrieval quality under the evaluated setting.",
            ),
        ],
        figures=[
            FigureAsset(
                id="fig-architecture",
                source_file="paper.pdf",
                page=2,
                caption="Architecture of the retrieval graph.",
                path="figures/architecture.png",
            )
        ],
    )
    path = tmp_path / "evidence.json"
    path.write_text(pack.to_json(), encoding="utf-8")
    return path
