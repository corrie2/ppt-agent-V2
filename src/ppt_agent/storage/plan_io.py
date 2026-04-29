from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic import ValidationError

from ppt_agent.domain.models import DeckIntent, PptSpec

PLAN_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class PlanDocument:
    path: Path
    payload: dict
    spec: PptSpec
    source_type: str
    schema_version: int | None


class ValidateReport(BaseModel):
    path: str
    ok: bool
    format: str
    schema_version: int | None = None
    source_type: str
    slides_count: int = 0
    title: str | None = None
    request_topic: str | None = None
    request_audience: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MigratePlanResult(BaseModel):
    input_path: str
    output_path: str
    source_type: str
    target_schema_version: int
    already_current: bool = False


def build_plan_payload(
    intent: DeckIntent,
    spec: PptSpec,
    *,
    mode: str,
    approved: bool,
    transitions: list[str],
    theme: str | None = None,
    metadata: dict | None = None,
) -> dict:
    resolved_theme = theme or spec.theme
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "request": intent.model_dump(mode="json"),
        "title": spec.title,
        "theme": resolved_theme,
        "outline": [slide.title for slide in spec.slides],
        "slides": [slide.model_dump(mode="json") for slide in spec.slides],
        "mode": mode,
        "approved": approved,
        "transitions": transitions,
    }
    extra = metadata or {}
    if spec.output_format != "pptx" or extra.get("output_format"):
        payload["output_format"] = extra.get("output_format") or spec.output_format
    if spec.applied_skills or extra.get("applied_skills"):
        payload["applied_skills"] = extra.get("applied_skills") or spec.applied_skills
    if spec.skill_root or extra.get("skill_root"):
        payload["skill_root"] = extra.get("skill_root") or spec.skill_root
    if spec.skill_md_path or extra.get("skill_md_path"):
        payload["skill_md_path"] = extra.get("skill_md_path") or spec.skill_md_path
    if spec.source_digest or extra.get("source_digest"):
        payload["source_digest"] = extra.get("source_digest") or spec.source_digest
    if spec.grounding_warnings or extra.get("grounding_warnings"):
        payload["grounding_warnings"] = extra.get("grounding_warnings") or spec.grounding_warnings
    if extra.get("project_memory"):
        payload["metadata"] = {"project_memory": extra["project_memory"]}
    return payload


