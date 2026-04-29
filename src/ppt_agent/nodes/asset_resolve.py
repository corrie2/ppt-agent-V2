from __future__ import annotations

from typing import Any

import httpx

from ppt_agent.domain.models import PptSpec, SlideSpec
from ppt_agent.runtime.image_assets import ImageAssetError, resolve_image_asset
from ppt_agent.utils.state import append_transition, state_get


def asset_resolve_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    resolved: list[SlideSpec] = []
    warnings: list[str] = list(state_get(state, "asset_warnings") or [])

    for slide in spec.slides:
        asset_kind = slide.visual_spec.get("asset_kind", "diagram")
        if asset_kind == "image":
            try:
                image_asset = resolve_image_asset(query=slide.image_query, prompt=slide.image_prompt)
                slide.resolved_asset = {
                    "type": "image_file",
                    "status": "downloaded",
                    "local_path": image_asset.local_path,
                    "source_url": image_asset.source_url,
                    "source_name": image_asset.source_name,
                    "license_note": image_asset.license_note,
                    "match_reason": image_asset.match_reason,
                    "caption": slide.image_caption,
                    "required": slide.visual_spec.get("visual_required", True),
                }
            except (ImageAssetError, OSError, ValueError, httpx.HTTPError) as exc:
                warning = f"Slide '{slide.title}' image resolution failed: {exc}"
                warnings.append(warning)
                slide.resolved_asset = {
                    "type": "image_placeholder",
                    "status": "planned",
                    "query": slide.image_query,
                    "prompt": slide.image_prompt,
                    "caption": slide.image_caption,
                    "required": slide.visual_spec.get("visual_required", True),
                    "warning": warning,
                    "fallback_reason": "image_search_failed",
                }
        else:
            slide.resolved_asset = {
                "type": "diagram_placeholder",
                "status": "planned",
                "diagram_kind": slide.visual_type,
                "required": slide.visual_spec.get("visual_required", True),
            }
        resolved.append(slide)

    spec.slides = resolved
    return {
        "spec": spec.model_dump(mode="json"),
        "asset_warnings": warnings,
        "transitions": append_transition(state, "asset_resolve"),
    }
