from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


SUPPORTED_PATTERNS = ("*.pdf", "*.docx", "*.md", "*.json", "*.pptx")


class WorkspaceFile(BaseModel):
    name: str
    file_type: str
    path: str
    size: int
    relative_path: str
    modified_time: str
    page_count: int | None = None


def scan_workspace(cwd: Path | None = None, *, max_depth: int = 3) -> list[WorkspaceFile]:
    root = (cwd or Path.cwd()).resolve()
    results: list[WorkspaceFile] = []

    try:
        candidates = root.rglob("*")
        for path in candidates:
            try:
                if not path.is_file():
                    continue
                relative_parts = path.relative_to(root).parts
                if len(relative_parts) > max_depth:
                    continue
                if not _is_supported(path):
                    continue
                stat = path.stat()
            except (OSError, ValueError):
                continue
            results.append(
                WorkspaceFile(
                    name=path.name,
                    file_type=path.suffix.lower().lstrip("."),
                    path=str(path),
                    size=stat.st_size,
                    relative_path=str(path.relative_to(root)),
                    modified_time=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    page_count=_read_page_count(path),
                )
            )
    except OSError:
        pass

    return sorted(results, key=lambda item: (item.file_type, item.name))


def _is_supported(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in {".pdf", ".docx", ".md", ".json", ".pptx"}


def _read_page_count(path: Path) -> int | None:
    if path.suffix.lower() != ".pdf":
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None

    # Best-effort PDF page counting without adding a new dependency.
    try:
        count = content.count(b"/Type /Page")
        return count if count > 0 else None
    except Exception:
        return None
