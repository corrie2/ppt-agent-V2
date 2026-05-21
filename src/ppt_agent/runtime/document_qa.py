from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field

from ppt_agent.domain.evidence import EvidenceItem, EvidencePack, FigureAsset
from ppt_agent.domain.models import PptSpec, SlideSpec


class DocumentQaIssue(BaseModel):
    id: str
    severity: str
    slide_id: str | None = None
    message: str
    suggested_fix: str | None = None


class DocumentQaReport(BaseModel):
    ok: bool
    issues: list[DocumentQaIssue] = Field(default_factory=list)

    def to_json(self, *, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)


def run_document_qa(spec: PptSpec, *, evidence_pack: EvidencePack | None = None, evidence_path: Path | None = None) -> DocumentQaReport:
    evidence_by_id = _evidence_by_id(evidence_pack)
    figures_by_id = _figures_by_id(evidence_pack)
    issues: list[DocumentQaIssue] = []

    for index, slide in enumerate(spec.slides, start=1):
        slide_id = slide.id or f"slide-{index:03d}"
        issues.extend(_slide_message_issues(slide, slide_id=slide_id))
        issues.extend(_citation_issues(slide, slide_id=slide_id, evidence_by_id=evidence_by_id, has_evidence=evidence_pack is not None))
        issues.extend(
            _figure_issues(
                slide,
                slide_id=slide_id,
                figures_by_id=figures_by_id,
                evidence_path=evidence_path,
                has_evidence=evidence_pack is not None,
            )
        )
        issues.extend(_content_shape_issues(slide, slide_id=slide_id))

    return DocumentQaReport(ok=not any(issue.severity == "error" for issue in issues), issues=issues)


def write_document_qa_report(report: DocumentQaReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")


def run_pptx_render_qa(
    spec: PptSpec,
    *,
    pptx_path: Path,
    debug_source_trace: bool = False,
) -> DocumentQaReport:
    issues: list[DocumentQaIssue] = []
    slide_xml_by_index = _read_slide_xml(pptx_path)
    for index, slide in enumerate(spec.slides, start=1):
        slide_id = slide.id or f"slide-{index:03d}"
        xml = slide_xml_by_index.get(index, "")
        text = _xml_text(xml)
        if slide.content.figure_ids and "<p:pic" not in xml:
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:render_missing_picture_shape",
                    severity="warning",
                    slide_id=slide_id,
                    message="Slide has figure_ids but the rendered PPTX slide has no picture shape.",
                    suggested_fix="Verify figure paths in evidence.json and ensure the renderer can add_picture.",
                )
            )
        if slide.content.table_ids and "<a:tbl" not in xml and "Table summary" not in text:
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:render_missing_table_summary",
                    severity="warning",
                    slide_id=slide_id,
                    message="Slide has table_ids but the rendered PPTX slide has no table shape or table summary.",
                    suggested_fix="Use result_table_summary or render a compact table summary for table evidence.",
                )
            )
        if not debug_source_trace and "Source Trace" in text:
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:source_trace_visible",
                    severity="warning",
                    slide_id=slide_id,
                    message="Rendered slide body contains full Source Trace in non-debug mode.",
                    suggested_fix="Keep full source trace in debug notes only and render a compact footer.",
                )
            )
        if len(slide.bullets) > 3:
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:visible_body_over_budget",
                    severity="warning",
                    slide_id=slide_id,
                    message="Slide plan contains more than three body bullets; renderer should cap visible body text.",
                    suggested_fix="Keep visible slide body to at most three points and move details to speaker notes.",
                )
            )
        if _has_template_residue(text):
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:template_number_residue",
                    severity="warning",
                    slide_id=slide_id,
                    message="Rendered slide appears to contain fixed 01/02/03 template residue.",
                    suggested_fix="Use role-specific section labels instead of fixed numeric placeholders.",
                )
            )
    return DocumentQaReport(ok=not any(issue.severity == "error" for issue in issues), issues=issues)


