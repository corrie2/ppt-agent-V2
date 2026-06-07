from __future__ import annotations

from pathlib import Path
from typing import Any

from ppt_agent.domain.evidence import ClaimEvidence, EvidencePack, FigureAsset, SectionEvidence, TableAsset
from ppt_agent.domain.models import Citation, PptSpec
from ppt_agent.ingest import EvidenceBuilder, MinerUAdapter
from ppt_agent.ingest.mineru_adapter import MinerUOptions, detect_mineru_device
from ppt_agent.runtime.source_store import source_id_for_path


def ensure_mineru_evidence_for_source(
    source: Path,
    *,
    workspace: Path,
    options: MinerUOptions | None = None,
) -> tuple[EvidencePack | None, Path | None, list[str]]:
    source_path = Path(source).resolve()
    warnings: list[str] = []
    if source_path.suffix.lower() != ".pdf":
        return None, None, [f"{source_path.name}: MinerU evidence only supports PDF sources"]

    try:
        source_id = source_id_for_path(source_path)
    except OSError as exc:
        return None, None, [f"{source_path.name}: cannot create source id for MinerU evidence: {exc}"]

    evidence_dir = workspace / ".ppt-agent" / "data" / "evidence" / source_id
    evidence_path = evidence_dir / "evidence.json"
    if evidence_path.exists():
        try:
            return EvidencePack.from_json(evidence_path.read_text(encoding="utf-8")), evidence_path, warnings
        except ValueError as exc:
            warnings.append(f"{source_path.name}: cached evidence is invalid and will be regenerated: {exc}")

    workdir = workspace / ".ppt-agent" / "ingest" / source_id
    try:
        parse_result = MinerUAdapter(options=options or _default_mineru_options()).parse(source_path, workdir)
        pack = EvidenceBuilder().build(parse_result)
    except (RuntimeError, ValueError, OSError) as exc:
        return None, None, [f"{source_path.name}: MinerU evidence generation failed: {exc}"]

    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(pack.to_json(), encoding="utf-8")
    return pack, evidence_path, warnings


def _default_mineru_options() -> MinerUOptions:
    import os

    device, _reason = detect_mineru_device()
    api_url = os.environ.get("PPT_AGENT_MINERU_API_URL")
    return MinerUOptions(
        backend=os.environ.get("PPT_AGENT_MINERU_BACKEND", "pipeline"),
        method=os.environ.get("PPT_AGENT_MINERU_METHOD", "auto"),
        lang=os.environ.get("PPT_AGENT_MINERU_LANG", "en"),
        formula=_env_bool("PPT_AGENT_MINERU_FORMULA", default=False),
        table=_env_bool("PPT_AGENT_MINERU_TABLE", default=False),
        image_analysis=_env_bool("PPT_AGENT_MINERU_IMAGE_ANALYSIS", default=False),
        timeout_seconds=int(os.environ.get("PPT_AGENT_MINERU_TIMEOUT", "600")),
        batch_size=int(os.environ.get("PPT_AGENT_MINERU_BATCH_SIZE", "1")),
        device=device,
        api_url=api_url,
    )


def _env_bool(name: str, *, default: bool) -> bool:
    import os

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def evidence_digest(pack: EvidencePack, *, evidence_path: Path | None) -> dict[str, Any]:
    sections = _section_summaries(pack)
    figures = _figure_summaries(pack)
    tables = _table_summaries(pack)
    claims = _claim_summaries(pack)
    return {
        "type": "evidence_pack",
        "path": str(evidence_path) if evidence_path else None,
        "sources": [
            {
                "source_id": source.id,
                "name": source.source_file,
                "title": source.title or Path(source.source_file).stem,
                "path": source.path,
            }
            for source in pack.source_files
        ],
        "selection_summary": {
            "sections_total": len(pack.sections),
            "figures_total": len(pack.figures),
            "tables_total": len(pack.tables),
            "claims_total": len(pack.claims),
            "sections_selected": len(sections),
            "figures_selected": len(figures),
            "tables_selected": len(tables),
            "claims_selected": len(claims),
        },
        "evidence_items": [*sections, *figures, *tables, *claims],
    }


def load_evidence_pack(path: str | Path | None) -> tuple[EvidencePack | None, Path | None, list[str]]:
    if not path:
        return None, None, []
    evidence_path = Path(path)
    try:
        return EvidencePack.from_json(evidence_path.read_text(encoding="utf-8")), evidence_path, []
    except (OSError, ValueError) as exc:
        return None, evidence_path, [f"failed to load evidence pack {evidence_path}: {exc}"]


