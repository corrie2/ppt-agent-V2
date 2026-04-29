from __future__ import annotations

import json

import httpx
from pydantic import BaseModel

from ppt_agent.domain.models import DeckIntent, PptSpec
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model


class PlannerConfigError(ValueError):
    """Raised when planner configuration is incomplete."""


class LlmConnectionResult(BaseModel):
    provider: str
    model: str
    key_status: str
    connection_ok: bool


def generate_plan_with_llm(intent: DeckIntent, *, provider: str, model: str, api_key: str, timeout: float = 60.0) -> PptSpec:
    validate_model(provider, model)
    if not api_key.strip():
        raise PlannerConfigError(f"missing API key for provider {provider}")

    provider_spec = PROVIDER_SPECS[provider]
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a planning agent for PowerPoint generation. "
                    "Return JSON only. The JSON must match this schema: "
                    '{"title": string, "audience": string, "theme": string, '
                    '"slides": [{"title": string, "objective": string, "core_message": string, '
                    '"bullets": [string], "supporting_points": [string], "speaker_notes": string, '
                    '"visual_type": string, "image_query": string, "image_prompt": string, '
                    '"image_caption": string, "image_rationale": string, "layout_hint": string, '
                    '"style_tags": [string], "evidence_refs": [string], '
                    '"grounding_status": "grounded|partial|ungrounded", "source_notes": string}]}. '
                    "Do not use generic placeholders like 'Context and objective' or 'Primary recommendation'. "
                    "If source_digest or source_context is provided, generate only facts grounded in those materials. "
                    "Do not invent conference names, citation counts, ROI, customer cases, GitHub stars, business deployment metrics, "
                    "or experimental metrics. If evidence does not provide a detail, write 'not provided by source' or omit it. "
                    "Apply project_preferences as persistent user constraints. Avoid failure_patterns from prior QA or feedback. "
                    "If active_skill_context is provided, follow its output style and constraints while preserving the schema."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Create a concise deck plan for this topic: {intent.topic}\n"
                    f"Audience: {intent.audience}\n"
                    f"Tone: {intent.tone}\n"
                    f"Output format: {intent.output_format}\n"
                    f"Applied skills: {', '.join(intent.applied_skills) if intent.applied_skills else 'none'}\n"
                    f"project_preferences: {json.dumps(intent.project_preferences, ensure_ascii=False) if intent.project_preferences else 'none'}\n"
                    f"failure_patterns: {json.dumps(intent.failure_patterns, ensure_ascii=False) if intent.failure_patterns else 'none'}\n"
                    f"source_context: {json.dumps(intent.source_context, ensure_ascii=False) if intent.source_context else 'none'}\n"
                    f"source_digest: {json.dumps(intent.source_digest, ensure_ascii=False) if intent.source_digest else 'none'}\n"
                    f"active_skill_context: {intent.active_skill_context or 'none'}\n"
                    "Requirements:\n"
                    "- For academic/paper explanation decks, use a research-paper teaching structure rather than a business proposal.\n"
                    "- Every slide must have a specific objective and core_message.\n"
                    "- At least two slides should use non-image visuals such as timeline, comparison, or card summary.\n"
                    "- Bullets and supporting_points must be concrete and grounded in the digest or retrieved chunks when provided.\n"
                    "- Each slide must include evidence_refs, grounding_status, and source_notes.\n"
                    "- Keep image fields empty for non-image slides.\n"
                ),
            },
        ],
    }

    response = httpx.post(
        f"{provider_spec.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    data = _extract_json_object(content)
    return PptSpec.model_validate(data)


def test_llm_connection(provider: str, *, model: str, api_key: str, timeout: float = 30.0) -> LlmConnectionResult:
    validate_model(provider, model)
    if not api_key.strip():
        raise PlannerConfigError(f"missing API key for provider {provider}")

    provider_spec = PROVIDER_SPECS[provider]
    response = httpx.post(
        f"{provider_spec.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0,
            "max_tokens": 8,
            "messages": [
                {"role": "system", "content": "Reply with OK."},
                {"role": "user", "content": "Connection test."},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    response.json()
    return LlmConnectionResult(provider=provider, model=model, key_status="present", connection_ok=True)


def _extract_json_object(content: str) -> dict:
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
        raise ValueError("LLM response did not contain a JSON object")
    return json.loads(text[start : end + 1])
