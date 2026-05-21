from __future__ import annotations

from pathlib import Path
from typing import Any

from ppt_agent.domain.models import DeckIntent, PptSpec
from ppt_agent.runtime.evidence_ingest import load_evidence_pack
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.utils.state import append_transition, state_get


def build_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    intent = DeckIntent.model_validate(state_get(state, "intent"))
    source_digest = spec.source_digest or intent.source_digest or {}
    evidence_path = source_digest.get("path") if isinstance(source_digest, dict) else None
    evidence_pack, resolved_evidence_path, _ = load_evidence_pack(evidence_path)
    artifact = build_pptx(spec, Path(intent.output_path), evidence_pack=evidence_pack, evidence_path=resolved_evidence_path)
    return {"artifact": artifact.model_dump(mode="json"), "transitions": append_transition(state, "build")}
