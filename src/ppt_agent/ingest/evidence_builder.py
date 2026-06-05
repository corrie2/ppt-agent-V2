from __future__ import annotations

import hashlib
from pathlib import Path

from ppt_agent.domain.evidence import EvidencePack, FigureAsset, SectionEvidence, SourceRef, TableAsset
from ppt_agent.ingest.parser import ParseResult


class EvidenceBuilder:
    def build(self, parse_result: ParseResult) -> EvidencePack:
        source_path = Path(parse_result.source_path)
        sections: list[SectionEvidence] = []
        figures: list[FigureAsset] = []
        tables: list[TableAsset] = []
        pending_heading: dict | None = None
        figure_index = 0
        table_index = 0

        for index, item in enumerate(parse_result.content_list or [], start=1):
            item_type = str(item.get("type") or "").lower()
            if item_type == "title":
                pending_heading = item
                title_text = (item.get("text") or item.get("heading") or "").strip()
                if title_text:
                    sections.append(
                        SectionEvidence(
                            id=_section_id(source_path, index),
                            source_file=source_path.name,
                            page=_page(item),
                            bbox=_bbox(item),
                            heading=title_text,
                            level=item.get("level") or 1,
                            text=title_text,
                        )
                    )
                continue
            if item_type in {"image", "chart"}:
                figure_index += 1
                figures.append(
                    FigureAsset(
                        id=f"fig_{figure_index:03d}",
                        source_file=source_path.name,
                        page=_page(item),
                        bbox=_bbox(item),
                        caption=_caption(item, item_type=item_type),
                        path=_asset_path(item, assets_dir=parse_result.assets_dir, item_type=item_type),
                        text=item.get("text"),
                    )
                )
                continue
            if item_type == "table":
                table_index += 1
                tables.append(
                    TableAsset(
                        id=f"table_{table_index:03d}",
                        source_file=source_path.name,
                        page=_page(item),
                        bbox=_bbox(item),
                        caption=_caption(item, item_type=item_type),
                        text=item.get("table_body") or item.get("text"),
                        path=_asset_path(item, assets_dir=parse_result.assets_dir, item_type=item_type),
                    )
                )
                continue

            text = (item.get("text") or "").strip()
            heading = item.get("heading")
            if not heading and pending_heading is not None:
                heading = pending_heading.get("text") or pending_heading.get("heading")
            if text or heading:
                sections.append(
                    SectionEvidence(
                        id=_section_id(source_path, index),
                        source_file=source_path.name,
                        page=_page(item),
                        bbox=_bbox(item),
                        heading=heading,
                        level=item.get("level"),
                        text=text or str(heading),
                    )
                )
                pending_heading = None

        if not sections and parse_result.markdown_text.strip():
            sections.append(
                SectionEvidence(
                    id=_section_id(source_path, 1),
                    source_file=source_path.name,
                    page=1,
                    text=parse_result.markdown_text.strip(),
                )
            )

        return EvidencePack(
            source_files=[
                SourceRef(
                    id=_source_id(source_path),
                    source_file=source_path.name,
                    path=str(Path(source_path).resolve()),
                    title=source_path.stem,
                )
            ],
            sections=sections,
            figures=figures,
            tables=tables,
            metadata={
                "source_path": str(source_path),
                "assets_dir": str(parse_result.assets_dir) if parse_result.assets_dir else None,
            },
        )


def _source_id(source_path: Path) -> str:
    return hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:16]


def _section_id(source_path: Path, index: int) -> str:
    return f"{_source_id(source_path)}-section-{index:04d}"


def _page(item: dict) -> int | None:
    value = item.get("page") or item.get("page_idx") or item.get("page_number")
    if value is None:
        return 1
    return int(value)


def _bbox(item: dict):
    bbox = item.get("bbox")
    if bbox is None:
        return None
    if len(bbox) != 4:
        raise ValueError("bbox must contain exactly four values")
    return tuple(float(value) for value in bbox)


def _caption(item: dict, *, item_type: str) -> str:
    value = (
        item.get("caption")
        or item.get(f"{item_type}_caption")
        or item.get("image_caption")
        or item.get("chart_caption")
        or item.get("table_caption")
        or ""
    )
    if isinstance(value, list):
        return " ".join(str(part).strip() for part in value if str(part).strip())
    return str(value).strip()


def _asset_path(item: dict, *, assets_dir: Path | None, item_type: str) -> str | None:
    value = item.get("path") or item.get("img_path") or item.get("image_path")
    if item_type == "table":
        value = value or item.get("table_path")
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return str(path)
    if assets_dir is None:
        return str(path)
    if path.parts and path.parts[0] == assets_dir.name:
        return str(assets_dir.parent / path)
    return str(assets_dir / path)
