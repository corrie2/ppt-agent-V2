"""Document ingestion interfaces for EvidencePack generation."""

from ppt_agent.ingest.evidence_builder import EvidenceBuilder
from ppt_agent.ingest.loader import load_evidence_pack
from ppt_agent.ingest.mineru_adapter import MinerUAdapter
from ppt_agent.ingest.parser import DocumentParser, MarkdownParser, ParseResult

__all__ = [
    "DocumentParser",
    "EvidenceBuilder",
    "MarkdownParser",
    "MinerUAdapter",
    "ParseResult",
    "load_evidence_pack",
]
