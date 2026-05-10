import json

from typer.testing import CliRunner

from ppt_agent.cli.main import app
from ppt_agent.domain.evidence import EvidencePack, FigureAsset, SectionEvidence, SourceRef


runner = CliRunner()


def test_document_qa_cli_reports_document_to_deck_issues(tmp_path):
    plan_path = tmp_path / "plan.json"
    evidence_path = tmp_path / "evidence.json"
    report_path = tmp_path / "qa_report.json"
    _write_evidence(evidence_path, missing_image=tmp_path / "missing.png")
    _write_problem_plan(plan_path)

    result = runner.invoke(app, ["qa", str(plan_path), "--evidence", str(evidence_path), "--out", str(report_path)])

    assert result.exit_code == 1
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    issues = payload["issues"]
    issue_ids = {issue["id"] for issue in issues}

    assert payload["ok"] is False
    assert "claim-no-citation:missing_citation" in issue_ids
    assert "bad-citation:missing_evidence:missing_section" in issue_ids
    assert "missing-figure:missing_figure:fig_missing" in issue_ids
    assert "missing-image:missing_figure_image:fig_001" in issue_ids
    assert "too-many-bullets:too_many_bullets" in issue_ids
    assert "empty-message:empty_message" in issue_ids
    assert "layout-mismatch:layout_content_mismatch" in issue_ids
    assert all({"id", "severity", "slide_id", "message"} <= set(issue) for issue in issues)
    assert any(issue["suggested_fix"] for issue in issues)


def test_document_qa_unresolved_citation_without_evidence_does_not_fail(tmp_path):
    plan_path = tmp_path / "plan.json"
    report_path = tmp_path / "qa_report.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "title": "Traceable Plan",
                "audience": "reviewers",
                "slides": [
                    {
                        "id": "cited",
                        "role": "claim",
                        "title": "Claim",
                        "message": "A supported claim.",
                        "layout": "two_column_text_image",
                        "content": {"bullets": ["Supported."], "figure_ids": [], "table_ids": [], "metrics": []},
                        "citations": [{"evidence_id": "not_checked_without_evidence"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["qa", str(plan_path), "--out", str(report_path)])

    assert result.exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["issues"] == []


def _write_evidence(path, *, missing_image):
    pack = EvidencePack(
        source_files=[SourceRef(id="source_001", source_file="paper.pdf", title="Paper")],
        sections=[SectionEvidence(id="section_001", source_file="paper.pdf", page=1, heading="Method", text="Method evidence.")],
        figures=[FigureAsset(id="fig_001", source_file="paper.pdf", page=2, caption="Architecture.", path=str(missing_image))],
    )
    path.write_text(pack.to_json(), encoding="utf-8")


def _write_problem_plan(path):
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
            "message": "This cites an unavailable evidence item.",
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
            "id": "missing-image",
            "role": "figure_evidence",
            "title": "Missing Image",
            "message": "This figure exists but the image file does not.",
            "layout": "figure_with_caption",
            "content": {"bullets": ["Image path should exist."], "figure_ids": ["fig_001"], "table_ids": [], "metrics": []},
            "citations": [{"evidence_id": "fig_001"}],
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
