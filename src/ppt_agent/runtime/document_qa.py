from __future__ import annotations

import json
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


def _slide_message_issues(slide: SlideSpec, *, slide_id: str) -> list[DocumentQaIssue]:
    issues: list[DocumentQaIssue] = []
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
                id=f"{slide_id}:empty_message",
                severity="error",
                slide_id=slide_id,
                message="Slide message is empty.",
                suggested_fix="Add a concise message summarizing the slide's point.",
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
    if len(slide.bullets) > 5:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:too_many_bullets",
                severity="warning",
                slide_id=slide_id,
                message=f"Slide has too many bullets: {len(slide.bullets)}.",
                suggested_fix="Reduce the slide to five or fewer bullets.",
            )
        )

    layout = (slide.layout or slide.layout_hint or "").strip()
    if slide.content.figure_ids and layout != "figure_with_caption":
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:layout_content_mismatch",
                severity="warning",
                slide_id=slide_id,
                message="Slide has figure_ids but does not use figure_with_caption layout.",
                suggested_fix="Set layout to figure_with_caption or remove content.figure_ids.",
            )
        )
    if layout == "figure_with_caption" and not slide.content.figure_ids:
        issues.append(
            DocumentQaIssue(
                id=f"{slide_id}:layout_content_mismatch",
                severity="warning",
                slide_id=slide_id,
                message="figure_with_caption layout has no figure_ids.",
                suggested_fix="Add a figure_id from evidence.json or choose a non-figure layout.",
            )
        )
    return issues


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
    return evidence_path.parent / candidate
