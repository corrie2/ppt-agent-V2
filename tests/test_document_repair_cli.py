import base64
import json
from pathlib import Path

from typer.testing import CliRunner

from ppt_agent.cli.main import app
from ppt_agent.domain.evidence import EvidencePack, FigureAsset, SectionEvidence, SourceRef


runner = CliRunner()


def test_repair_plan_from_qa_report_reduces_issues_without_touching_pptx(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    plan_path = tmp_path / "plan.json"
    qa_path = tmp_path / "qa_report.json"
    repaired_path = tmp_path / "repaired_plan.json"
    pptx_path = tmp_path / "deck.pptx"
    image_path = tmp_path / "figure.png"
    _write_png(image_path)
    _write_evidence(evidence_path, figure_path=image_path)
    _write_problem_plan(plan_path)

    before = runner.invoke(app, ["qa", str(plan_path), "--evidence", str(evidence_path), "--out", str(qa_path)])
    before_payload = json.loads(qa_path.read_text(encoding="utf-8"))

    result = runner.invoke(
        app,
        ["repair", str(plan_path), "--qa", str(qa_path), "--evidence", str(evidence_path), "--out", str(repaired_path)],
    )

    assert before.exit_code == 1
    assert result.exit_code == 0
    assert repaired_path.exists()
    assert not pptx_path.exists()
    assert not list(tmp_path.glob("*.pptx"))

    after_qa_path = tmp_path / "qa_after.json"
    after = runner.invoke(app, ["qa", str(repaired_path), "--evidence", str(evidence_path), "--out", str(after_qa_path)])
    after_payload = json.loads(after_qa_path.read_text(encoding="utf-8"))

    assert len(after_payload["issues"]) < len(before_payload["issues"])

    repaired = json.loads(repaired_path.read_text(encoding="utf-8"))
    by_id = {slide["id"]: slide for slide in repaired["slides"]}
    assert by_id["missing-figure"]["content"]["figure_ids"] == []
    assert by_id["empty-message"]["message"]
    assert len(by_id["too-many-bullets"]["bullets"]) == 5
    assert by_id["bad-citation"]["citations"] == []
    assert by_id["layout-mismatch"]["layout"] == "figure_with_caption"

    build = runner.invoke(app, ["build", str(repaired_path), "--evidence", str(evidence_path), "--out", str(pptx_path)])

    assert after.exit_code == 1
    assert build.exit_code == 0
    assert pptx_path.exists()


def _write_evidence(path: Path, *, figure_path: Path) -> None:
    pack = EvidencePack(
        source_files=[SourceRef(id="source_001", source_file="paper.pdf", title="Paper")],
        sections=[SectionEvidence(id="section_001", source_file="paper.pdf", page=1, heading="Method", text="Method evidence.")],
        figures=[FigureAsset(id="fig_001", source_file="paper.pdf", page=2, caption="Architecture.", path=str(figure_path))],
    )
    path.write_text(pack.to_json(), encoding="utf-8")


def _write_problem_plan(path: Path) -> None:
    slides = [
        {
            "id": "claim-no-citation",
            "role": "claim",
            "title": "Main Claim",
            "message": "The method improves retrieval quality.",
            "layout": "two_column_text_image",
            "content": {"bullets": ["Improves quality."], "figure_ids": [], "table_ids": [], "metrics": []},
            "citations": [],
        },
        {
            "id": "bad-citation",
            "role": "evidence",
            "title": "Bad Citation",
            "message": "This cites unavailable evidence.",
            "layout": "two_column_text_image",
            "content": {"bullets": ["Citation should resolve."], "figure_ids": [], "table_ids": [], "metrics": []},
            "citations": [{"evidence_id": "missing_section"}],
        },
        {
            "id": "missing-figure",
            "role": "figure_evidence",
            "title": "Missing Figure",
            "message": "This figure id is absent.",
            "layout": "figure_with_caption",
            "content": {"bullets": ["Figure should exist."], "figure_ids": ["fig_missing"], "table_ids": [], "metrics": []},
            "citations": [{"evidence_id": "section_001"}],
        },
        {
            "id": "too-many-bullets",
            "role": "summary",
            "title": "Dense Slide",
            "message": "This slide has too many bullets.",
            "layout": "two_column_text_image",
            "content": {"bullets": ["A", "B", "C", "D", "E", "F"], "figure_ids": [], "table_ids": [], "metrics": []},
            "citations": [],
        },
        {
            "id": "empty-message",
            "role": "summary",
            "title": "No Message",
            "message": "",
            "layout": "two_column_text_image",
            "content": {"bullets": ["Needs message."], "figure_ids": [], "table_ids": [], "metrics": []},
            "citations": [],
        },
        {
            "id": "layout-mismatch",
            "role": "figure_evidence",
            "title": "Layout Mismatch",
            "message": "This slide has a figure but not a figure layout.",
            "layout": "two_column_text_image",
            "content": {"bullets": ["Mismatch."], "figure_ids": ["fig_001"], "table_ids": [], "metrics": []},
            "citations": [{"evidence_id": "fig_001"}],
        },
    ]
    path.write_text(
        json.dumps({"schema_version": 2, "title": "Problem Plan", "audience": "reviewers", "slides": slides}),
        encoding="utf-8",
    )


def _write_png(path: Path) -> None:
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
    )
