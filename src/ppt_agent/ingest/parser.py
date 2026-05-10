from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ParseResult(BaseModel):
    markdown_text: str
    source_path: Path
    assets_dir: Path | None = None
    content_list: list[dict[str, Any]] | None = None


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, source_path: Path) -> ParseResult:
        raise NotImplementedError


class MarkdownParser(DocumentParser):
    def parse(self, source_path: Path) -> ParseResult:
        path = Path(source_path)
        if path.suffix.lower() != ".md":
            raise ValueError(f"MarkdownParser only supports .md files: {path}")
        markdown_text = path.read_text(encoding="utf-8")
        return ParseResult(
            markdown_text=markdown_text,
            source_path=path,
            assets_dir=path.parent,
            content_list=_markdown_sections(markdown_text),
        )


def _markdown_sections(markdown_text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []

    for line in markdown_text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            if current is not None:
                current["text"] = "\n".join(body_lines).strip()
                sections.append(current)
            current = {
                "heading": match.group(2).strip(),
                "level": len(match.group(1)),
            }
            body_lines = []
            continue
        body_lines.append(line)

    if current is not None:
        current["text"] = "\n".join(body_lines).strip()
        sections.append(current)
    elif markdown_text.strip():
        sections.append({"heading": None, "level": None, "text": markdown_text.strip()})

    return sections
