from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


MEMORY_DIR = ".ppt-agent/memory"
USER_PREFERENCES_FILE = "user_preferences.json"
EXECUTION_TRACES_FILE = "execution_traces.jsonl"
QA_FAILURES_FILE = "qa_failures.jsonl"
ACCEPTED_OUTPUTS_FILE = "accepted_outputs.jsonl"


def project_memory_dir(workspace: Path) -> Path:
    return workspace / MEMORY_DIR


def ensure_project_memory(workspace: Path) -> Path:
    root = project_memory_dir(workspace)
    root.mkdir(parents=True, exist_ok=True)
    preferences = root / USER_PREFERENCES_FILE
    if not preferences.exists():
        _write_json(preferences, {"preferences": []})
    for name in (EXECUTION_TRACES_FILE, QA_FAILURES_FILE, ACCEPTED_OUTPUTS_FILE):
        (root / name).touch(exist_ok=True)
    return root


def retrieve_project_memory(workspace: Path, *, query: str = "", limit: int = 20) -> dict[str, Any]:
    ensure_project_memory(workspace)
    preferences = _load_preferences(workspace)
    accepted_outputs = _read_jsonl(project_memory_dir(workspace) / ACCEPTED_OUTPUTS_FILE)
    return {
        "preferences": _rank_records(preferences.get("preferences", []), query=query, limit=limit),
        "accepted_outputs": _rank_records(accepted_outputs, query=query, limit=limit),
    }


def retrieve_failure_patterns(workspace: Path, *, query: str = "", limit: int = 20) -> dict[str, Any]:
    ensure_project_memory(workspace)
    failures = _read_jsonl(project_memory_dir(workspace) / QA_FAILURES_FILE)
    return {"failure_patterns": _rank_records(failures, query=query, limit=limit)}


def record_project_memory(
    workspace: Path,
    *,
    feedback: str,
    category: str | None = None,
    source: str = "user_feedback",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_project_memory(workspace)
    inferred = category or infer_preference_category(feedback)
    preference = {
        "id": _record_id(feedback),
        "created_at": _now(),
        "updated_at": _now(),
        "source": source,
        "category": inferred,
        "preference": feedback.strip(),
        "metadata": metadata or {},
    }

    path = project_memory_dir(workspace) / USER_PREFERENCES_FILE
    data = _load_preferences(workspace)
    records = data.setdefault("preferences", [])
    existing = next((item for item in records if item.get("id") == preference["id"]), None)
    if existing:
        existing.update({key: value for key, value in preference.items() if key != "created_at"})
        stored = existing
    else:
        records.append(preference)
        stored = preference
    _write_json(path, data)
    return {"preference": stored, "path": str(path)}


def record_execution_trace(
    workspace: Path,
    *,
    event: str,
    payload: dict[str, Any] | None = None,
    trace_type: str = "execution",
) -> dict[str, Any]:
    ensure_project_memory(workspace)
    file_name = {
        "qa_failure": QA_FAILURES_FILE,
        "accepted_output": ACCEPTED_OUTPUTS_FILE,
    }.get(trace_type, EXECUTION_TRACES_FILE)
    path = _append_jsonl(
        project_memory_dir(workspace) / file_name,
        {"created_at": _now(), "type": trace_type, "event": event, "payload": payload or {}},
    )
    return {"path": str(path)}


def infer_preference_category(text: str) -> str:
    normalized = text.strip().lower()
    if any(token in normalized for token in ("风格", "style", "研究生", "学术", "graduate", "academic")):
        return "style"
    if any(token in normalized for token in ("正文", "字太多", "文字太多", "text-heavy", "too much text")):
        return "content_density"
    if any(token in normalized for token in ("空方框", "空框", "placeholder", "empty box")):
        return "visual_constraints"
    if any(token in normalized for token in ("不要", "别", "avoid", "don't", "do not")):
        return "avoidance"
    return "general"


def looks_like_user_preference(text: str) -> bool:
    normalized = text.strip().lower()
    if len(normalized) < 3:
        return False
    markers = (
        "不要",
        "别",
        "不喜欢",
        "太多",
        "太少",
        "希望",
        "偏好",
        "风格",
        "avoid",
        "prefer",
        "too much",
        "too many",
        "style",
    )
    return any(marker in normalized for marker in markers) or ("要" in normalized and "风格" in normalized)


def _load_preferences(workspace: Path) -> dict[str, Any]:
    path = project_memory_dir(workspace) / USER_PREFERENCES_FILE
    if not path.exists():
        return {"preferences": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"preferences": []}
    if not isinstance(data, dict):
        return {"preferences": []}
    if not isinstance(data.get("preferences"), list):
        data["preferences"] = []
    return data


def _rank_records(records: list[dict[str, Any]], *, query: str, limit: int) -> list[dict[str, Any]]:
    if not query:
        return records[-limit:]
    terms = _terms(query)
    scored = [
        (item, _score(json.dumps(item, ensure_ascii=False), terms))
        for item in records
    ]
    matched = [item for item, score in sorted(scored, key=lambda pair: pair[1], reverse=True) if score > 0]
    if len(matched) >= limit:
        return matched[:limit]
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in matched}
    recent = [
        item
        for item in records[-limit:]
        if json.dumps(item, ensure_ascii=False, sort_keys=True) not in seen
    ]
    return [*matched, *recent][:limit]


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    ascii_terms = {term for term in re.split(r"[^a-z0-9]+", lowered) if len(term) > 2}
    cjk_terms = {lowered[index : index + 2] for index in range(max(len(lowered) - 1, 0)) if "\u4e00" <= lowered[index] <= "\u9fff"}
    return ascii_terms | cjk_terms


def _score(text: str, terms: set[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _append_jsonl(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_id(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
