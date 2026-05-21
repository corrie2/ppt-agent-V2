from __future__ import annotations

from pathlib import Path
from typing import Any

from ppt_agent.domain.models import PptSpec
from ppt_agent.runtime.visual_quality import evaluate_pptx_visual_quality, visual_quality_report_path
from ppt_agent.utils.state import append_transition, state_get


def visual_quality_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    artifact = state_get(state, "artifact")
    pptx_path = Path(artifact["path"] if isinstance(artifact, dict) else artifact.path)
    report_path = visual_quality_report_path(pptx_path)
    report = evaluate_pptx_visual_quality(spec, pptx_path, report_path=report_path)
    return {
        "visual_quality_report": report.model_dump(mode="json"),
        "visual_quality_report_path": str(report_path),
        "transitions": append_transition(state, "visual_quality"),
    }
