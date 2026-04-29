from __future__ import annotations

from pathlib import Path
from typing import Any

from ppt_agent.domain.models import DeckIntent, PptSpec
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.utils.state import append_transition, state_get


def build_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    intent = DeckIntent.model_validate(state_get(state, "intent"))
    artifact = build_pptx(spec, Path(intent.output_path))
    return {"artifact": artifact.model_dump(mode="json"), "transitions": append_transition(state, "build")}
