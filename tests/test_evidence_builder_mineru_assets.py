from ppt_agent.ingest import EvidenceBuilder
from ppt_agent.ingest.parser import ParseResult


def test_evidence_builder_extracts_mineru_image_chart_and_table_assets(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    parse_result = ParseResult(
        markdown_text="",
        source_path=source,
        assets_dir=assets_dir,
        content_list=[
            {
                "type": "image",
                "img_path": "img-1.png",
                "image_caption": ["Architecture", "overview"],
                "page_idx": 2,
                "bbox": [10, 20, 110, 220],
            },
            {
                "type": "chart",
                "image_path": "chart-1.png",
                "chart_caption": "Accuracy by method",
                "page_idx": 3,
                "bbox": [1.5, 2.5, 3.5, 4.5],
            },
            {
                "type": "table",
                "path": "table-1.html",
                "table_caption": ["Main", "results"],
                "table_body": "Method | Score",
                "page_idx": 4,
                "bbox": [5, 6, 7, 8],
            },
        ],
    )

    pack = EvidenceBuilder().build(parse_result)

    assert [figure.id for figure in pack.figures] == ["fig_001", "fig_002"]
    assert pack.figures[0].source_file == "paper.pdf"
    assert pack.figures[0].page == 2
    assert pack.figures[0].bbox == (10.0, 20.0, 110.0, 220.0)
    assert pack.figures[0].caption == "Architecture overview"
    assert pack.figures[0].path == str(assets_dir / "img-1.png")
    assert pack.figures[1].page == 3
    assert pack.figures[1].caption == "Accuracy by method"
    assert pack.figures[1].path == str(assets_dir / "chart-1.png")

    assert [table.id for table in pack.tables] == ["table_001"]
    assert pack.tables[0].source_file == "paper.pdf"
    assert pack.tables[0].page == 4
    assert pack.tables[0].bbox == (5.0, 6.0, 7.0, 8.0)
    assert pack.tables[0].caption == "Main results"
    assert pack.tables[0].text == "Method | Score"
    assert pack.tables[0].path == str(assets_dir / "table-1.html")
