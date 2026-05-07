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
    long_term_preferences = _maybe_read_long_term_memory(
        workspace,
        memory_types=["user_preference"],
        query=query,
        limit=limit,
    )
    long_term_accepted_outputs = _maybe_read_long_term_memory(
        workspace,
        memory_types=["accepted_output"],
        query=query,
        limit=limit,
    )
    preferences = _load_preferences(workspace)
    accepted_outputs = _read_jsonl(project_memory_dir(workspace) / ACCEPTED_OUTPUTS_FILE)
    return {
        "preferences": long_term_preferences
        if long_term_preferences is not None
        else _rank_records(preferences.get("preferences", []), query=query, limit=limit),
        "accepted_outputs": long_term_accepted_outputs
        if long_term_accepted_outputs is not None
        else _rank_records(accepted_outputs, query=query, limit=limit),
    }


def retrieve_failure_patterns(workspace: Path, *, query: str = "", limit: int = 20) -> dict[str, Any]:
    ensure_project_memory(workspace)
    long_term_failures = _maybe_read_long_term_memory(
        workspace,
        memory_types=["qa_failure"],
        query=query,
        limit=limit,
    )
    failures = _read_jsonl(project_memory_dir(workspace) / QA_FAILURES_FILE)
    return {
        "failure_patterns": long_term_failures
        if long_term_failures is not None
        else _rank_records(failures, query=query, limit=limit)
    }


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
    result = {"preference": stored, "path": str(path)}
    long_term_result = _maybe_write_long_term_memory(
        workspace,
        memory_type="user_preference",
        title=f"{inferred} preference",
        content=feedback.strip(),
        source_type=source,
        source_ref=preference["id"],
        tags=["project_memory", "preference", inferred],
    )
    if long_term_result is not None:
        result["long_term_memory"] = long_term_result
    return result


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
    record = {"created_at": _now(), "type": trace_type, "event": event, "payload": payload or {}}
    path = _append_jsonl(project_memory_dir(workspace) / file_name, record)
    result = {"path": str(path)}
    long_term_result = _maybe_write_long_term_memory(
        workspace,
        memory_type=trace_type,
        title=event,
        content=json.dumps(record, ensure_ascii=False, sort_keys=True),
        source_type="project_memory",
        source_ref=file_name,
        tags=["project_memory", trace_type],
    )
    if long_term_result is not None:
        result["long_term_memory"] = long_term_result
    return result


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


def _maybe_write_long_term_memory(
    workspace: Path,
    *,
    memory_type: str,
    title: str,
    content: str,
    source_type: str,
    source_ref: str,
    tags: list[str],
) -> dict[str, Any] | None:
    try:
        from ppt_agent.storage.memory_config import load_memory_config

        config = load_memory_config()
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}

    if not config.enabled:
        return None
    if not config.database_url:
        return {"status": "skipped", "reason": "PPT_AGENT_MEMORY_DATABASE_URL is not set"}
    if _contains_sensitive_memory_data(content):
        return {"status": "skipped", "reason": "sensitive memory content is not written to PostgreSQL"}

    try:
        from ppt_agent.storage.memory_db import CreateMemoryRecordInput
        from ppt_agent.storage.semantic_memory import write_semantic_memory

        result = write_semantic_memory(
            workspace,
            CreateMemoryRecordInput(
                memory_type=memory_type,
                title=title,
                content=content,
                source_type=source_type,
                source_ref=source_ref,
                tags=tags,
            ),
            config=config,
        )
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}

    return {
        "status": "written",
        "project_id": result.project.id,
        "record_id": result.record.id,
        "embedding_id": result.embedding.id if result.embedding else None,
    }


def _maybe_read_long_term_memory(
    workspace: Path,
    *,
    memory_types: list[str],
    query: str,
    limit: int,
) -> list[dict[str, Any]] | None:
    try:
        from ppt_agent.storage.memory_config import load_memory_config

        config = load_memory_config()
    except Exception:
        return None

    if not config.enabled or not config.database_url:
        return None

    try:
        if query.strip():
            from ppt_agent.storage.semantic_memory import search_semantic_memory

            results = search_semantic_memory(
                workspace,
                query,
                config=config,
                memory_types=memory_types,
                limit=limit,
            )
            return [_long_term_search_result_to_project_memory(item) for item in results]

        from ppt_agent.storage.memory_db import ensure_memory_project, list_memory_records
        from ppt_agent.storage.memory_scope import resolve_project_scope

        project = ensure_memory_project(resolve_project_scope(workspace), config=config)
        records = list_memory_records(project, memory_types=memory_types, limit=limit, config=config)
        return [_long_term_record_to_project_memory(record) for record in records]
    except Exception:
        return None


def _long_term_search_result_to_project_memory(result) -> dict[str, Any]:
    item = _long_term_record_to_project_memory(result.record)
    item["similarity"] = result.similarity
    item["embedding_model"] = result.embedding_model
    return item


def _long_term_record_to_project_memory(record) -> dict[str, Any]:
    base = {
        "id": record.id,
        "source": record.source_type,
        "source_ref": record.source_ref,
        "memory_type": record.memory_type,
        "title": record.title,
        "content": record.content,
        "module_path": record.module_path,
        "tags": record.tags,
        "importance": record.importance,
        "confidence": record.confidence,
    }
    if record.memory_type == "user_preference":
        return {
            **base,
            "category": _category_from_tags(record.tags),
            "preference": record.content,
        }
    try:
        payload = json.loads(record.content)
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        return {**base, **payload}
    return base


def _category_from_tags(tags: list[str]) -> str:
    for tag in tags:
        if tag not in {"project_memory", "preference"}:
            return tag
    return "general"


_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|auth(orization)?|bearer|password|passwd|secret|private[_-]?key|session[_-]?cookie|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]+|password\s*[:=]|private\s+key|session[_-]?cookie|cookie\s*:)",
    re.IGNORECASE,
)


def _contains_sensitive_memory_data(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY_RE.search(str(key)):
                return True
            if _contains_sensitive_memory_data(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive_memory_data(item) for item in value)
    if isinstance(value, str):
        if _SENSITIVE_TEXT_RE.search(value):
            return True
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return False
        return _contains_sensitive_memory_data(parsed)
    return False


def _record_id(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
