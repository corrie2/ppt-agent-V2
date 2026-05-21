from __future__ import annotations

import argparse
import json
from pathlib import Path

from ppt_agent.cli.main import _evidence_digest, _read_evidence_pack
from ppt_agent.domain.models import DeckIntent, PptSpec
from ppt_agent.llm.analyzer import generate_paper_analysis_with_llm
from ppt_agent.runtime.document_qa import run_document_qa, write_document_qa_report
from ppt_agent.runtime.planner import build_plan_spec, deterministic_plan_spec, resolve_planner_selection
from ppt_agent.storage.llm_settings import load_api_key
from ppt_agent.storage.plan_io import write_plan_document


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate fallback, LLM, and analyze+LLM paper deck paths.")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    pack = _read_evidence_pack(args.evidence)
    digest = _evidence_digest(pack, evidence_path=args.evidence)
    topic = _topic_from_digest(digest) or args.evidence.stem
    base_intent = DeckIntent(topic=topic, source_digest=digest, source_context=digest.get("evidence_items", []))

    fallback = deterministic_plan_spec(base_intent)
    _write_plan_and_qa(args.out_dir / "fallback_plan.json", args.out_dir / "qa_fallback.json", base_intent, fallback, pack, args.evidence)

    resolved_provider, resolved_model = resolve_planner_selection(provider=args.provider, model=args.model)
    api_key = load_api_key(resolved_provider) if resolved_provider else None
    llm_plan: PptSpec | None = None
    analysis_plan: PptSpec | None = None

    if resolved_provider and resolved_model and api_key:
        try:
            llm_plan = build_plan_spec(base_intent, provider=resolved_provider, model=resolved_model)
            _write_plan_and_qa(args.out_dir / "llm_plan.json", args.out_dir / "qa_llm.json", base_intent, llm_plan, pack, args.evidence)
        except Exception as exc:  # noqa: BLE001 - eval script records failures instead of aborting.
            notes.append(f"LLM plan skipped: {exc}")

        try:
            analysis = generate_paper_analysis_with_llm(digest, provider=resolved_provider, model=resolved_model, api_key=api_key)
            analysis_path = args.out_dir / "analysis.json"
            analysis_path.write_text(analysis.to_json(), encoding="utf-8")
            analysis_digest = {
                "type": "paper_analysis_with_evidence",
                "analysis_path": str(analysis_path),
                "paper_analysis": analysis.model_dump(mode="json"),
                "evidence_digest": digest,
            }
            analysis_intent = DeckIntent(
                topic=analysis.paper_title or topic,
                source_digest=analysis_digest,
                source_context=digest.get("evidence_items", []),
            )
            analysis_plan = build_plan_spec(analysis_intent, provider=resolved_provider, model=resolved_model)
            _write_plan_and_qa(
                args.out_dir / "analysis_plan.json",
                args.out_dir / "qa_analysis_plan.json",
                analysis_intent,
                analysis_plan,
                pack,
                args.evidence,
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Analyze+LLM path skipped: {exc}")
    else:
        notes.append("LLM paths skipped: provider/model/key not available.")

    _write_summary(
        args.out_dir / "summary.md",
        plans={
            "fallback": fallback,
            "llm": llm_plan,
            "analysis_plan": analysis_plan,
        },
        notes=notes,
    )


def _write_plan_and_qa(
    plan_path: Path,
    qa_path: Path,
    intent: DeckIntent,
    spec: PptSpec,
    pack,
    evidence_path: Path,
) -> None:
    write_plan_document(plan_path, intent=intent, spec=spec, mode="plan", approved=False, transitions=["eval"])
    report = run_document_qa(spec, evidence_pack=pack, evidence_path=evidence_path)
    write_document_qa_report(report, qa_path)


def _write_summary(path: Path, *, plans: dict[str, PptSpec | None], notes: list[str]) -> None:
    lines = ["# Paper Deck Evaluation", ""]
    if notes:
        lines.extend(["## Notes", *[f"- {note}" for note in notes], ""])
    lines.extend(["## Metrics", ""])
    lines.append("| path | slides_count | cited_slide_ratio | average_bullets_per_slide | figure_slide_count | table_or_result_slide_count | generic_title_count | missing_message_count | needs_verification_count |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, spec in plans.items():
        if spec is None:
            continue
        metrics = _metrics(spec)
        lines.append(
            f"| {name} | {metrics['slides_count']} | {metrics['cited_slide_ratio']:.2f} | "
            f"{metrics['average_bullets_per_slide']:.2f} | {metrics['figure_slide_count']} | "
            f"{metrics['table_or_result_slide_count']} | {metrics['generic_title_count']} | "
            f"{metrics['missing_message_count']} | {metrics['needs_verification_count']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _metrics(spec: PptSpec) -> dict[str, float | int]:
    slides = spec.slides
    count = len(slides)
    cited = sum(1 for slide in slides if slide.citations)
    bullets = sum(len(slide.bullets) for slide in slides)
    return {
        "slides_count": count,
        "cited_slide_ratio": cited / count if count else 0,
        "average_bullets_per_slide": bullets / count if count else 0,
        "figure_slide_count": sum(1 for slide in slides if slide.content.figure_ids),
        "table_or_result_slide_count": sum(1 for slide in slides if slide.content.table_ids or slide.content.result_summary or "result" in slide.role.lower()),
        "generic_title_count": sum(1 for slide in slides if slide.title.lower() in {"problem", "method", "results", "takeaways", "summary"}),
        "missing_message_count": sum(1 for slide in slides if not (slide.message or slide.core_message).strip()),
        "needs_verification_count": sum(
            1
            for slide in slides
            if (slide.grounding_status == "needs_verification" or slide.content.grounding_status == "needs_verification")
        ),
    }


def _topic_from_digest(digest: dict) -> str | None:
    for source in digest.get("sources") or []:
        if source.get("title"):
            return source["title"]
    return None


if __name__ == "__main__":
    main()