def write_plan_document(
    path: Path,
    *,
    intent: DeckIntent,
    spec: PptSpec,
    mode: str,
    approved: bool,
    transitions: list[str],
    theme: str | None = None,
    metadata: dict | None = None,
) -> None:
    payload = build_plan_payload(
        intent,
        spec,
        mode=mode,
        approved=approved,
        transitions=transitions,
        theme=theme,
        metadata=metadata,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_plan_document(path: Path) -> PlanDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg}") from exc

    try:
        # Canonical plan/spec document with an explicit schema_version.
        if isinstance(raw, dict) and "schema_version" in raw:
            spec = PptSpec.model_validate(
                {
                    "title": raw.get("title"),
                    "audience": (raw.get("request") or {}).get("audience", "general business audience"),
                    "theme": raw.get("theme") or "executive_blue",
                    "slides": raw.get("slides"),
                    "source_digest": raw.get("source_digest"),
                    "applied_skills": raw.get("applied_skills") or [],
                    "output_format": raw.get("output_format") or "pptx",
                    "skill_root": raw.get("skill_root"),
                    "skill_md_path": raw.get("skill_md_path"),
                    "grounding_warnings": raw.get("grounding_warnings") or [],
                }
            )
            return PlanDocument(
                path=path,
                payload=raw,
                spec=spec,
                source_type="versioned",
                schema_version=raw.get("schema_version"),
            )

        # Legacy compatibility for bare PptSpec JSON before the unified wrapper schema.
        if isinstance(raw, dict) and _looks_like_bare_pptspec(raw):
            spec = PptSpec.model_validate(raw)
            payload = build_plan_payload(
                DeckIntent(topic=spec.title, audience=spec.audience),
                spec,
                mode="plan",
                approved=False,
                transitions=[],
            )
            return PlanDocument(
                path=path,
                payload=payload,
                spec=spec,
                source_type="bare_pptspec",
                schema_version=None,
            )

        # Legacy compatibility for older plan/spec files that predate schema_version.
        if isinstance(raw, dict) and "slides" in raw:
            spec = PptSpec.model_validate(
                {
                    "title": raw.get("title"),
                    "audience": (raw.get("request") or {}).get("audience", "general business audience"),
                    "theme": raw.get("theme") or "executive_blue",
                    "slides": raw.get("slides"),
                }
            )
            compatible = build_plan_payload(
                DeckIntent(
                    topic=(raw.get("request") or {}).get("topic", raw.get("title")),
                    audience=(raw.get("request") or {}).get("audience", "general business audience"),
                    tone=(raw.get("request") or {}).get("tone", "clear and pragmatic"),
                    output_path=(raw.get("request") or {}).get("output_path", "deck.pptx"),
                ),
                spec,
                mode=raw.get("mode", "plan"),
                approved=raw.get("approved", False),
                transitions=raw.get("transitions", []),
                theme=raw.get("theme"),
            )
            return PlanDocument(
                path=path,
                payload=compatible,
                spec=spec,
                source_type="legacy_slides",
                schema_version=None,
            )

        # Legacy compatibility for older plan-out files using `slide_specs`.
        if isinstance(raw, dict) and "slide_specs" in raw:
            spec = PptSpec.model_validate(
                {
                    "title": raw.get("title"),
                    "audience": (raw.get("request") or {}).get("audience", "general business audience"),
                    "theme": raw.get("theme") or "executive_blue",
                    "slides": raw.get("slide_specs"),
                }
            )
            compatible = {
                "schema_version": PLAN_SCHEMA_VERSION,
                "request": raw.get("request", {}),
                "title": raw.get("title"),
                "theme": raw.get("theme"),
                "outline": raw.get("outline", []),
                "slides": raw.get("slide_specs", []),
                "mode": raw.get("mode"),
                "approved": raw.get("approved", False),
                "transitions": raw.get("transitions", []),
            }
            return PlanDocument(
                path=path,
                payload=compatible,
                spec=spec,
                source_type="legacy_slide_specs",
                schema_version=None,
            )

        spec = PptSpec.model_validate(raw)
        payload = build_plan_payload(
            DeckIntent(topic=spec.title, audience=spec.audience),
            spec,
            mode="plan",
            approved=False,
            transitions=[],
        )
        return PlanDocument(
            path=path,
            payload=payload,
            spec=spec,
            source_type="bare_pptspec",
            schema_version=None,
        )
    except ValidationError as exc:
        raise ValueError(f"invalid plan schema in {path}: {exc}") from exc


def validate_plan_document(path: Path) -> ValidateReport:
    versioned_report = _validate_version_header(path)
    if versioned_report is not None:
        return versioned_report

    try:
        document = read_plan_document(path)
    except ValueError as exc:
        return ValidateReport(
            path=str(path),
            ok=False,
            format="invalid schema",
            source_type="invalid",
            errors=[str(exc)],
        )

    request = document.payload.get("request", {})
    warnings: list[str] = []

    if document.source_type == "versioned":
        version = document.schema_version
        if not isinstance(version, int) or version < 1:
            return ValidateReport(
                path=str(path),
                ok=False,
                format="invalid schema",
                schema_version=version if isinstance(version, int) else None,
                source_type=document.source_type,
                slides_count=len(document.spec.slides),
                title=document.spec.title,
                request_topic=request.get("topic", document.spec.title),
                request_audience=request.get("audience", document.spec.audience),
                errors=["invalid schema_version"],
            )
        if version > PLAN_SCHEMA_VERSION:
            return ValidateReport(
                path=str(path),
                ok=False,
                format="unsupported schema version",
                schema_version=version,
                source_type=document.source_type,
                slides_count=len(document.spec.slides),
                title=document.spec.title,
                request_topic=request.get("topic", document.spec.title),
                request_audience=request.get("audience", document.spec.audience),
                errors=["unsupported future schema version"],
            )

        visual_pages = sum(1 for slide in document.spec.slides if slide.visual_type or slide.resolved_asset or slide.visual_spec)
        if visual_pages == 0:
            warnings.append("no visual metadata detected; consider migrating or enriching this plan")
        return ValidateReport(
            path=str(path),
            ok=True,
            format="formal schema",
            schema_version=version,
            source_type=document.source_type,
            slides_count=len(document.spec.slides),
            title=document.spec.title,
            request_topic=request.get("topic", document.spec.title),
            request_audience=request.get("audience", document.spec.audience),
            warnings=warnings,
        )

    warnings.append("legacy compatibility format")
    warnings.append(_migration_recommendation(path))
    return ValidateReport(
        path=str(path),
        ok=True,
        format="legacy compatibility",
        schema_version=None,
        source_type=document.source_type,
        slides_count=len(document.spec.slides),
        title=document.spec.title,
        request_topic=request.get("topic", document.spec.title),
        request_audience=request.get("audience", document.spec.audience),
        warnings=warnings,
    )


