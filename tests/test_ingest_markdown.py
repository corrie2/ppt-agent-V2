from ppt_agent.domain.evidence import EvidencePack
from ppt_agent.ingest import EvidenceBuilder, MarkdownParser, load_evidence_pack


def test_markdown_parser_builds_evidence_pack_sections(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text(
        "# Abstract\n"
        "This paper introduces the method.\n\n"
        "## Results\n"
        "The evaluation reports stronger accuracy.\n",
        encoding="utf-8",
    )

    parse_result = MarkdownParser().parse(source)
    pack = EvidenceBuilder().build(parse_result)

    assert pack.source_files[0].source_file == "paper.md"
    assert len(pack.sections) == 2
    assert pack.sections[0].id
    assert pack.sections[0].source_file == "paper.md"
    assert pack.sections[0].page == 1
    assert pack.sections[0].heading == "Abstract"
    assert "introduces the method" in pack.sections[0].text
    assert pack.sections[1].heading == "Results"


def test_load_evidence_pack_uses_markdown_parser_by_default(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("No heading body.", encoding="utf-8")

    pack = load_evidence_pack(source)

    assert pack.sections[0].source_file == "notes.md"
    assert pack.sections[0].page == 1
    assert pack.sections[0].text == "No heading body."


def test_markdown_evidence_pack_json_round_trip(tmp_path):
    source = tmp_path / "deck.md"
    source.write_text("# One\nContent for one.\n", encoding="utf-8")
    pack = load_evidence_pack(source)
    path = tmp_path / "evidence.json"

    path.write_text(pack.to_json(), encoding="utf-8")
    restored = EvidencePack.from_json(path.read_text(encoding="utf-8"))

    assert restored == pack
