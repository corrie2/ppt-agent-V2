import json

from typer.testing import CliRunner

from ppt_agent.cli import main
from ppt_agent.cli.main import app
from ppt_agent.ingest.parser import ParseResult


runner = CliRunner()


def test_ingest_markdown_writes_evidence_json_and_summary(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text(
        "# Abstract\n"
        "This paper introduces the method.\n\n"
        "## Results\n"
        "The evaluation reports stronger accuracy.\n",
        encoding="utf-8",
    )
    out = tmp_path / ".ppt-agent" / "evidence.json"

    result = runner.invoke(app, ["ingest", str(source), "--out", str(out)])

    assert result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["sections"]) == 2
    assert payload["sections"][0]["heading"] == "Abstract"
    assert payload["figures"] == []
    assert payload["tables"] == []
    assert "Sections: 2" in result.output
    assert "Figures: 0" in result.output
    assert "Tables: 0" in result.output
    assert f"Output: {out}" in result.output


def test_ingest_pdf_auto_uses_mineru_adapter_without_real_mineru(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    out = tmp_path / "evidence.json"
    workdir = tmp_path / "parsed"
    calls = []

    class FakeMinerUAdapter:
        def __init__(self, options=None):
            self.options = options

        def parse(self, input_path, output_dir):
            calls.append((input_path, output_dir, self.options))
            return ParseResult(
                markdown_text="",
                source_path=input_path,
                assets_dir=output_dir / "images",
                content_list=[
                    {"type": "text", "text": "Parsed PDF content.", "page": 1},
                    {"type": "image", "caption": "Architecture", "path": "figure.png", "page": 2},
                    {"type": "table", "caption": "Scores", "text": "A | B", "page": 3},
                ],
            )

    monkeypatch.setattr(main, "MinerUAdapter", FakeMinerUAdapter)

    result = runner.invoke(app, ["ingest", str(source), "--workdir", str(workdir), "--out", str(out)])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == source
    assert calls[0][1] == workdir
    assert calls[0][2].backend == "pipeline"
    assert calls[0][2].method == "auto"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["sections"]) == 1
    assert len(payload["figures"]) == 1
    assert len(payload["tables"]) == 1
    assert payload["figures"][0]["caption"] == "Architecture"
    assert payload["tables"][0]["caption"] == "Scores"
    assert "Sections: 1" in result.output
    assert "Figures: 1" in result.output
    assert "Tables: 1" in result.output


def test_ingest_pdf_passes_mineru_cli_options(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    out = tmp_path / "evidence.json"
    workdir = tmp_path / "parsed"
    calls = []

    class FakeMinerUAdapter:
        def __init__(self, options=None):
            self.options = options

        def parse(self, input_path, output_dir):
            calls.append((input_path, output_dir, self.options))
            return ParseResult(
                markdown_text="# Parsed\nPDF content.\n",
                source_path=input_path,
                assets_dir=None,
                content_list=None,
            )

    monkeypatch.setattr(main, "MinerUAdapter", FakeMinerUAdapter)

    result = runner.invoke(
        app,
        [
            "ingest",
            str(source),
            "--parser",
            "mineru",
            "--workdir",
            str(workdir),
            "--out",
            str(out),
            "--mineru-backend",
            "hybrid-auto-engine",
            "--mineru-method",
            "ocr",
            "--mineru-lang",
            "en",
            "--mineru-start",
            "1",
            "--mineru-end",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    options = calls[0][2]
    assert options.backend == "hybrid-auto-engine"
    assert options.method == "ocr"
    assert options.lang == "en"
    assert options.start == 1
    assert options.end == 3


def test_doctor_reports_mineru_status(monkeypatch):
    monkeypatch.setattr(main.shutil, "which", lambda command: "mineru.exe")
    monkeypatch.setattr(main.metadata, "version", lambda package: "3.1.11")

    class Completed:
        stdout = "mineru, version 3.1.11\n"
        stderr = ""

    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: Completed())

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "MinerU:" in result.output
    assert "command: found" in result.output
    assert "package: 3.1.11" in result.output
    assert "cli: mineru, version 3.1.11" in result.output


def test_ingest_auto_rejects_unknown_format(tmp_path):
    source = tmp_path / "paper.txt"
    source.write_text("plain text", encoding="utf-8")
    out = tmp_path / "evidence.json"

    result = runner.invoke(app, ["ingest", str(source), "--out", str(out)])

    assert result.exit_code == 1
    assert not out.exists()
    assert "unsupported input format for auto parser" in result.output
