from __future__ import annotations

import json
import logging

import httpx
from pydantic import BaseModel

from ppt_agent.domain.models import DeckIntent, PptSpec
from ppt_agent.llm.common import extract_json_object, llm_call_with_retry
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model

logger = logging.getLogger(__name__)


class PlannerConfigError(ValueError):
    """Raised when planner configuration is incomplete."""


class LlmConnectionResult(BaseModel):
    provider: str
    model: str
    key_status: str
    connection_ok: bool


PAPER_STRUCTURE = """ACADEMIC PAPER DECK STRUCTURE (follow this order):
1. Title slide (paper title, authors, venue)
2. Problem & Motivation (why this matters, what gap exists)
3. Core Idea / Contribution (key insight in 1-2 sentences)
4. Method Overview (architecture/framework figure if available)
5. Method Detail N (one slide per key component, with figures)
6. Experiment Setup (datasets, baselines, metrics)
7. Result N (one slide per key finding, with figures/tables)
8. Ablation / Analysis (if evidence supports)
9. Limitations & Future Work
10. Conclusion & Key Takeaways

SLIDE COUNT RULES:
- If user specified a slide count, use exactly that number
- Otherwise, determine slide count by content volume:
  - Count main sections in evidence (problem, method, experiments, results)
  - Count available figures and tables
  - Each key figure/table should have its own slide
  - Each major result finding needs a slide
  - Estimate: sections + figures/2, minimum 8, maximum 30
- Never artificially limit to 7-10 slides for papers with rich content"""

SYSTEM_PROMPT = """You are creating a technical paper explanation deck, not a generic business deck.
Return JSON only. The JSON must match this schema:
{"schema_version": 2, "title": string, "audience": string, "goal": string,
"narrative": string, "theme": string,
"slides": [{"id": string, "role": string, "title": string, "message": string, "layout": string,
"content": {"bullets": [string], "figure_ids": [string], "table_ids": [string], "metrics": [object],
"visual_reason": string, "callouts": [object], "result_summary": [object],
"grounding_status": "grounded|partial|needs_verification"},
"citations": [{"evidence_id": string, "page": integer|null, "source_file": string|null}],
"quality_checks": [string], "objective": string, "core_message": string,
"bullets": [string], "supporting_points": [string], "speaker_notes": string,
"visual_type": string, "image_query": string, "image_prompt": string,
"image_caption": string, "image_rationale": string, "layout_hint": string,
"style_tags": [string], "evidence_refs": [string],
"grounding_status": "grounded|partial|ungrounded", "source_notes": string}]}.

CRITICAL RULES:
1. DYNAMIC SLIDE COUNT: Do not fix at 7-10. Determine slides by content volume.
   - If user specified slides count, use exactly that number
   - Rich papers (20+ pages, many figures): 15-25 slides
   - Medium papers (10-20 pages): 10-15 slides
   - Short papers (<10 pages): 8-12 slides
2. USE FIGURES AGGRESSIVELY: Use at least 30% of available figures.
   - Architecture/method figures MUST be used
   - Key result figures MUST be used
   - Each important figure gets its own slide
3. CONTENT DENSITY: Each slide needs 3-5 bullets with SPECIFIC data.
   - BAD: "The method performs well on benchmarks"
   - GOOD: "JAG achieves QPS > 10,000 at recall 0.8 on MSTuring-10M (10x better than baselines)"
4. ACADEMIC STRUCTURE: Follow paper structure (Problem -> Method -> Results -> Conclusion)
5. Every slide must teach ONE specific idea from the paper.
6. Do not use generic placeholders like 'Context and objective' or 'Primary recommendation'.
7. If source_digest or source_context is provided, generate only facts grounded in those materials.
8. Key conclusions must cite existing evidence_id values only.
9. Figure/table selection must be based on role and relevance; do not default to the first figure.
10. If a slide uses a figure or table, explain the visual choice in content.visual_reason or speaker_notes.
11. Never invent evidence IDs, figure IDs, table IDs, metrics, or facts that are absent from the provided evidence_items."""


