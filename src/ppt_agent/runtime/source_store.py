from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


DIGEST_FIELDS = ("title", "abstract", "problem", "method", "experiments", "results", "limitations")


def source_store_root(workspace: Path) -> Path:
    return workspace / ".ppt-agent" / "data" / "sources"


def memory_store_path(workspace: Path) -> Path:
    return workspace / ".ppt-agent" / "data" / "memory" / "events.jsonl"


def append_memory_event(workspace: Path, event: dict[str, Any]) -> Path:
    path = memory_store_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"created_at": datetime.now().isoformat(timespec="seconds"), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def source_id_for_path(path: Path) -> str:
    resolved = path.resolve()
    stat = resolved.stat()
    raw = f"{resolved}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def index_source(path: Path, *, workspace: Path, chunk_chars: int = 1200) -> dict[str, Any]:
    source_id = source_id_for_path(path)
    source_dir = source_store_root(workspace) / source_id
    source_dir.mkdir(parents=True, exist_ok=True)

    text, warnings = extract_source_text(path)
    cleaned = " ".join(text.split())
    chunks = _chunk_text(cleaned, chunk_chars=chunk_chars)
    digest = build_source_digest(path, cleaned, warnings=warnings)
    metadata = {
        "source_id": source_id,
        "path": str(path.resolve()),
        "name": path.name,
        "size": path.stat().st_size,
        "modified_time": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
        "chunk_count": len(chunks),
        "warnings": warnings,
    }

    _write_json(source_dir / "metadata.json", metadata)
    _write_jsonl(source_dir / "text.jsonl", [{"source_id": source_id, "text": cleaned}])
    _write_jsonl(
        source_dir / "chunks.jsonl",
        [
            {"source_id": source_id, "chunk_id": f"{source_id}-{index:04d}", "index": index, "text": chunk}
            for index, chunk in enumerate(chunks, start=1)
        ],
    )
    _write_json(source_dir / "digest.json", digest)
    return {"source_id": source_id, "metadata": metadata, "digest": digest, "warnings": warnings}


def ingest_sources(paths: list[Path], *, workspace: Path) -> dict[str, Any]:
    indexed = []
    warnings: list[str] = []
    for path in paths:
        try:
            result = index_source(path, workspace=workspace)
        except Exception as exc:
            warnings.append(f"{path.name}: source indexing failed: {exc}")
            continue
        indexed.append(result)
        warnings.extend(result.get("warnings", []))
    return {"indexed": indexed, "warnings": warnings}


def load_source_digest(source_id: str, *, workspace: Path) -> dict[str, Any] | None:
    path = source_store_root(workspace) / source_id / "digest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def digest_sources(paths: list[Path], *, workspace: Path) -> dict[str, Any]:
    indexed = ingest_sources(paths, workspace=workspace)
    digests = [item["digest"] for item in indexed["indexed"]]
    return {"sources": digests, "warnings": indexed["warnings"]}


def retrieve_source_context(paths: list[Path], *, workspace: Path, query: str = "", limit: int = 5) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    query_terms = _terms(query)
    for path in paths:
        try:
            source_id = source_id_for_path(path)
        except OSError as exc:
            warnings.append(f"{path.name}: cannot resolve source id: {exc}")
            continue
        chunks_path = source_store_root(workspace) / source_id / "chunks.jsonl"
        if not chunks_path.exists():
            index_result = index_source(path, workspace=workspace)
            warnings.extend(index_result.get("warnings", []))
        records.extend(_read_jsonl(chunks_path))

    scored = sorted(records, key=lambda item: _score_chunk(item.get("text", ""), query_terms), reverse=True)
    return {"contexts": scored[:limit], "warnings": warnings}


def extract_source_text(path: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not path.exists():
        return "", [f"{path.name}: source file does not exist"]
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if text.strip():
                return text, warnings
            warnings.append(f"{path.name}: no extractable PDF text")
        except Exception as exc:
            warnings.append(f"{path.name}: PDF text extraction failed: {exc}")
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            warnings.append(f"{path.name}: no extractable source text")
        return text, warnings
    except OSError as exc:
        warnings.append(f"{path.name}: fallback text read failed: {exc}")
        return "", warnings


def build_source_digest(path: Path, text: str, *, warnings: list[str] | None = None) -> dict[str, Any]:
    digest = {
        "source_id": source_id_for_path(path),
        "path": str(path.resolve()),
        "name": path.name,
        "title": _guess_title(text, path),
        "abstract": _section_text(text, "abstract"),
        "problem": _keyword_window(text, ("problem", "challenge", "motivation", "question")),
        "method": _keyword_window(text, ("method", "approach", "algorithm", "system", "architecture")),
        "experiments": _keyword_window(text, ("experiment", "evaluation", "setup", "dataset", "benchmark")),
        "results": _keyword_window(text, ("result", "performance", "improve", "outperform", "finding")),
        "limitations": _keyword_window(text, ("limitation", "threat", "future work", "discussion")),
        "warnings": list(warnings or []),
    }
    for field in DIGEST_FIELDS:
        if not digest[field]:
            digest[field] = "unknown"
            digest["warnings"].append(f"{path.name}: digest field unavailable: {field}")
    return digest


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _chunk_text(text: str, *, chunk_chars: int) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_chars] for index in range(0, len(text), chunk_chars)]


def _guess_title(text: str, path: Path) -> str:
    for line in re_split_sentences(text)[:5]:
        candidate = line.strip()
        if 8 <= len(candidate) <= 180:
            return candidate
    return path.stem


def _section_text(text: str, section: str) -> str:
    lower = text.lower()
    index = lower.find(section)
    if index < 0:
        return ""
    return text[index : index + 900].strip()


def _keyword_window(text: str, keywords: tuple[str, ...]) -> str:
    lower = text.lower()
    for keyword in keywords:
        index = lower.find(keyword.lower())
        if index >= 0:
            return text[index : index + 700].strip()
    return ""


def _terms(query: str) -> set[str]:
    return {term for term in query.lower().replace("_", " ").split() if len(term) > 2}


def _score_chunk(text: str, query_terms: set[str]) -> int:
    if not query_terms:
        return 0
    lower = text.lower()
    return sum(lower.count(term) for term in query_terms)


def re_split_sentences(text: str) -> list[str]:
    return [part.strip() for part in text.replace("\n", ". ").split(". ") if part.strip()]