def _slide_message_issues(slide: SlideSpec, *, slide_id: str) -> list[DocumentQaIssue]:
    issues: list[DocumentQaIssue] = []
    if not _is_cover_slide(slide) and not slide.citations:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:missing_citations",
                severity="error",
                slide_id=slide_id,
                message="Non-cover slide has no citations.",
                suggested_fix="Add at least one citation with an evidence_id from evidence.json.",
            )
        )
    if _is_key_claim_slide(slide) and not slide.citations:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:missing_citation",
                severity="error",
                slide_id=slide_id,
                message="Key claim slide has no citation.",
                suggested_fix="Add at least one citation with an evidence_id from evidence.json.",
            )
        )
    if not (slide.message or slide.core_message or "").strip():
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:missing_slide_message",
                severity="error",
                slide_id=slide_id,
                message="Slide message is empty.",
                suggested_fix="Add a concise message summarizing the slide's point.",
            )
        )
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:empty_message",
                severity="error",
                slide_id=slide_id,
                message="Slide message is empty.",
                suggested_fix="Add a concise message summarizing the slide's point.",
            )
        )
    elif _is_generic_message(slide):
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:message_too_generic",
                severity="warning",
                slide_id=slide_id,
                message="Slide message is too generic for a paper explanation deck.",
                suggested_fix="Make the message name the paper-specific method, result, dataset, or claim.",
            )
        )
    return issues


def _citation_issues(
    slide: SlideSpec,
    *,
    slide_id: str,
    evidence_by_id: dict[str, EvidenceItem],
    has_evidence: bool,
) -> list[DocumentQaIssue]:
    issues: list[DocumentQaIssue] = []
    if not has_evidence:
        return issues
    for citation in slide.citations:
        if citation.evidence_id not in evidence_by_id:
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:missing_evidence:{citation.evidence_id}",
                    severity="error",
                    slide_id=slide_id,
                    message=f"Citation references missing evidence_id: {citation.evidence_id}.",
                    suggested_fix="Use an evidence_id that exists in evidence.json or remove the citation.",
                )
            )
    return issues


def _figure_issues(
    slide: SlideSpec,
    *,
    slide_id: str,
    figures_by_id: dict[str, FigureAsset],
    evidence_path: Path | None,
    has_evidence: bool,
) -> list[DocumentQaIssue]:
    issues: list[DocumentQaIssue] = []
    if not slide.content.figure_ids or not has_evidence:
        return issues

    for figure_id in slide.content.figure_ids:
        figure = figures_by_id.get(figure_id)
        if figure is None:
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:missing_figure:{figure_id}",
                    severity="error",
                    slide_id=slide_id,
                    message=f"Slide references missing figure_id: {figure_id}.",
                    suggested_fix="Use a figure_id from evidence.json or remove it from content.figure_ids.",
                )
            )
            continue

        if figure.path and not _resolve_asset_path(figure.path, evidence_path=evidence_path).exists():
            issues.append(
                DocumentQaIssue(
                    id=f"{slide_id}:missing_figure_image:{figure_id}",
                    severity="warning",
                    slide_id=slide_id,
                    message=f"Figure image_path is missing for figure_id: {figure_id}.",
                    suggested_fix="Regenerate or copy the figure asset so the path in evidence.json exists.",
                )
            )

    return issues


def _content_shape_issues(slide: SlideSpec, *, slide_id: str) -> list[DocumentQaIssue]:
    issues: list[DocumentQaIssue] = []
    if len(slide.bullets) > 3:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:too_many_bullets",
                severity="warning",
                slide_id=slide_id,
                message=f"Slide has too many bullets: {len(slide.bullets)}.",
                suggested_fix="Reduce the slide to three or fewer bullets.",
            )
        )

    layout = (slide.layout or slide.layout_hint or "").strip()
    figure_layouts = {"figure_walkthrough", "figure_with_caption", "method_figure_callouts"}
    if slide.content.figure_ids and layout not in figure_layouts:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:layout_content_mismatch",
                severity="warning",
                slide_id=slide_id,
                message="Slide has figure_ids but does not use a figure walkthrough layout.",
                suggested_fix="Set layout to figure_walkthrough or remove content.figure_ids.",
            )
        )
    if layout in figure_layouts and not slide.content.figure_ids:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:layout_content_mismatch",
                severity="warning",
                slide_id=slide_id,
                message="Figure layout has no figure_ids.",
                suggested_fix="Add a figure_id from evidence.json or choose a non-figure layout.",
            )
        )
    if slide.content.figure_ids and not slide.content.visual_reason.strip():
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:figure_without_visual_reason",
                severity="warning",
                slide_id=slide_id,
                message="Slide references figure_ids but content.visual_reason is empty.",
                suggested_fix="Explain why the selected figure supports the slide message.",
            )
        )
    if slide.content.figure_ids == ["fig_001"] and not slide.content.visual_reason.strip():
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:default_first_figure_suspicious",
                severity="warning",
                slide_id=slide_id,
                message="Slide uses only fig_001 without a visual selection reason.",
                suggested_fix="Confirm the first figure is truly the best visual or choose a more specific figure.",
            )
        )
    if _is_result_slide(slide) and not (slide.content.table_ids or slide.content.metrics or slide.content.result_summary):
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:result_slide_without_result_evidence",
                severity="warning",
                slide_id=slide_id,
                message="Result slide has no table_ids, metrics, or result_summary.",
                suggested_fix="Ground result slides in a table, metric list, or structured result summary.",
            )
        )
    grounding_status = (slide.content.grounding_status or slide.grounding_status or "").lower()
    if grounding_status == "needs_verification":
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:needs_verification",
                severity="warning",
                slide_id=slide_id,
                message="Slide is marked as needs_verification.",
                suggested_fix="Review the slide against evidence before presenting.",
            )
        )
    return issues


