from __future__ import annotations

from typing import Any

from ppt_agent.domain.models import PptSpec
from ppt_agent.utils.state import append_transition, state_get


def approve_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    transitions = append_transition(state, "approve")

    if bool(state_get(state, "approved", False)):
        print_plan_summary(spec)
        print("Approve: auto-approved")
        return {"approved": True, "transitions": transitions}

    print_plan_summary(spec)
    answer = input("Approve this plan and build the PPTX? [y/N]: ").strip().lower()
    approved = answer in {"y", "yes"}
    if not approved:
        transitions = [*transitions, "rejected"]
        print("Approve: rejected, exiting before build")
    return {"approved": approved, "transitions": transitions}


def print_plan_summary(spec: PptSpec) -> None:
    print("\nPlan Summary")
    print(f"Title: {spec.title}")
    print(f"Audience: {spec.audience}")
    print("Slides:")
    for index, slide in enumerate(spec.slides, start=1):
        print(f"  {index}. {slide.title}")
        for bullet in slide.bullets[:3]:
            print(f"     - {bullet}")
