import json
import zipfile

from typer.testing import CliRunner

from ppt_agent.cli.main import app
from ppt_agent.domain.evidence import EvidencePack, FigureAsset, SectionEvidence, SourceRef


runner = CliRunner()


def test_build_writes_resolved_citations_to_speaker_notes(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        EvidencePack(
            source_files=[SourceRef(id="source_001", source_file="paper.pdf", title="Paper")],
            figures=[FigureAsset(id="fig_001", source_file="paper.pdf", page=3, caption="Architecture overview.", path=str(tmp_path / "missing.png"))],
            sections=[SectionEvidence(id="section_001", source_file="paper.pdf", page=2, heading="Method", text="The method builds the graph.")],
        ).to_json(),
        encoding="utf-8",
    )
    plan_path = _write_plan(
        tmp_path,
        citations=[
            {"evidence_id": "fig_001"},
            {"evidence_id": "section_001"},
        ],
    )
    output_path = tmp_path / "deck.pptx"

    result = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(output_path)])

    assert result.exit_code == 0
    notes_xml = _notes_xml(output_path)
    assert "Source Trace:" in notes_xml
    assert "Source: paper.pdf p.3 fig_001 - Architecture overview." in notes_xml
    assert "Source: paper.pdf p.2 section_001 - Method" in notes_xml


def test_build_writes_unresolved_citation_without_failing(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(EvidencePack().to_json(), encoding="utf-8")
    plan_path = _write_plan(tmp_path, citations=[{"evidence_id": "missing_001"}])
    output_path = tmp_path / "deck-unresolved.pptx"

    result = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    notes_xml = _notes_xml(output_path)
    assert "Source: unresolved missing_001" in notes_xml


def _write_plan(tmp_path, *, citations):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "title": "Trace Deck",
                "audience": "reviewers",
                "slides": [
                    {
                        "id": "slide-001",
                        "role": "evidence",
                        "title": "Evidence Slide",
                        "message": "This slide has citations.",
                        "layout": "two_column_text_image",
                        "content": {"bullets": ["Review cited evidence."], "figure_ids": [], "table_ids": [], "metrics": []},
                        "citations": citations,
                        "speaker_notes": "Existing presenter note.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _notes_xml(pptx_path):
    with zipfile.ZipFile(pptx_path) as archive:
        note_names = sorted(name for name in archive.namelist() if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml"))
        assert note_names
        return archive.read(note_names[0]).decode("utf-8", errors="ignore")
