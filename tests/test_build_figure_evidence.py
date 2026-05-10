import base64
import json
import zipfile

from typer.testing import CliRunner

from ppt_agent.cli.main import app
from ppt_agent.domain.evidence import EvidencePack, FigureAsset, SourceRef


runner = CliRunner()


def test_build_with_evidence_figure_image_writes_pptx(tmp_path):
    image_path = tmp_path / "figure.png"
    _write_png(image_path)
    evidence_path = _write_evidence(tmp_path, figure_path=image_path)
    plan_path = _write_figure_plan(tmp_path)
    output_path = tmp_path / "deck.pptx"

    result = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    with zipfile.ZipFile(output_path) as archive:
        assert any(name.startswith("ppt/media/") for name in archive.namelist())


def test_build_resolves_relative_figure_path_from_evidence_location(tmp_path, monkeypatch):
    image_dir = tmp_path / "figures"
    image_dir.mkdir()
    image_path = image_dir / "figure.png"
    _write_png(image_path)
    evidence_path = _write_evidence(tmp_path, figure_path="figures/figure.png")
    plan_path = _write_figure_plan(tmp_path)
    output_path = tmp_path / "deck-relative.pptx"
    unrelated_cwd = tmp_path / "cwd"
    unrelated_cwd.mkdir()

    monkeypatch.chdir(unrelated_cwd)

    result = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(output_path)])

    assert result.exit_code == 0
    with zipfile.ZipFile(output_path) as archive:
        assert any(name.startswith("ppt/media/") for name in archive.namelist())
        slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8", errors="ignore")
    assert "Figure image missing" not in slide_xml


def test_build_prefers_existing_workspace_relative_figure_path(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    image_dir = workspace / ".ppt-agent" / "parsed" / "images"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "figure.png"
    _write_png(image_path)
    output_dir = workspace / "output"
    output_dir.mkdir()
    evidence_path = _write_evidence(output_dir, figure_path=".ppt-agent/parsed/images/figure.png")
    plan_path = _write_figure_plan(output_dir)
    output_path = output_dir / "deck-workspace-relative.pptx"

    monkeypatch.chdir(workspace)

    result = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(output_path)])

    assert result.exit_code == 0
    with zipfile.ZipFile(output_path) as archive:
        assert any(name.startswith("ppt/media/") for name in archive.namelist())
        slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8", errors="ignore")
    assert "Figure image missing" not in slide_xml


def test_build_with_missing_evidence_figure_image_does_not_crash(tmp_path):
    evidence_path = _write_evidence(tmp_path, figure_path=tmp_path / "missing.png")
    plan_path = _write_figure_plan(tmp_path)
    output_path = tmp_path / "deck-missing.pptx"

    result = runner.invoke(app, ["build", str(plan_path), "--evidence", str(evidence_path), "--out", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    with zipfile.ZipFile(output_path) as archive:
        slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8", errors="ignore")
    assert "Figure image missing" in slide_xml
    assert "fig_001" in slide_xml


def _write_figure_plan(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "title": "Figure Deck",
                "audience": "research leads",
                "slides": [
                    {
                        "id": "slide-001",
                        "role": "figure_evidence",
                        "title": "Architecture Figure",
                        "message": "The architecture figure summarizes the system.",
                        "layout": "figure_with_caption",
                        "content": {
                            "bullets": ["The figure shows the main processing stages."],
                            "figure_ids": ["fig_001"],
                            "table_ids": [],
                            "metrics": [],
                        },
                        "citations": [{"evidence_id": "fig_001", "page": 2, "source_file": "paper.pdf"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_evidence(tmp_path, *, figure_path):
    path = tmp_path / "evidence.json"
    pack = EvidencePack(
        source_files=[SourceRef(id="source_001", source_file="paper.pdf", title="Figure Paper")],
        figures=[
            FigureAsset(
                id="fig_001",
                source_file="paper.pdf",
                page=2,
                caption="Architecture of the system.",
                path=str(figure_path),
            )
        ],
    )
    path.write_text(pack.to_json(), encoding="utf-8")
    return path


def _write_png(path):
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
    )
