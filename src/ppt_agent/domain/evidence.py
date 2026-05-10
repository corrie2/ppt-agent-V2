from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, Field


BBox = tuple[float, float, float, float]


class SourceRef(BaseModel):
    id: str
    source_file: str
    path: str | None = None
    title: str | None = None
    page_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    id: str
    source_file: str
    page: int | None = None
    bbox: BBox | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SectionEvidence(EvidenceItem):
    text: str
    heading: str | None = None
    level: int | None = None


class FigureAsset(EvidenceItem):
    caption: str = ""
    path: str | None = None
    text: str | None = None


class TableAsset(EvidenceItem):
    caption: str = ""
    text: str | None = None
    path: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class ClaimEvidence(EvidenceItem):
    text: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class EvidencePack(BaseModel):
    source_files: list[SourceRef] = Field(default_factory=list)
    sections: list[SectionEvidence] = Field(default_factory=list)
    figures: list[FigureAsset] = Field(default_factory=list)
    tables: list[TableAsset] = Field(default_factory=list)
    claims: list[ClaimEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_json(self, *, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, payload: str) -> Self:
        return cls.model_validate_json(payload)

    def evidence_items(self) -> list[EvidenceItem]:
        return [*self.sections, *self.figures, *self.tables, *self.claims]
