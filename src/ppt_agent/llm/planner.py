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
                    "You are creating a technical paper explanation deck, not a generic business deck. "
                    "Return JSON only. The JSON must match this schema: "
                    '{"schema_version": 2, "title": string, "audience": string, "goal": string, '
                    '"narrative": string, "theme": string, '
                    '"slides": [{"id": string, "role": string, "title": string, "message": string, "layout": string, '
                    '"content": {"bullets": [string], "figure_ids": [string], "table_ids": [string], "metrics": [object], '
                    '"visual_reason": string, "callouts": [object], "result_summary": [object], '
                    '"grounding_status": "grounded|partial|needs_verification"}, '
                    '"citations": [{"evidence_id": string, "page": integer|null, "source_file": string|null}], '
                    '"quality_checks": [string], "objective": string, "core_message": string, '
                    '"bullets": [string], "supporting_points": [string], "speaker_notes": string, '
                    '"visual_type": string, "image_query": string, "image_prompt": string, '
                    '"image_caption": string, "image_rationale": string, "layout_hint": string, '
                    '"style_tags": [string], "evidence_refs": [string], '
                    '"grounding_status": "grounded|partial|ungrounded", "source_notes": string}]}. '
                    "Target 7 to 10 slides unless evidence is too limited. Every slide must teach one specific idea from the paper. "
                    "Use the paper's actual story, not a fixed template. Each slide needs one concrete message and at most 3 bullets. "
                    "Do not use generic placeholders like 'Context and objective' or 'Primary recommendation'. "
                    "If source_digest or source_context is provided, generate only facts grounded in those materials. "
                    "If source_digest contains paper_analysis, use it first to form the narrative, then use evidence_digest/evidence_items for grounding. "
                    "When source_digest.type is evidence_pack, every non-cover slide must include role, message, layout, and non-empty citations. "
                    "Key conclusions must cite existing evidence_id values only. Figure/table selection must be based on role and relevance; do not default to the first figure. "
                    "If a slide uses a figure or table, explain the visual choice in content.visual_reason or speaker_notes. "
                    "Never invent evidence IDs, figure IDs, table IDs, metrics, or facts that are absent from the provided evidence_items. "
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
                    "- If paper_analysis is present, prioritize its problem, core_idea, method, experiments, limitations, and recommended_deck_outline.\n"
                    "- Build a 7 to 10 slide technical paper explanation deck when evidence supports it.\n"
                    "- Every slide must have a specific objective, one message, and core_message.\n"
                    "- Every slide must have role, message, and layout; mirror message into core_message and layout into layout_hint.\n"
                    "- Use at most 3 bullets per slide.\n"
                    "- At least two slides should use non-image visuals such as timeline, comparison, card summary, or result summary.\n"
                    "- Bullets and supporting_points must be concrete and grounded in the digest or retrieved chunks when provided.\n"
                    "- Each non-cover slide must include citations, evidence_refs, grounding_status, and source_notes.\n"
                    "- If source_context contains evidence_items, citations and evidence_refs may only reference their evidence_id values.\n"
                    "- If a slide uses a figure, put the figure evidence_id in content.figure_ids and explain the choice in content.visual_reason.\n"
                    "- For result slides, prefer table_ids, metrics, or content.result_summary grounded in evidence.\n"
                    "- If evidence is insufficient, use cautious wording and mark needs_verification in quality_checks or content.grounding_status.\n"
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