def normalize_plan_document(document: PlanDocument) -> dict:
    return build_plan_payload(
        _intent_from_document(document),
        document.spec,
        mode=document.payload.get("mode", "plan"),
        approved=document.payload.get("approved", False),
        transitions=document.payload.get("transitions", []),
        theme=document.payload.get("theme"),
    )


def migrate_plan_document(input_path: Path, output_path: Path) -> MigratePlanResult:
    document = read_plan_document(input_path)
    payload = normalize_plan_document(document)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return MigratePlanResult(
        input_path=str(input_path),
        output_path=str(output_path),
        source_type=document.source_type,
        target_schema_version=PLAN_SCHEMA_VERSION,
        already_current=document.source_type == "versioned" and document.schema_version == PLAN_SCHEMA_VERSION,
    )


def _intent_from_document(document: PlanDocument) -> DeckIntent:
    request = document.payload.get("request", {})
    return DeckIntent(
        topic=request.get("topic", document.spec.title),
        audience=request.get("audience", document.spec.audience),
        tone=request.get("tone", "clear and pragmatic"),
        output_path=request.get("output_path", "deck.pptx"),
    )


def _looks_like_bare_pptspec(raw: dict) -> bool:
    if "slides" not in raw:
        return False
    wrapper_keys = {"request", "theme", "mode", "approved", "transitions", "schema_version", "outline"}
    return not any(key in raw for key in wrapper_keys)


def _migration_recommendation(path: Path) -> str:
    normalized = path.with_name(f"{path.stem}.normalized.json")
    return f"Recommendation: run `ppt-agent migrate-plan {path} --out {normalized}`"


def _validate_version_header(path: Path) -> ValidateReport | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or "schema_version" not in raw:
        return None

    version = raw.get("schema_version")
    request = raw.get("request") if isinstance(raw.get("request"), dict) else {}
    slides = raw.get("slides")
    slides_count = len(slides) if isinstance(slides, list) else 0
    title = raw.get("title") if isinstance(raw.get("title"), str) else None
    audience = request.get("audience") if isinstance(request.get("audience"), str) else None
    topic = request.get("topic") if isinstance(request.get("topic"), str) else None

    if not isinstance(version, int) or version < 1:
        return ValidateReport(
            path=str(path),
            ok=False,
            format="invalid schema",
            schema_version=version if isinstance(version, int) else None,
            source_type="versioned",
            slides_count=slides_count,
            title=title,
            request_topic=topic or title,
            request_audience=audience,
            errors=["invalid schema_version"],
        )
    if version > PLAN_SCHEMA_VERSION:
        return ValidateReport(
            path=str(path),
            ok=False,
            format="unsupported schema version",
            schema_version=version,
            source_type="versioned",
            slides_count=slides_count,
            title=title,
            request_topic=topic or title,
            request_audience=audience,
            errors=["unsupported future schema version"],
        )
    return None
