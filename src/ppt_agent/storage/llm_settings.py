from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model, validate_provider


class LlmSelection(BaseModel):
    provider: str
    model: str


class LlmKeyStatus(BaseModel):
    provider: str
    has_key: bool


def llm_root(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ".ppt-agent" / "llm"


def config_path(cwd: Path | None = None) -> Path:
    return llm_root(cwd) / "config.json"


def key_path(provider: str, cwd: Path | None = None) -> Path:
    validate_provider(provider)
    return llm_root(cwd) / "keys" / f"{provider}.key"


def save_selection(provider: str, model: str, cwd: Path | None = None) -> LlmSelection:
    validate_model(provider, model)
    selection = LlmSelection(provider=provider, model=model)
    path = config_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(selection.model_dump_json(indent=2), encoding="utf-8")
    return selection


def load_selection(cwd: Path | None = None) -> LlmSelection | None:
    path = config_path(cwd)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    selection = LlmSelection.model_validate(raw)
    validate_model(selection.provider, selection.model)
    return selection


def save_api_key(provider: str, api_key: str, cwd: Path | None = None) -> Path:
    validate_provider(provider)
    path = key_path(provider, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(api_key.strip(), encoding="utf-8")
    return path


def load_api_key(provider: str, cwd: Path | None = None) -> str | None:
    path = key_path(provider, cwd)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def key_statuses(cwd: Path | None = None) -> list[LlmKeyStatus]:
    return [LlmKeyStatus(provider=name, has_key=load_api_key(name, cwd) is not None) for name in PROVIDER_SPECS]
