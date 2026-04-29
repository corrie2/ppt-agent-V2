from __future__ import annotations

from typing import Any

from ppt_agent.domain.models import PptSpec, QaIssue, SlideSpec
from ppt_agent.utils.state import append_transition, state_get


def repair_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    issues = [QaIssue.model_validate(issue) for issue in state_get(state, "qa_issues", [])]

    if any(issue.code == "too_few_slides" for issue in issues):
        spec.slides.append(
            SlideSpec(
                title="Appendix",
                objective="Provide supporting reference material for the executive review.",
                core_message="Appendix material supports the main recommendation with reference details.",
                bullets=["Reference assumptions", "Supporting data points", "Open governance questions"],
                supporting_points=["Use only if the audience asks for underlying detail."],
                visual_type="three_card_summary",
                layout_hint="three_card_summary",
                style_tags=["appendix", "reference"],
            )
        )

    repaired_slides: list[SlideSpec] = []
    for index, slide in enumerate(spec.slides, start=1):
        if not slide.title.strip():
            slide.title = f"Slide {index}"
        if not slide.bullets:
            slide.bullets = [
                "Clarify the business implication for the audience.",
                "Add the operational or commercial detail behind the point.",
                "Close with the action implied by the slide.",
            ]
        if not slide.objective:
            slide.objective = f"Explain the main point of slide {index} in business terms."
        if not slide.core_message:
            slide.core_message = f"Slide {index} should land one clear business takeaway."
        if not slide.supporting_points:
            slide.supporting_points = [
                "Add evidence, examples, or metrics that support the argument.",
                "Show how this point affects execution, risk, or outcome.",
            ]
        if not slide.visual_type:
            slide.visual_type = "three_card_summary"
        if not slide.layout_hint:
            slide.layout_hint = "three_card_summary"
        if not slide.style_tags:
            slide.style_tags = ["business", "clean"]
        if slide.visual_type in {"hero_image", "market_scene", "workspace_photo", "customer_moment"}:
            if not slide.image_query:
                slide.image_query = f"{slide.title} business presentation visual"
            if not slide.image_prompt:
                slide.image_prompt = f"Professional business visual for {slide.title}"
            if not slide.image_caption:
                slide.image_caption = slide.core_message
            if not slide.image_rationale:
                slide.image_rationale = "Repair step added a relevant supporting visual brief."
        if not slide.visual_spec:
            slide.visual_spec = {
                "visual_required": True,
                "visual_type": slide.visual_type,
                "layout_hint": slide.layout_hint,
                "style_tags": slide.style_tags,
                "asset_kind": "image"
                if slide.visual_type in {"hero_image", "market_scene", "workspace_photo", "customer_moment"}
                else "diagram",
            }
        if not slide.resolved_asset:
            slide.resolved_asset = {
                "type": "image_placeholder"
                if slide.visual_spec.get("asset_kind") == "image"
                else "diagram_placeholder",
                "status": "planned",
                "required": True,
            }
        repaired_slides.append(slide)

    spec.slides = repaired_slides
    attempts = int(state_get(state, "repair_attempts") or 0) + 1
    return {
        "spec": spec.model_dump(mode="json"),
        "repair_attempts": attempts,
        "qa_issues": [],
        "transitions": append_transition(state, "repair"),
    }