def _is_cover_slide(slide: SlideSpec) -> bool:
    text = " ".join([slide.role, slide.layout, slide.layout_hint, slide.visual_type]).lower()
    return any(token in text for token in ("title", "cover"))


def _is_result_slide(slide: SlideSpec) -> bool:
    text = " ".join([slide.role, slide.layout, slide.layout_hint, slide.title]).lower()
    return any(token in text for token in ("result", "performance", "comparison"))


def _is_generic_message(slide: SlideSpec) -> bool:
    message = (slide.message or slide.core_message or "").strip().lower()
    if not message or message != slide.title.strip().lower():
        generic_phrases = (
            "this slide",
            "key takeaways",
            "main results",
            "method overview",
            "problem and motivation",
            "the evidence establishes",
        )
        if not any(phrase in message for phrase in generic_phrases):
            return False
    specific_tokens = {
        token.strip(".,:;()[]{}").lower()
        for token in " ".join([slide.title, *slide.bullets]).split()
        if len(token.strip(".,:;()[]{}")) >= 8
    }
    return len(specific_tokens) < 2


def _is_key_claim_slide(slide: SlideSpec) -> bool:
    role = slide.role.lower()
    text = " ".join([slide.title, slide.message, slide.core_message, *slide.bullets]).lower()
    if role in {"claim", "result", "takeaway", "takeaways", "recommendation", "figure_evidence", "evidence"}:
        return True
    return any(token in text for token in ("result", "conclusion", "takeaway", "recommend", "improves", "outperforms", "shows"))


def _evidence_by_id(evidence_pack: EvidencePack | None) -> dict[str, EvidenceItem]:
    if evidence_pack is None:
        return {}
    return {item.id: item for item in evidence_pack.evidence_items()}


def _figures_by_id(evidence_pack: EvidencePack | None) -> dict[str, FigureAsset]:
    if evidence_pack is None:
        return {}
    return {figure.id: figure for figure in evidence_pack.figures}


def _resolve_asset_path(path: str, *, evidence_path: Path | None) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or evidence_path is None:
        return candidate
    if candidate.exists():
        return candidate
    return evidence_path.parent / candidate


def _read_slide_xml(pptx_path: Path) -> dict[int, str]:
    try:
        with zipfile.ZipFile(pptx_path) as archive:
            names = sorted(
                (
                    name
                    for name in archive.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                ),
                key=_slide_xml_sort_key,
            )
            return {
                index: archive.read(name).decode("utf-8", errors="ignore")
                for index, name in enumerate(names, start=1)
            }
    except (OSError, zipfile.BadZipFile):
        return {}


def _slide_xml_sort_key(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _xml_text(xml: str) -> str:
    return " ".join(re.findall(r"<a:t>(.*?)</a:t>", xml, flags=re.DOTALL))


def _has_template_residue(text: str) -> bool:
    tokens = set(re.findall(r"\b0?[123]\b", text))
    return {"01", "02", "03"} <= tokens or {"1", "02", "03"} <= tokens
