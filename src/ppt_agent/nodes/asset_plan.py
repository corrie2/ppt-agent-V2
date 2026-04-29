from __future__ import annotations

from typing import Any

from ppt_agent.domain.models import PptSpec, SlideSpec
from ppt_agent.utils.state import append_transition, state_get


IMAGE_VISUALS = {"hero_image", "market_scene", "workspace_photo", "customer_moment"}


def asset_plan_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    planned: list[SlideSpec] = []

    for index, slide in enumerate(spec.slides, start=1):
        visual_type = slide.visual_type or _default_visual_type(index)
        layout_hint = slide.layout_hint or _default_layout_for_visual(visual_type)
        style_tags = slide.style_tags or ["business", "clean", "executive"]
        visual_spec = {
            "visual_required": visual_type not in {"text_brief"},
            "visual_type": visual_type,
            "layout_hint": layout_hint,
            "style_tags": style_tags,
        }
        if visual_type in IMAGE_VISUALS:
            visual_spec["asset_kind"] = "image"
            slide.image_query = slide.image_query or f"{slide.title} {slide.core_message}".strip()
            slide.image_prompt = slide.image_prompt or (
                f"Professional business presentation image for {slide.title}. "
                f"Message: {slide.core_message or slide.objective}."
            )
            slide.image_caption = slide.image_caption or slide.core_message or slide.objective
            slide.image_rationale = slide.image_rationale or "Use a relevant business visual to anchor the argument."
        else:
            visual_spec["asset_kind"] = "diagram"
        slide.visual_type = visual_type
        slide.layout_hint = layout_hint
        slide.style_tags = style_tags
        slide.visual_spec = visual_spec
        planned.append(slide)

    spec.slides = planned
    return {"spec": spec.model_dump(mode="json"), "transitions": append_transition(state, "asset_plan")}


def _default_visual_type(index: int) -> str:
    defaults = {
        1: "hero_image",
        2: "comparison_table",
        3: "three_card_summary",
        4: "process_timeline",
    }
    return defaults.get(index, "workspace_photo")


def _default_layout_for_visual(visual_type: str) -> str:
    mapping = {
        "hero_image": "title_cover",
        "market_scene": "hero_image_plus_argument",
        "workspace_photo": "two_column_text_image",
        "customer_moment": "hero_image_plus_argument",
        "three_card_summary": "three_card_summary",
        "process_timeline": "process_timeline",
        "comparison_table": "comparison_table",
    }
    return mapping.get(visual_type, "two_column_text_image")
