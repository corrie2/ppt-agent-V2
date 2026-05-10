from ppt_agent.domain.evidence import (
    ClaimEvidence,
    EvidencePack,
    FigureAsset,
    SectionEvidence,
    SourceRef,
    TableAsset,
)


def test_evidence_pack_json_round_trip_preserves_items():
    pack = EvidencePack(
        source_files=[
            SourceRef(
                id="source-1",
                source_file="paper.pdf",
                path="input/paper.pdf",
                title="Paper",
                page_count=12,
            )
        ],
        sections=[
            SectionEvidence(
                id="section-1",
                source_file="paper.pdf",
                page=1,
                bbox=(10.0, 20.0, 300.0, 420.0),
                heading="Abstract",
                text="The paper introduces the main method.",
            )
        ],
        figures=[
            FigureAsset(
                id="figure-1",
                source_file="paper.pdf",
                page=2,
                caption="System overview.",
                path="assets/figure-1.png",
            )
        ],
        tables=[
            TableAsset(
                id="table-1",
                source_file="paper.pdf",
                page=3,
                caption="Results table.",
                headers=["Metric", "Value"],
                rows=[["Accuracy", "92"]],
            )
        ],
        claims=[
            ClaimEvidence(
                id="claim-1",
                source_file="paper.pdf",
                page=4,
                text="The method improves accuracy.",
                supporting_evidence_ids=["section-1", "table-1"],
                confidence=0.8,
            )
        ],
        metadata={"parser": "fixture"},
    )

    restored = EvidencePack.from_json(pack.to_json())

    assert restored == pack
    assert restored.source_files[0].source_file == "paper.pdf"
    assert restored.sections[0].bbox == (10.0, 20.0, 300.0, 420.0)
    assert restored.figures[0].path == "assets/figure-1.png"
    assert restored.tables[0].rows == [["Accuracy", "92"]]
    assert restored.claims[0].supporting_evidence_ids == ["section-1", "table-1"]


def test_evidence_pack_collects_all_evidence_items():
    pack = EvidencePack(
        sections=[SectionEvidence(id="s1", source_file="doc.pdf", text="Section")],
        figures=[FigureAsset(id="f1", source_file="doc.pdf", caption="Figure")],
        tables=[TableAsset(id="t1", source_file="doc.pdf", caption="Table")],
        claims=[ClaimEvidence(id="c1", source_file="doc.pdf", text="Claim")],
    )

    assert [item.id for item in pack.evidence_items()] == ["s1", "f1", "t1", "c1"]
