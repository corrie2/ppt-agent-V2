from __future__ import annotations

from ppt_agent.domain.evidence import EvidencePack
from ppt_agent.domain.models import PptSpec, SlideSpec
from ppt_agent.runtime.document_qa import DocumentQaReport


MAX_BULLETS = 3


def repair_plan_spec(
    spec: PptSpec,
    *,
    qa_report: DocumentQaReport | None = None,
    evidence_pack: EvidencePack | None = None,
) -> PptSpec:
    if qa_report is not None and not qa_report.issues:
        return spec

    figure_ids = {figure.id for figure in evidence_pack.figures} if evidence_pack is not None else None
    evidence_ids = {item.id for item in evidence_pack.evidence_items()} if evidence_pack is not None else None

    repaired_slides = [
        _repair_slide(slide, figure_ids=figure_ids, evidence_ids=evidence_ids)
        for slide in spec.slides
    ]
    return spec.model_copy(update={"slides": repaired_slides})


def _repair_slide(
    slide: SlideSpec,
    *,
    figure_ids: set[str] | None,
    evidence_ids: set[str] | None,
) -> SlideSpec:
    repaired = slide.model_copy(deep=True)

    if not (repaired.message or repaired.core_message or "").strip():
        generated = _generated_message(repaired)
        repaired.message = generated
        repaired.core_message = generated

    if len(repaired.bullets) > MAX_BULLETS:
        repaired.bullets = repaired.bullets[:MAX_BULLETS]
        repaired.content.bullets = repaired.bullets

    if figure_ids is not None and repaired.content.figure_ids:
        repaired.content.figure_ids = [figure_id for figure_id in repaired.content.figure_ids if figure_id in figure_ids]

    if evidence_ids is not None and repaired.citations:
        repaired.citations = [citation for citation in repaired.citations if citation.evidence_id in evidence_ids]
        repaired.evidence_refs = [citation.evidence_id for citation in repaired.citations]

    if repaired.content.figure_ids:
        repaired.layout = "figure_with_caption"
        repaired.layout_hint = "figure_with_caption"
        repaired.visual_type = repaired.visual_type or "figure_with_caption"
    elif (repaired.layout or repaired.layout_hint) == "figure_with_caption":
        repaired.layout = "two_column_text_image"
        repaired.layout_hint = "two_column_text_image"
        if repaired.visual_type == "figure_with_caption":
            repaired.visual_type = "editorial_diagram"

    return repaired


def _generated_message(slide: SlideSpec) -> str:
    for value in (slide.core_message, slide.objective, slide.title):
        text = value.strip()
        if text:
            return text[:180]
    for bullet in slide.bullets:
        text = bullet.strip()
        if text:
            return text[:180]
    return "Summarize the supported point for this slide."
