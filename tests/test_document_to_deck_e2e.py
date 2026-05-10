import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from ppt_agent.cli.main import app


runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def test_document_to_deck_fixture_end_to_end_without_external_services(tmp_path):
    parsed_dir = tmp_path / "parsed"
    images_dir = parsed_dir / "images"
    images_dir.mkdir(parents=True)
    shutil.copy2(FIXTURES / "sample_paper.md", parsed_dir / "sample_paper.md")
    shutil.copy2(FIXTURES / "sample_content_list.json", parsed_dir / "content_list.json")
    shutil.copy2(FIXTURES / "sample_image.png", images_dir / "sample_image.png")
    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n% fixture only\n")

    evidence_path = tmp_path / "evidence.json"
    plan_path = tmp_path / "plan.json"
    deck_path = tmp_path / "deck.pptx"
    qa_path = tmp_path / "qa_report.json"
    repaired_path = tmp_path / "repaired_plan.json"

    ingest = runner.invoke(
        app,
        [
            "ingest",
            str(fake_pdf),
            "--parser",
            "mineru",
            "--workdir",
            str(parsed_dir),
            "--out",
            str(evidence_path),
        ],
    )
    assert ingest.exit_code == 0
    assert evidence_path.exists()
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence_payload["figures"]

    plan = runner.invoke(app, ["plan", "--evidence", str(evidence_path), "--spec", str(plan_path)])
    assert plan.exit_code == 0
    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_payload["schema_version"] == 2
    assert any(slide["layout"] == "figure_with_caption" for slide in plan_payload["slides"])

    build = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(deck_path)])
    assert build.exit_code == 0
    assert deck_path.exists()

    qa = runner.invoke(app, ["qa", str(plan_path), "--evidence", str(evidence_path), "--out", str(qa_path)])
    assert qa_path.exists()
    qa_payload = json.loads(qa_path.read_text(encoding="utf-8"))
    assert "issues" in qa_payload

    repair = runner.invoke(
        app,
        ["repair", str(plan_path), "--qa", str(qa_path), "--evidence", str(evidence_path), "--out", str(repaired_path)],
    )
    assert repair.exit_code == 0
    assert repaired_path.exists()
    repaired_payload = json.loads(repaired_path.read_text(encoding="utf-8"))
    assert repaired_payload["schema_version"] == 2
