from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


STAGE_ORDER = [
    "request",
    "source_ingest",
    "brief_outline",
    "plan_confirm",
    "content",
    "design_chart",
    "slides_ir",
    "qa",
    "repair",
    "page_design",
    "renderer_engineer",
    "page_generator",
    "page_preview",
    "render_review",
    "build_confirm",
    "pptx_build",
    "visual_quality",
]

StageStatus = Literal[
    "pending",
    "running",
    "passed",
    "completed",
    "failed",
    "needs_rework",
    "skipped",
    "invalidated",
    "waiting_approval",
    "approved",
    "rejected",
]
TaskStatus = Literal["pending", "running", "completed", "failed", "needs_rework", "invalidated", "waiting_approval", "rejected"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HarnessStage(BaseModel):
    id: str
    name: str
    status: StageStatus = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    input_path: str | None = None
    output_path: str | None = None
    eval_path: str | None = None
    status_path: str | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    invalidated_by: str | None = None


class HarnessManifest(BaseModel):
    schema_version: int = 1
    task_id: str
    topic: str
    status: TaskStatus = "pending"
    current_stage: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    input: dict[str, Any] = Field(default_factory=dict)
    stages: list[HarnessStage] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)
    reports: dict[str, str] = Field(default_factory=dict)
    resume: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def stage(self, stage_id: str) -> HarnessStage | None:
        return next((stage for stage in self.stages if stage.id == stage_id), None)


def task_root(workspace: Path) -> Path:
    return workspace / ".ppt-agent" / "tasks"


def resolve_task_dir(workspace: Path, task_id_or_path: str | Path) -> Path:
    candidate = Path(task_id_or_path)
    if candidate.exists():
        return candidate.resolve()
    return (task_root(workspace) / str(task_id_or_path)).resolve()


def manifest_path(task_dir: Path) -> Path:
    return task_dir / "manifest.json"


def create_manifest(task_dir: Path, *, task_id: str, topic: str, metadata: dict[str, Any] | None = None) -> HarnessManifest:
    task_dir.mkdir(parents=True, exist_ok=True)
    manifest = HarnessManifest(
        task_id=task_id,
        topic=topic,
        status="running",
        current_stage="request",
        stages=[HarnessStage(id=stage_id, name=_stage_name(stage_id)) for stage_id in STAGE_ORDER],
        metadata=metadata or {},
    )
    save_manifest(task_dir, manifest)
    return manifest


def load_manifest(task_dir: Path) -> HarnessManifest:
    path = manifest_path(task_dir)
    if not path.exists():
        raise ValueError(f"manifest not found: {path}")
    try:
        return HarnessManifest.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid manifest JSON: {path}") from exc


def save_manifest(task_dir: Path, manifest: HarnessManifest) -> Path:
    manifest.updated_at = now_iso()
    path = manifest_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def update_stage(
    task_dir: Path,
    manifest: HarnessManifest,
    stage_id: str,
    *,
    status: StageStatus,
    input_path: str | None = None,
    output_path: str | None = None,
    eval_path: str | None = None,
    status_path: str | None = None,
    issues: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    invalidated_by: str | None = None,
) -> HarnessStage:
    stage = manifest.stage(stage_id)
    if stage is None:
        stage = HarnessStage(id=stage_id, name=_stage_name(stage_id))
        manifest.stages.append(stage)
    stage.status = status
    if input_path is not None:
        stage.input_path = _rel(task_dir, Path(input_path))
    if output_path is not None:
        stage.output_path = _rel(task_dir, Path(output_path))
    if eval_path is not None:
        stage.eval_path = _rel(task_dir, Path(eval_path))
    if status_path is not None:
        stage.status_path = _rel(task_dir, Path(status_path))
    if issues is not None:
        stage.issues = issues
    if metrics is not None:
        stage.metrics = metrics
    if started_at is not None:
        stage.started_at = started_at
    if finished_at is not None:
        stage.finished_at = finished_at
    if invalidated_by is not None:
        stage.invalidated_by = invalidated_by
    manifest.current_stage = stage_id
    _refresh_resume(manifest)
    save_manifest(task_dir, manifest)
    return stage


def invalidate_from_stage(task_dir: Path, manifest: HarnessManifest, stage_id: str, *, reason: str) -> HarnessManifest:
    seen = False
    for stage in manifest.stages:
        if stage.id == stage_id:
            seen = True
        if seen:
            stage.status = "invalidated"
            stage.invalidated_by = reason
    manifest.status = "invalidated"
    manifest.current_stage = stage_id
    _refresh_resume(manifest)
    save_manifest(task_dir, manifest)
    return manifest


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _refresh_resume(manifest: HarnessManifest) -> None:
    passed_statuses = {"passed", "completed", "approved", "skipped"}
    passed = [stage.id for stage in manifest.stages if stage.status in passed_statuses]
    last_passed = passed[-1] if passed else None
    next_stage = None
    if last_passed in STAGE_ORDER:
        index = STAGE_ORDER.index(last_passed)
        for stage_id in STAGE_ORDER[index + 1 :]:
            stage = manifest.stage(stage_id)
            if stage is None or stage.status not in passed_statuses:
                next_stage = stage_id
                break
    elif manifest.stages:
        next_stage = manifest.stages[0].id
    manifest.resume = {"last_passed_stage": last_passed, "next_stage": next_stage}
    if any(stage.status == "waiting_approval" for stage in manifest.stages):
        manifest.status = "waiting_approval"
        manifest.current_stage = next((stage.id for stage in manifest.stages if stage.status == "waiting_approval"), manifest.current_stage)
    elif any(stage.status == "rejected" for stage in manifest.stages):
        manifest.status = "rejected"
    elif all(stage.status in passed_statuses for stage in manifest.stages if stage.id in STAGE_ORDER):
        manifest.status = "completed"
        manifest.current_stage = None
    elif any(stage.status == "failed" for stage in manifest.stages):
        manifest.status = "failed"
    elif any(stage.status == "needs_rework" for stage in manifest.stages):
        manifest.status = "needs_rework"


def _stage_name(stage_id: str) -> str:
    return stage_id.replace("_", " ").title()


def _rel(task_dir: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(task_dir.resolve()))
    except ValueError:
        return str(path)
