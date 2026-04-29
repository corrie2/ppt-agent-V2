from ppt_agent.domain.models import DeckIntent
from ppt_agent.nodes.plan import _deterministic_spec


def test_deterministic_spec_has_minimum_structure():
    spec = _deterministic_spec(DeckIntent(topic="AI Sales Enablement"))

    assert spec.title == "AI Sales Enablement"
    assert len(spec.slides) >= 8
    assert all(slide.title for slide in spec.slides)
    assert all(slide.bullets for slide in spec.slides)
    assert all(slide.visual_type for slide in spec.slides)
    assert any(slide.image_query for slide in spec.slides)


def test_deterministic_spec_without_evidence_does_not_invent_forbidden_claims():
    spec = _deterministic_spec(DeckIntent(topic="AI Sales Enablement"))
    text = " ".join(
        [spec.title]
        + [
            " ".join([slide.title, slide.objective, slide.core_message, " ".join(slide.bullets), " ".join(slide.supporting_points)])
            for slide in spec.slides
        ]
    ).lower()

    for forbidden in ("roi", "github star", "neurips", "sigmod", "customer case", "citation count"):
        assert forbidden not in text
