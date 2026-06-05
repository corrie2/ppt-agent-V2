from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(task_dir: Path, event: str, *, stage_id: str | None = None, payload: dict[str, Any] | None = None) -> Path:
    path = task_dir / "logs" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "created_at": now_iso(),
        "event": event,
        "stage_id": stage_id,
        "payload": payload or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def read_events(task_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = task_dir / "logs" / "events.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"created_at": None, "event": "invalid_event_line", "payload": {"line": line}})
    if limit is not None:
        return records[-limit:]
    return records
