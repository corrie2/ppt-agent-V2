from ppt_agent.domain.models import DeckIntent, PptSpec, SlideSpec
from ppt_agent.nodes.qa import qa_node
from ppt_agent.nodes.repair import repair_node


def test_repair_fills_missing_slide_content():
    spec = PptSpec(title="T", audience="A", slides=[SlideSpec(title="", bullets=[])])
    state = {"spec": spec.model_dump(mode="json"), "qa_issues": qa_node({"spec": spec.model_dump(mode="json")})["qa_issues"]}

    repaired = repair_node(state)
    repaired_spec = PptSpec.model_validate(repaired["spec"])

    assert repaired["repair_attempts"] == 1
    assert repaired_spec.slides[0].title == "Slide 1"
    assert repaired_spec.slides[0].bullets
    assert repaired_spec.slides[0].visual_type


def test_academic_qa_flags_missing_evidence_specific_claims_and_placeholders():
    slides = [
        SlideSpec(
            title="研究补充 1",
            objective="Explain the result.",
            core_message="The method improves latency by 35%.",
            bullets=["论文未提供", "论文未提供"],
            supporting_points=["论文未提供", "论文未提供"],
            visual_type="editorial_diagram",
            layout_hint="two_column_text_image",
        ),
        SlideSpec(
            title="Result",
            objective="Explain the result.",
            core_message="The model outperforms baselines significantly.",
            bullets=["not provided by source", "not provided by source"],
            supporting_points=["not provided by source"],
            visual_type="editorial_diagram",
            layout_hint="two_column_text_image",
            evidence_refs=["paper:digest"],
        ),
    ]
    spec = PptSpec(
        title="Paper Deck",
        audience="研究生",
        source_digest={"sources": [{"title": "Paper", "results": "latency is evaluated"}]},
        slides=slides,
    )

    result = qa_node({"spec": spec.model_dump(mode="json")})
    codes = {issue["code"] for issue in result["qa_issues"]}

    assert "missing_evidence_refs" in codes
    assert "unsupported_specific_conclusion" in codes
    assert "research_supplement_placeholder" in codes
    assert "too_many_not_provided_markers" in codes


def test_guizang_qa_flags_preference_violations():
    dense = " ".join(["This paragraph contains too much explanatory body copy for an editorial slide."] * 10)
    slides = [
        SlideSpec(
            title="One",
            objective="Explain one.",
            core_message=dense,
            bullets=["A", "B", "C", "D", "E", "F"],
            supporting_points=["S1", "S2"],
            visual_type="editorial_panel",
            layout_hint="same_layout",
            visual_spec={"visual_required": True},
        ),
        SlideSpec(
            title="Two",
            objective="Explain two.",
            core_message="A concise message for slide two.",
            bullets=["A", "B"],
            supporting_points=["S1", "S2"],
            visual_type="editorial_panel",
            layout_hint="same_layout",
        ),
        SlideSpec(
            title="Three",
            objective="Explain three.",
            core_message="A concise message for slide three.",
            bullets=["A", "B"],
            supporting_points=["S1", "S2"],
            visual_type="editorial_panel",
            layout_hint="same_layout",
        ),
    ]
    spec = PptSpec(
        title="Guizang Deck",
        audience="研究生",
        output_format="html",
        applied_skills=["guizang-ppt-skill"],
        slides=slides,
    )
    intent = DeckIntent(
        topic="Guizang Deck",
        project_preferences=[{"preference": "不要空方框"}, {"preference": "正文太多"}],
    )

    result = qa_node({"spec": spec.model_dump(mode="json"), "intent": intent.model_dump(mode="json")})
    codes = {issue["code"] for issue in result["qa_issues"]}

    assert "guizang_empty_box_preference_violation" in codes
    assert "guizang_text_too_dense" in codes
    assert "guizang_consecutive_homogenous_layout" in codes
