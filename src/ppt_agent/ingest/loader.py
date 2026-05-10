from __future__ import annotations

from pathlib import Path

from ppt_agent.domain.evidence import EvidencePack
from ppt_agent.ingest.evidence_builder import EvidenceBuilder
from ppt_agent.ingest.parser import DocumentParser, MarkdownParser


def load_evidence_pack(
    source_path: Path,
    *,
    parser: DocumentParser | None = None,
    builder: EvidenceBuilder | None = None,
) -> EvidencePack:
    resolved_parser = parser or _default_parser(source_path)
    resolved_builder = builder or EvidenceBuilder()
    return resolved_builder.build(resolved_parser.parse(source_path))


def _default_parser(source_path: Path) -> DocumentParser:
    path = Path(source_path)
    if path.suffix.lower() == ".md":
        return MarkdownParser()
    raise ValueError(f"No document parser registered for: {path}")
