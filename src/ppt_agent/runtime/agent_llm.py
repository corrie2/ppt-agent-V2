from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ppt_agent.llm.common import extract_json_object, llm_call_with_retry
from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model
from ppt_agent.storage.llm_settings import load_api_key

logger = logging.getLogger(__name__)


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

# Non-critical agents that can fail fast (deterministic fallback is acceptable)
FAST_AGENTS = {"qa", "evaluator", "render_review", "brief_outline", "design_chart"}


class AgentModelConfig(BaseModel):
    provider: str | None = None
    model: str | None = None


class AgentOverride(BaseModel):
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_retries: int | None = None


class AgentLlmConfig(BaseModel):
    agents: dict[str, AgentModelConfig]
    enabled: bool = True
    fallback_to_deterministic: bool = True
    timeout_seconds: float = 180.0
    max_tokens: int = 16384
    max_retries: int = 2
    agent_overrides: dict[str, AgentOverride] = {}

    def for_agent(self, agent_name: str) -> tuple[float, int, int]:
        """Return (timeout, max_tokens, max_retries) for a specific agent."""
        override = self.agent_overrides.get(agent_name)
        if override is None:
            return self.timeout_seconds, self.max_tokens, self.max_retries
        return (
            override.timeout_seconds or self.timeout_seconds,
            override.max_tokens or self.max_tokens,
            override.max_retries if override.max_retries is not None else self.max_retries,
        )


def default_agent_llm_config() -> AgentLlmConfig:
    return AgentLlmConfig(
        agents={name: AgentModelConfig.model_validate(v) for name, v in DEFAULT_AGENT_MODELS.items()},
        agent_overrides={name: AgentOverride(timeout_seconds=90.0, max_retries=1) for name in FAST_AGENTS},
    )


def load_agent_llm_config(cwd: Path | None = None) -> AgentLlmConfig:
    path = _agent_config_path(cwd)
    if not path.exists():
        return default_agent_llm_config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    loaded = AgentLlmConfig.model_validate(raw)
    defaults = default_agent_llm_config()
    return loaded.model_copy(update={"agents": {**defaults.agents, **loaded.agents}})


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

    timeout_s, max_tok, max_ret = resolved.for_agent(agent_name)
    provider_spec = PROVIDER_SPECS[agent_config.provider]

    def _parse(payload: dict) -> dict[str, Any]:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected API response format: {exc}") from exc
        return extract_json_object(content)

    try:
        return llm_call_with_retry(
            url=f"{provider_spec.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            body={
                "model": agent_config.model,
                "temperature": 0.2,
                "max_tokens": max_tok,
                "messages": [
                    {"role": "system", "content": (
                        f"You are the {agent_name} worker in a PPT multi-agent pipeline. "
                        "Return JSON only. Do not include markdown fences or explanations. "
                        + system_prompt
                    )},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            },
            timeout=timeout_s,
            max_retries=max_ret,
            parse_response=_parse,
            fallback_to_none=resolved.fallback_to_deterministic,
            label=f"Agent:{agent_name}",
        )
    except Exception as exc:
        if resolved.fallback_to_deterministic:
            logger.warning("Agent %s failed, falling back to deterministic: %s", agent_name, exc)
            return None
        raise PlannerConfigError(f"Agent {agent_name} failed: {exc}") from exc


def _agent_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".ppt-agent" / "agents" / "config.json"
