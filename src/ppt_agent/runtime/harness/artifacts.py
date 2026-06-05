from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def stage_dir(task_dir: Path, order: int, stage_id: str) -> Path:
    return task_dir / "stages" / f"{order:02d}_{stage_id}"


def copy_artifact(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists() and source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
