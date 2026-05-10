import json

from ppt_agent.domain.models import Citation, DeckIntent, PptSpec, SlideContent, SlideSpec
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.storage.plan_io import read_plan_document, write_plan_document


def test_legacy_title_bullets_spec_is_still_readable(tmp_path):
    path = tmp_path / "legacy-plan.json"
    path.write_text(
        json.dumps(
            {
                "title": "Legacy Deck",
                "request": {"topic": "Legacy Deck", "audience": "leadership"},
                "slides": [
                    {"title": "Opening", "bullets": ["Context", "Decision"]},
                    {"title": "Next", "bullets": ["Owner", "Timing"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    document = read_plan_document(path)

    assert document.source_type == "legacy_slides"
    assert document.spec.title == "Legacy Deck"
    assert document.spec.slides[0].title == "Opening"
    assert document.spec.slides[0].bullets == ["Context", "Decision"]


def test_schema_v2_spec_with_content_and_citations_is_readable(tmp_path):
    path = tmp_path / "schema-v2-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "title": "Traceable Deck",
                "audience": "research leads",
                "goal": "Explain the evidence-backed decision.",
                "narrative": "Move from problem to supported recommendation.",
                "slides": [
                    {
                        "id": "slide-001",
                        "role": "evidence",
                        "title": "Result",
                        "message": "The evaluation supports the recommendation.",
                        "layout": "comparison_table",
                        "content": {
                            "bullets": ["Accuracy improves.", "Latency remains acceptable."],
                            "figure_ids": ["fig_001"],
                            "table_ids": ["table_001"],
                            "metrics": [{"name": "accuracy", "value": "92%"}],
                        },
                        "citations": [{"evidence_id": "section-001", "page": 3, "source_file": "paper.pdf"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    document = read_plan_document(path)
    slide = document.spec.slides[0]

    assert document.schema_version == 2
    assert document.spec.audience == "research leads"
    assert document.spec.goal == "Explain the evidence-backed decision."
    assert slide.id == "slide-001"
    assert slide.core_message == "The evaluation supports the recommendation."
    assert slide.layout_hint == "comparison_table"
    assert slide.bullets == ["Accuracy improves.", "Latency remains acceptable."]
    assert slide.content.figure_ids == ["fig_001"]
    assert slide.citations[0].evidence_id == "section-001"
    assert slide.evidence_refs == ["section-001"]


def test_slide_citations_are_serializable_in_plan_document(tmp_path):
    path = tmp_path / "citations-plan.json"
    spec = PptSpec(
        title="Citation Deck",
        audience="leadership",
        slides=[
            SlideSpec(
                id="slide-001",
                role="claim",
                title="Claim",
                message="Evidence supports this claim.",
                content=SlideContent(bullets=["Supported point."]),
                citations=[Citation(evidence_id="section-001", page=2, source_file="paper.pdf")],
            )
        ],
    )

    write_plan_document(
        path,
        intent=DeckIntent(topic="Citation Deck", audience="leadership"),
        spec=spec,
        mode="plan",
        approved=False,
        transitions=["plan"],
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    restored = read_plan_document(path)

    assert payload["schema_version"] == 2
    assert payload["audience"] == "leadership"
    assert payload["slides"][0]["citations"] == [{"evidence_id": "section-001", "page": 2, "source_file": "paper.pdf"}]
    assert restored.spec.slides[0].citations[0].source_file == "paper.pdf"


def test_pptx_builder_accepts_schema_v2_slide_fields(tmp_path):
    spec = PptSpec(
        title="Buildable V2 Deck",
        audience="leadership",
        slides=[
            SlideSpec(
                id="slide-001",
                role="recommendation",
                title="Recommendation",
                message="Start with a scoped pilot.",
                layout="two_column_text_image",
                content=SlideContent(
                    bullets=["Name the owner.", "Set the success metric."],
                    metrics=[{"name": "pilot_length", "value": "6 weeks"}],
                ),
                citations=[Citation(evidence_id="section-001")],
                quality_checks=["has_citation"],
            )
        ],
    )

    output = tmp_path / "schema-v2.pptx"
    build_pptx(spec, output)

    assert output.exists()
    assert spec.slides[0].bullets == ["Name the owner.", "Set the success metric."]
    assert spec.slides[0].core_message == "Start with a scoped pilot."