def attach_evidence_figures_to_spec(
    spec: PptSpec,
    pack: EvidencePack | None,
    selected_figure_ids: list[str] | None = None,
    selected_table_ids: list[str] | None = None,
) -> PptSpec:
    if not pack or (not pack.figures and not pack.tables):
        return spec

    import logging
    logger = logging.getLogger(__name__)

    valid_figure_ids = {f.id for f in pack.figures}
    valid_table_ids = {t.id for t in pack.tables}

    # ---- FIGURES ----
    # Priority: user selection > LLM assignment > auto-select
    if selected_figure_ids:
        # User selected figures — always use these, overwrite LLM's assignments
        figures = [f for f in pack.figures if f.id in selected_figure_ids]
        logger.info("Using %d user-selected figures: %s", len(figures), [f.id for f in figures])
    elif any(slide.content.figure_ids for slide in spec.slides):
        # LLM already assigned figures, user didn't select — validate and filter
        assigned_ids = {
            fid for slide in spec.slides for fid in slide.content.figure_ids
        }
        invalid_ids = assigned_ids - valid_figure_ids
        if invalid_ids:
            logger.warning("Removing invalid LLM-assigned figure_ids not in evidence pack: %s", invalid_ids)
            # Filter out invalid IDs from slide content
            slides = list(spec.slides)
            for slide in slides:
                original = slide.content.figure_ids
                filtered = [fid for fid in original if fid in valid_figure_ids]
                if filtered != original:
                    slide.content.figure_ids = filtered
            spec = spec.model_copy(update={"slides": slides})
        remaining = assigned_ids & valid_figure_ids
        if remaining:
            logger.info("Keeping validated LLM-assigned figures: %s", remaining)
            figures = []
        else:
            # All LLM-assigned figures were invalid — fall through to auto-select
            logger.warning("All LLM-assigned figure_ids were invalid, falling through to auto-select")
            figures = None  # sentinel for fall-through
    else:
        figures = None  # sentinel for fall-through

    if figures is None:
        # No user selection, no valid LLM assignment — auto-select
        max_figures = max(3, min(len(pack.figures), len(spec.slides) // 2))
        figures = _selected_figures(pack.figures, max_items=max_figures) if pack.figures else []
        logger.info("Auto-selected %d figures", len(figures))

    if figures:
        slides = list(spec.slides)
        candidate_indexes = _figure_slide_indexes(len(slides), len(figures))
        for figure, slide_index in zip(figures, candidate_indexes):
            slide = slides[slide_index]
            slide.content.figure_ids = [figure.id]
            slide.content.visual_reason = slide.content.visual_reason or "Use the source paper figure as direct visual evidence."
            slide.visual_type = slide.visual_type or "figure_with_caption"
            slide.layout = "figure_with_caption"
            slide.layout_hint = "figure_with_caption"
            slide.image_caption = slide.image_caption or figure.caption or figure.text or figure.id
            if not any(citation.evidence_id == figure.id for citation in slide.citations):
                slide.citations.append(Citation(evidence_id=figure.id, page=figure.page, source_file=figure.source_file))
            if figure.id not in slide.evidence_refs:
                slide.evidence_refs.append(figure.id)
        logger.info("Final figure assignments: %d figures on slides %s", len(figures), list(candidate_indexes))
        spec = spec.model_copy(update={"slides": slides})

    # ---- TABLES ----
    if not pack.tables:
        return spec

    if selected_table_ids:
        tables = [t for t in pack.tables if t.id in selected_table_ids]
        logger.info("Using %d user-selected tables: %s", len(tables), [t.id for t in tables])
    elif any(slide.content.table_ids for slide in spec.slides):
        # LLM assigned table_ids — validate and filter
        assigned_ids = {
            tid for slide in spec.slides for tid in slide.content.table_ids
        }
        invalid_ids = assigned_ids - valid_table_ids
        if invalid_ids:
            logger.warning("Removing invalid LLM-assigned table_ids not in evidence pack: %s", invalid_ids)
            slides = list(spec.slides)
            for slide in slides:
                original = slide.content.table_ids
                filtered = [tid for tid in original if tid in valid_table_ids]
                if filtered != original:
                    slide.content.table_ids = filtered
            spec = spec.model_copy(update={"slides": slides})
        remaining = assigned_ids & valid_table_ids
        if remaining:
            logger.info("Keeping validated LLM-assigned tables: %s", remaining)
        else:
            logger.warning("All LLM-assigned table_ids were invalid")
        return spec
    else:
        # Auto-select tables
        max_tables = max(2, min(len(pack.tables), len(spec.slides) // 3))
        tables = _selected_tables_list(pack.tables, max_items=max_tables)
        logger.info("Auto-selected %d tables", len(tables))

        if tables:
            slides = list(spec.slides)
            table_indexes = _table_slide_indexes(len(slides), len(tables))
            for table, slide_index in zip(tables, table_indexes):
                slide = slides[slide_index]
                slide.content.table_ids = [table.id]
                slide.content.visual_reason = slide.content.visual_reason or "Use the source paper table as direct evidence."
                slide.visual_type = slide.visual_type or "result_table_summary"
                slide.layout = slide.layout or "result_table_summary"
                if not any(citation.evidence_id == table.id for citation in slide.citations):
                    slide.citations.append(Citation(evidence_id=table.id, page=table.page, source_file=table.source_file))
                if table.id not in slide.evidence_refs:
                    slide.evidence_refs.append(table.id)
            logger.info("Final table assignments: %d tables on slides %s", len(tables), list(table_indexes))
            spec = spec.model_copy(update={"slides": slides})

    return spec


def _figure_slide_indexes(slide_count: int, figure_count: int) -> list[int]:
    if slide_count <= 0:
        return []
    anchors = [max(1, slide_count // 3), max(1, slide_count // 2), max(1, (slide_count * 2) // 3), max(1, slide_count - 2)]
    indexes: list[int] = []
    for anchor in anchors:
        index = min(slide_count - 1, anchor)
        if index not in indexes:
            indexes.append(index)
        if len(indexes) >= figure_count:
            break
    while len(indexes) < figure_count:
        candidate = min(slide_count - 1, len(indexes) + 1)
        if candidate not in indexes:
            indexes.append(candidate)
        else:
            break
    return indexes


def _table_slide_indexes(slide_count: int, table_count: int) -> list[int]:
    """Pick slide indexes for tables, preferring later slides (results section)."""
    if slide_count <= 0:
        return []
    anchors = [max(1, slide_count // 2), max(1, (slide_count * 2) // 3), max(1, slide_count - 2), max(1, slide_count - 1)]
    indexes: list[int] = []
    for anchor in anchors:
        index = min(slide_count - 1, anchor)
        if index not in indexes:
            indexes.append(index)
        if len(indexes) >= table_count:
            break
    while len(indexes) < table_count:
        candidate = min(slide_count - 1, len(indexes) + 1)
        if candidate not in indexes:
            indexes.append(candidate)
        else:
            break
    return indexes


def _selected_tables_list(tables: list[TableAsset], *, max_items: int) -> list[TableAsset]:
    return [item[0] for item in _select_tables(tables, max_items=max_items)]


SECTION_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "abstract": ("abstract", "summary"),
    "introduction": ("introduction", "intro"),
    "problem_or_motivation": ("problem", "motivation", "challenge", "background"),
    "method_or_approach": ("method", "approach", "architecture", "framework", "algorithm", "system", "pipeline"),
    "experiment_or_evaluation": ("experiment", "evaluation", "setup", "dataset", "baseline", "metric"),
    "results": ("result", "performance", "comparison", "benchmark", "outperform"),
    "conclusion_or_limitations": ("conclusion", "limitation", "future work", "takeaway"),
}

FIGURE_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "method_overview": ("framework", "architecture", "pipeline", "overview", "workflow", "system", "design"),
    "algorithm_or_component": ("algorithm", "module", "component", "mechanism", "structure"),
    "result": ("result", "performance", "comparison", "benchmark", "evaluation", "experiment"),
    "analysis_or_ablation": ("ablation", "sensitivity", "analysis", "case study", "visualization"),
    "motivation_or_example": ("example", "illustration", "motivation", "scenario", "use case"),
}

TABLE_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "main_results": ("result", "performance", "comparison", "benchmark", "score"),
    "ablation": ("ablation", "variant", "sensitivity", "component"),
    "dataset_or_setup": ("dataset", "setup", "baseline", "metric", "statistics"),
}


def _section_summaries(pack: EvidencePack) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "role": role,
            "evidence_id": section.id,
            "source_file": section.source_file,
            "page": section.page,
            "heading": section.heading,
            "text": _truncate(section.text, limit=1000),
            "why_selected": why,
            "score": round(score, 3),
        }
        for section, role, why, score in _select_sections(pack.sections)
    ]


def _figure_summaries(pack: EvidencePack) -> list[dict[str, Any]]:
    return [
        {
            "type": "figure",
            "role": role,
            "evidence_id": figure.id,
            "source_file": figure.source_file,
            "page": figure.page,
            "caption": figure.caption,
            "text": _truncate(figure.text or "", limit=700),
            "path": figure.path,
            "why_selected": why,
            "score": round(score, 3),
        }
        for figure, role, why, score in _select_figures(pack.figures)
    ]


def _table_summaries(pack: EvidencePack) -> list[dict[str, Any]]:
    return [
        {
            "type": "table",
            "role": role,
            "evidence_id": table.id,
            "source_file": table.source_file,
            "page": table.page,
            "caption": table.caption,
            "text": _truncate(table.text or "", limit=700),
            "why_selected": why,
            "score": round(score, 3),
        }
        for table, role, why, score in _select_tables(pack.tables)
    ]


def _claim_summaries(pack: EvidencePack) -> list[dict[str, Any]]:
    return [
        {
            "type": "claim",
            "role": "claim",
            "evidence_id": claim.id,
            "source_file": claim.source_file,
            "page": claim.page,
            "text": _truncate(claim.text, limit=500),
            "supporting_evidence_ids": claim.supporting_evidence_ids,
            "confidence": claim.confidence,
        }
        for claim in pack.claims[:24]
    ]


def _select_sections(sections: list[SectionEvidence], *, max_items: int = 36) -> list[tuple[SectionEvidence, str, str, float]]:
    scored = [_score_section(section) for section in sections]
    selected: dict[str, tuple[SectionEvidence, str, str, float]] = {}
    for role in SECTION_ROLE_KEYWORDS:
        candidates = [item for item in scored if item[1] == role]
        if candidates:
            best = max(candidates, key=lambda item: (item[3], _text_density(item[0].text)))
            selected[best[0].id] = best
    for item in sorted(scored, key=lambda item: (item[3], _text_density(item[0].text)), reverse=True):
        if len(selected) >= max_items:
            break
        selected.setdefault(item[0].id, item)
    return list(selected.values())


def _score_section(section: SectionEvidence) -> tuple[SectionEvidence, str, str, float]:
    sample = f"{section.heading or ''} {section.text[:500]}".lower()
    best_role = "high_information"
    best_hits = 0
    for role, keywords in SECTION_ROLE_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in sample)
        if hits > best_hits:
            best_role = role
            best_hits = hits
    density = min(_text_density(section.text) / 1200, 0.25)
    score = min(1.0, 0.45 + best_hits * 0.18 + density) if best_hits else min(0.5, 0.2 + density)
    why = f"heading/text matched {best_role} keywords" if best_hits else "high information density section"
    return section, best_role, why, score


def _select_figures(figures: list[FigureAsset], *, max_items: int = 20) -> list[tuple[FigureAsset, str, str, float]]:
    scored = [_score_visual(figure, FIGURE_ROLE_KEYWORDS, fallback_role="supporting_figure") for figure in figures]
    return _pick_by_role_then_score(scored, max_items=max_items)


def _selected_figures(figures: list[FigureAsset], *, max_items: int) -> list[FigureAsset]:
    return [item[0] for item in _select_figures(figures, max_items=max_items) if item[0].path]


def _select_tables(tables: list[TableAsset], *, max_items: int = 12) -> list[tuple[TableAsset, str, str, float]]:
    scored = [_score_visual(table, TABLE_ROLE_KEYWORDS, fallback_role="supporting_table") for table in tables]
    return _pick_by_role_then_score(scored, max_items=max_items)


def _score_visual(item: FigureAsset | TableAsset, role_keywords: dict[str, tuple[str, ...]], *, fallback_role: str) -> tuple:
    haystack = f"{item.caption or ''} {getattr(item, 'text', '') or ''}".lower()
    best_role = fallback_role
    best_hits = 0
    for role, keywords in role_keywords.items():
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits > best_hits:
            best_role = role
            best_hits = hits
    score = min(1.0, 0.35 + best_hits * 0.2 + min(len(haystack) / 1400, 0.2)) if best_hits else 0.3
    why = f"caption/text matched {best_role} keywords" if best_hits else "included as supporting visual evidence"
    return item, best_role, why, score


def _pick_by_role_then_score(scored: list[tuple], *, max_items: int) -> list[tuple]:
    selected: dict[str, tuple] = {}
    for role in dict.fromkeys(item[1] for item in scored):
        candidates = [item for item in scored if item[1] == role]
        if candidates:
            best = max(candidates, key=lambda item: item[3])
            selected[best[0].id] = best
    for item in sorted(scored, key=lambda item: item[3], reverse=True):
        if len(selected) >= max_items:
            break
        selected.setdefault(item[0].id, item)
    return list(selected.values())


def _text_density(text: str) -> int:
    return len({token.strip(".,:;()[]{}").lower() for token in text.split() if len(token.strip(".,:;()[]{}")) > 3})


def _truncate(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."
