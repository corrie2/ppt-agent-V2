import pytest

from ppt_agent.domain.evidence import EvidencePack
from ppt_agent.ingest import EvidenceBuilder, MinerUAdapter
from ppt_agent.ingest.mineru_adapter import MinerUOptions


def test_mineru_adapter_reads_existing_output_without_running_command(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output_dir = tmp_path / "mineru-output"
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True)
    (output_dir / "paper.md").write_text("# Abstract\nThe method is described.\n", encoding="utf-8")
    (output_dir / "content_list.json").write_text(
        """
[
  {"type": "title", "text": "Abstract", "page": 1},
  {"type": "text", "text": "The method is described.", "page": 1, "bbox": [1, 2, 3, 4]},
  {"type": "image", "caption": "System figure", "path": "images/figure.png", "page": 2},
  {"type": "chart", "caption": "Result chart", "path": "images/chart.png", "page": 3},
  {"type": "table", "caption": "Result table", "text": "A | B", "page": 4}
]
""",
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("mineru command should not be called when output exists")

    monkeypatch.setattr("subprocess.run", fail_run)

    parse_result = MinerUAdapter().parse(source, output_dir)
    pack = EvidenceBuilder().build(parse_result)

    assert parse_result.markdown_text.startswith("# Abstract")
    assert parse_result.assets_dir == images_dir
    assert parse_result.content_list is not None
    assert {item["type"] for item in parse_result.content_list} >= {"title", "text", "image", "chart", "table"}
    assert pack.sections[1].source_file == "paper.pdf"
    assert pack.sections[1].page == 1
    assert pack.sections[1].bbox == (1.0, 2.0, 3.0, 4.0)
    assert len(pack.figures) == 2
    assert pack.figures[0].caption == "System figure"
    assert pack.figures[0].path == str(images_dir / "figure.png")
    assert len(pack.tables) == 1
    assert pack.tables[0].caption == "Result table"


def test_mineru_adapter_reads_nested_named_output(monkeypatch, tmp_path):
    source = tmp_path / "JAG.pdf"
    source.write_bytes(b"%PDF")
    output_dir = tmp_path / "mineru-output"
    nested_dir = output_dir / "JAG" / "auto"
    images_dir = nested_dir / "images"
    images_dir.mkdir(parents=True)
    (nested_dir / "JAG.md").write_text("# Paper\nParsed body.\n", encoding="utf-8")
    (nested_dir / "JAG_content_list.json").write_text(
        """
[
  {"type": "text", "text": "Paper Title", "text_level": 1, "page_idx": 0},
  {"type": "text", "text": "Parsed body.", "page_idx": 0, "bbox": [1, 2, 3, 4]},
  {"type": "chart", "caption": "Throughput chart", "img_path": "images/chart.jpg", "page_idx": 2},
  {"type": "table", "table_caption": ["Dataset table"], "img_path": "images/table.jpg", "table_body": "<table></table>", "page_idx": 3}
]
""",
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("mineru command should not be called when nested output exists")

    monkeypatch.setattr("subprocess.run", fail_run)

    parse_result = MinerUAdapter().parse(source, output_dir)
    pack = EvidenceBuilder().build(parse_result)

    assert parse_result.assets_dir == images_dir
    assert parse_result.content_list is not None
    assert parse_result.content_list[0]["type"] == "title"
    assert parse_result.content_list[0]["page"] == 1
    assert len(pack.sections) == 2
    assert len(pack.figures) == 1
    assert len(pack.tables) == 1
    assert pack.figures[0].path == str(images_dir / "chart.jpg")
    assert pack.tables[0].path == str(images_dir / "table.jpg")
    assert pack.tables[0].page == 4


def test_mineru_adapter_raises_clear_error_when_command_missing(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")

    monkeypatch.setattr("shutil.which", lambda command: None)

    monkeypatch.setattr("ppt_agent.ingest.mineru_adapter._get_pdf_page_count", lambda path: 1)

    with pytest.raises(RuntimeError, match="MinerU is not installed or not found in PATH"):
        MinerUAdapter().parse(source, tmp_path / "missing-output")


def test_mineru_adapter_can_invoke_mocked_command(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output_dir = tmp_path / "mineru-output"

    monkeypatch.setattr("ppt_agent.ingest.mineru_adapter._get_pdf_page_count", lambda path: 1)

    monkeypatch.setattr("shutil.which", lambda command: "mineru")

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper.md").write_text("# Generated\nCreated by fake mineru.\n", encoding="utf-8")
        (output_dir / "content_list.json").write_text('[{"type": "text", "text": "Created by fake mineru.", "page": 1}]', encoding="utf-8")
        return None

    monkeypatch.setattr("subprocess.run", fake_run)

    parse_result = MinerUAdapter().parse(source, output_dir)
    pack = EvidenceBuilder().build(parse_result)
    restored = EvidencePack.from_json(pack.to_json())

    assert "Generated" in parse_result.markdown_text
    assert restored.sections[0].text == "Created by fake mineru."
    assert commands == [["mineru", "-p", str(source), "-o", str(output_dir)]]


def test_mineru_adapter_passes_cli_options_to_command(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output_dir = tmp_path / "mineru-output"
    commands = []

    monkeypatch.setattr("ppt_agent.ingest.mineru_adapter._get_pdf_page_count", lambda path: 1)

    monkeypatch.setattr("shutil.which", lambda command: "mineru")

    def fake_run(command, **kwargs):
        commands.append(command)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper.md").write_text("# Generated\nCreated by fake mineru.\n", encoding="utf-8")
        return None

    monkeypatch.setattr("subprocess.run", fake_run)

    MinerUAdapter(
        options=MinerUOptions(
            backend="pipeline",
            method="ocr",
            lang="en",
            start=1,
            end=2,
            formula=False,
            table=True,
            image_analysis=False,
        )
    ).parse(source, output_dir)

    assert commands == [
        [
            "mineru",
            "-p",
            str(source),
            "-o",
            str(output_dir),
            "--method",
            "ocr",
            "--backend",
            "pipeline",
            "--lang",
            "en",
            "--start",
            "1",
            "--end",
            "2",
            "--formula",
            "false",
            "--table",
            "true",
            "--image-analysis",
            "false",
        ]
    ]