def generate_plan_with_llm(
    intent: DeckIntent,
    *,
    provider: str,
    model: str,
    api_key: str,
    timeout: float = 180.0,
    max_retries: int = 2,
) -> PptSpec:
    validate_model(provider, model)
    if not api_key.strip():
        raise PlannerConfigError(f"missing API key for provider {provider}")

    provider_spec = PROVIDER_SPECS[provider]
    url = f"{provider_spec.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _build_body(max_tokens: int = 32768, extra_instructions: str = "") -> dict:
        return {
            "model": model,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + extra_instructions},
                {"role": "user", "content": "\n".join(_build_user_parts(intent))},
            ],
        }

    def _parse(payload: dict) -> PptSpec:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PlannerConfigError(f"Unexpected LLM response format: {exc}") from exc
        return PptSpec.model_validate(extract_json_object(content))

    # Primary attempt
    try:
        return llm_call_with_retry(
            url=url, headers=headers, body=_build_body(32768),
            timeout=timeout, max_retries=max_retries,
            parse_response=_parse, label="Planner",
        )
    except Exception as primary_exc:
        logger.warning("Primary planner failed (%s), trying simplified fallback...", primary_exc)

    # Simplified fallback: fewer slides, shorter bullets
    fallback_instructions = (
        "\n\nCRITICAL: Return ONLY valid JSON. No markdown fences. No explanation. "
        "Keep slides to 10 maximum. Use short bullet points (under 100 chars each)."
    )
    try:
        return llm_call_with_retry(
            url=url, headers=headers, body=_build_body(16384, fallback_instructions),
            timeout=timeout, max_retries=1,
            parse_response=_parse, label="Planner-fallback",
        )
    except Exception as fallback_exc:
        raise PlannerConfigError(
            f"Planner failed after all attempts. Last: {fallback_exc}"
        ) from fallback_exc


def _build_user_parts(intent: DeckIntent) -> list[str]:
    parts = [
        f"Create a deck plan for this topic: {intent.topic}",
        f"Audience: {intent.audience}",
        f"Tone: {intent.tone}",
        f"Output format: {intent.output_format}",
        f"Applied skills: {', '.join(intent.applied_skills) if intent.applied_skills else 'none'}",
    ]
    for field in ("project_preferences", "failure_patterns", "source_context", "source_digest"):
        value = getattr(intent, field, None)
        if value:
            parts.append(f"{field}: {json.dumps(value, ensure_ascii=False)}")
    if intent.active_skill_context:
        parts.append(f"active_skill_context: {intent.active_skill_context}")

    parts.extend([
        "",
        "REQUIREMENTS:",
        "- For academic/paper explanation decks, follow this structure:",
        PAPER_STRUCTURE,
        "",
        "- If paper_analysis is present, prioritize its problem, core_idea, method, experiments, limitations.",
        "- Every slide must have: role, message, layout, objective, core_message.",
        "- Every slide must have 3-5 bullets with SPECIFIC data points (numbers, metrics, comparisons).",
        "- Bullets must be concrete and grounded in evidence. Cite specific results.",
        "- Each non-cover slide must include citations, evidence_refs, grounding_status.",
        "- If a slide uses a figure, put the figure evidence_id in content.figure_ids.",
        "- For result slides, prefer table_ids, metrics, or content.result_summary.",
        "- Use ALL available figures that are relevant. Target 30%+ figure usage.",
        "- If selected_figure_ids is provided in source_digest, you MUST use those figures in your slides. Assign each to the most relevant slide based on content.",
        "- Keep image fields empty for non-image slides.",
    ])
    return parts


def test_llm_connection(provider: str, *, model: str, api_key: str, timeout: float = 30.0) -> LlmConnectionResult:
    validate_model(provider, model)
    if not api_key.strip():
        raise PlannerConfigError(f"missing API key for provider {provider}")

    provider_spec = PROVIDER_SPECS[provider]
    httpx.post(
        f"{provider_spec.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "temperature": 0, "max_tokens": 8,
              "messages": [{"role": "system", "content": "Reply with OK."},
                           {"role": "user", "content": "Connection test."}]},
        timeout=timeout,
    ).raise_for_status()
    return LlmConnectionResult(provider=provider, model=model, key_status="present", connection_ok=True)
