from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvidenceBackedText(BaseModel):
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class Contribution(BaseModel):
    claim: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class ImportantFigure(BaseModel):
    figure_id: str
    role: str = ""
    reason: str = ""


class ImportantTable(BaseModel):
    table_id: str
    role: str = ""
    reason: str = ""


class MethodComponent(BaseModel):
    name: str = ""
    description: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class MethodAnalysis(BaseModel):
    overview: str = ""
    components: list[MethodComponent] = Field(default_factory=list)
    important_figures: list[ImportantFigure] = Field(default_factory=list)


class ResultFinding(BaseModel):
    result: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class ExperimentAnalysis(BaseModel):
    datasets: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    main_results: list[ResultFinding] = Field(default_factory=list)
    important_tables: list[ImportantTable] = Field(default_factory=list)


class AnalysisFinding(BaseModel):
    finding: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class Limitation(BaseModel):
    limitation: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class DeckOutlineItem(BaseModel):
    slide_role: str = ""
    message: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SourceSummary(BaseModel):
    evidence_path: str = ""
    sections_used: list[str] = Field(default_factory=list)
    figures_used: list[str] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)


class PaperAnalysis(BaseModel):
    schema_version: int = 1
    paper_title: str = ""
    source_summary: SourceSummary = Field(default_factory=SourceSummary)
    problem: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    motivation: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    core_idea: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    contributions: list[Contribution] = Field(default_factory=list)
    method: MethodAnalysis = Field(default_factory=MethodAnalysis)
    experiments: ExperimentAnalysis = Field(default_factory=ExperimentAnalysis)
    ablation_or_analysis: list[AnalysisFinding] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)
    recommended_deck_outline: list[DeckOutlineItem] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    def to_json(self, *, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PaperAnalysis":
        return cls.model_validate(payload)
