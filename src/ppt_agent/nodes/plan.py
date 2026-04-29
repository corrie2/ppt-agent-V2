from __future__ import annotations

from typing import Any

from ppt_agent.domain.models import DeckIntent, PptSpec
from ppt_agent.runtime.planner import build_plan_spec, deterministic_plan_spec
from ppt_agent.utils.state import append_transition, state_get


def plan_node(state: dict[str, Any]) -> dict[str, Any]:
    intent = DeckIntent.model_validate(state_get(state, "intent"))
    spec = build_plan_spec(
        intent,
        provider=state_get(state, "planner_provider"),
        model=state_get(state, "planner_model"),
    )
    return {"spec": spec.model_dump(mode="json"), "transitions": append_transition(state, "plan")}


def _deterministic_spec(intent: DeckIntent) -> PptSpec:
    return deterministic_plan_spec(intent)
