from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def state_get(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def state_dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def append_transition(state: Any, name: str) -> list[str]:
    transitions = state_get(state, "transitions") or []
    return [*transitions, name]
