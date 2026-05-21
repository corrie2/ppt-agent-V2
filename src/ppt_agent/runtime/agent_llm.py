from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model
from ppt_agent.storage.llm_settings import load_api_key


DEFAULT_AGENT_MODELS: dict[str, dict[str, str | None]] = {
    "supervisor": {"provider": "deepseek", "model": "deepseek-v4-pro"},
    "brief_outline": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "content": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "design_chart": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "qa": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "evaluator": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "render_review": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "page_designer": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "renderer_engineer": {"provider": "deepseek", "model": "deepseek-v4-pro"},
    "visual_quality_evaluator": {"provider": "deepseek", "model": "deepseek-v4-pro"},
    "page_generator": {"provider": None, "model": None},
}


class AgentModelConfig(BaseModel):
    provider: str | None = None
    model: str | None = None


class AgentLlmConfig(BaseModel):
    agents: dict[str, AgentModelConfig]
    enabled: bool = True
    fallback_to_deterministic: bool = True
    timeout_seconds: float = 60.0


def default_agent_llm_config() -> AgentLlmConfig:
    return AgentLlmConfig(
        agents={name: AgentModelConfig.model_validate(value) for name, value in DEFAULT_AGENT_MODELS.items()}
    )


def load_agent_llm_config(cwd: Path | None = None) -> AgentLlmConfig:
    path = _agent_config_path(cwd)
    if not path.exists():
        return default_agent_llm_config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    loaded = AgentLlmConfig.model_validate(raw)
    defaults = default_agent_llm_config()
    agents = {**defaults.agents, **loaded.agents}
    return loaded.model_copy(update={"agents": agents})


def write_default_agent_llm_config(cwd: Path | None = None) -> Path:
    path = _agent_config_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_agent_llm_config().model_dump_json(indent=2), encoding="utf-8")
    return path


def generate_agent_json(
    agent_name: str,
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    config: AgentLlmConfig | None = None,
    cwd: Path | None = None,
) -> dict[str, Any] | None:
    resolved = config or load_agent_llm_config(cwd)
    if not resolved.enabled:
        return None

    agent_config = resolved.agents.get(agent_name)
    if agent_config is None or not agent_config.provider or not agent_config.model:
        return None

    validate_model(agent_config.provider, agent_config.model)
    api_key = load_api_key(agent_config.provider, cwd)
    if not api_key:
        if resolved.fallback_to_deterministic:
            return None
        raise PlannerConfigError(f"missing API key for agent {agent_name} provider {agent_config.provider}")

    provider_spec = PROVIDER_SPECS[agent_config.provider]
    response = httpx.post(
        f"{provider_spec.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": agent_config.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You are the {agent_name} worker in a PPT multi-agent pipeline. "
                        "Return JSON only. Do not include markdown fences or explanations. "
                        + system_prompt
                    ),
                },
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        },
        timeout=resolved.timeout_seconds,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return _extract_json_object(content)


def _agent_config_path(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ".ppt-agent" / "agents" / "config.json"


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("agent LLM response did not contain a JSON object")
    return json.loads(text[start : end + 1])
