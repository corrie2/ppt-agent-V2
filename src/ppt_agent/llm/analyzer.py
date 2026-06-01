from __future__ import annotations

import json

import httpx

from ppt_agent.domain.analysis import PaperAnalysis
from ppt_agent.llm.common import extract_json_object
from ppt_agent.llm.planner import PlannerConfigError
from ppt_agent.llm.providers import PROVIDER_SPECS, validate_model


def generate_paper_analysis_with_llm(
    evidence_digest: dict,
    *,
    provider: str,
    model: str,
    api_key: str,
    timeout: float = 180.0,
) -> PaperAnalysis:
    validate_model(provider, model)
    if not api_key.strip():
        raise PlannerConfigError(f"missing API key for provider {provider}")

    provider_spec = PROVIDER_SPECS[provider]
    body = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a technical paper reading assistant. Generate structured paper_analysis.json from an EvidencePack digest. "
                    "Do not create slides. Do not invent datasets, baselines, metrics, result numbers, or claims. "
                    "Every conclusion must be tied to evidence_ids. If evidence is insufficient, use 'not_provided_by_evidence' or empty arrays and explain in uncertainties. "
                    "Return JSON only, with no markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Generate JSON matching this schema:\n"
                    "{"
                    '"schema_version": 1, "paper_title": string, '
                    '"source_summary": {"evidence_path": string, "sections_used": [string], "figures_used": [string], "tables_used": [string]}, '
                    '"problem": {"summary": string, "evidence_ids": [string]}, '
                    '"motivation": {"summary": string, "evidence_ids": [string]}, '
                    '"core_idea": {"summary": string, "evidence_ids": [string]}, '
                    '"contributions": [{"claim": string, "evidence_ids": [string]}], '
                    '"method": {"overview": string, "components": [{"name": string, "description": string, "evidence_ids": [string]}], '
                    '"important_figures": [{"figure_id": string, "role": string, "reason": string}]}, '
                    '"experiments": {"datasets": [string], "baselines": [string], "metrics": [string], '
                    '"main_results": [{"result": string, "evidence_ids": [string]}], '
                    '"important_tables": [{"table_id": string, "role": string, "reason": string}]}, '
                    '"ablation_or_analysis": [{"finding": string, "evidence_ids": [string]}], '
                    '"limitations": [{"limitation": string, "evidence_ids": [string]}], '
                    '"recommended_deck_outline": [{"slide_role": string, "message": string, "evidence_ids": [string]}], '
                    '"uncertainties": [string]'
                    "}\n"
                    "Keep summaries to 1-2 sentences. recommended_deck_outline should follow the paper's actual story.\n"
                    f"Evidence digest:\n{json.dumps(evidence_digest, ensure_ascii=False)}"
                ),
            },
        ],
    }
    response = httpx.post(
        f"{provider_spec.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    try:
        payload = extract_json_object(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"LLM analyzer response was not valid JSON: {exc}") from exc
    return PaperAnalysis.model_validate(payload)
