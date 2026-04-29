from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from ppt_agent.domain.models import AgentState
from ppt_agent.nodes.approve import approve_node
from ppt_agent.nodes.asset_plan import asset_plan_node
from ppt_agent.nodes.asset_resolve import asset_resolve_node
from ppt_agent.nodes.build import build_node
from ppt_agent.nodes.plan import plan_node
from ppt_agent.nodes.qa import qa_node
from ppt_agent.nodes.repair import repair_node
from ppt_agent.utils.state import state_get


def _after_plan(state: dict[str, Any]) -> str:
    return "asset_plan"


def _after_asset_resolve(state: dict[str, Any]) -> str:
    return END if state_get(state, "mode") == "plan" else "approve"


def _after_approve(state: dict[str, Any]) -> str:
    return "build" if bool(state_get(state, "approved", False)) else END


def _after_qa(state: dict[str, Any]) -> str:
    issues = state_get(state, "qa_issues") or []
    attempts = int(state_get(state, "repair_attempts") or 0)
    blocking_issues = [
        issue
        for issue in issues
        if (issue.get("severity") if isinstance(issue, dict) else getattr(issue, "severity", None)) != "warning"
    ]
    return "repair" if blocking_issues and attempts < 1 else END


def create_agent_graph(entry_point: str = "plan"):
    if entry_point not in {"plan", "asset_plan", "approve"}:
        raise ValueError(f"Unsupported graph entry point: {entry_point}")

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("asset_plan", asset_plan_node)
    graph.add_node("asset_resolve", asset_resolve_node)
    graph.add_node("approve", approve_node)
    graph.add_node("build", build_node)
    graph.add_node("qa", qa_node)
    graph.add_node("repair", repair_node)

    graph.set_entry_point(entry_point)
    graph.add_conditional_edges("plan", _after_plan, {"asset_plan": "asset_plan"})
    graph.add_edge("asset_plan", "asset_resolve")
    graph.add_conditional_edges("asset_resolve", _after_asset_resolve, {"approve": "approve", END: END})
    graph.add_conditional_edges("approve", _after_approve, {"build": "build", END: END})
    graph.add_edge("build", "qa")
    graph.add_conditional_edges("qa", _after_qa, {"repair": "repair", END: END})
    graph.add_edge("repair", "build")
    return graph.compile()
