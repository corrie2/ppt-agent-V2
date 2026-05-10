from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ppt_agent.ingest.parser import ParseResult


@dataclass(frozen=True)
class MinerUOptions:
    backend: str | None = None
    method: str | None = None
    lang: str | None = None
    start: int | None = None
    end: int | None = None
    formula: bool | None = None
    table: bool | None = None
    image_analysis: bool | None = None


class MinerUAdapter:
    def __init__(self, options: MinerUOptions | None = None) -> None:
        self.options = options or MinerUOptions()

    def parse(self, input_path: Path, output_dir: Path) -> ParseResult:
        source_path = Path(input_path)
        target_dir = Path(output_dir)
        if not _has_mineru_output(target_dir):
            self._run_mineru(source_path, target_dir)
        return _read_mineru_output(source_path, target_dir)

    def _run_mineru(self, input_path: Path, output_dir: Path) -> None:
        if shutil.which("mineru") is None:
            raise RuntimeError("MinerU is not installed or not found in PATH")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _build_mineru_command(input_path=input_path, output_dir=output_dir, options=self.options)
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            message = f"MinerU failed with exit code {exc.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from exc


def _build_mineru_command(*, input_path: Path, output_dir: Path, options: MinerUOptions) -> list[str]:
    command = ["mineru", "-p", str(input_path), "-o", str(output_dir)]
    if options.method is not None:
        command.extend(["--method", options.method])
    if options.backend is not None:
        command.extend(["--backend", options.backend])
    if options.lang is not None:
        command.extend(["--lang", options.lang])
    if options.start is not None:
        command.extend(["--start", str(options.start)])
    if options.end is not None:
        command.extend(["--end", str(options.end)])
    if options.formula is not None:
        command.extend(["--formula", str(options.formula)])
    if options.table is not None:
        command.extend(["--table", str(options.table)])
    if options.image_analysis is not None:
        command.extend(["--image-analysis", str(options.image_analysis)])
    return command


def _has_mineru_output(output_dir: Path) -> bool:
    if not output_dir.exists():
        return False
    return _find_markdown(output_dir) is not None or (output_dir / "content_list.json").exists()


def _read_mineru_output(source_path: Path, output_dir: Path) -> ParseResult:
    markdown_path = _find_markdown(output_dir)
    content_list_path = _find_content_list(output_dir)
    markdown_text = markdown_path.read_text(encoding="utf-8") if markdown_path else ""
    content_list = _read_content_list(content_list_path, output_dir=output_dir) if content_list_path else None
    return ParseResult(
        markdown_text=markdown_text,
        source_path=source_path,
        assets_dir=_find_assets_dir(output_dir, markdown_path=markdown_path),
        content_list=content_list,
    )


def _find_markdown(output_dir: Path) -> Path | None:
    direct = sorted(output_dir.glob("*.md"))
    if direct:
        return direct[0]
    nested = sorted(output_dir.rglob("*.md"))
    return nested[0] if nested else None


def _find_content_list(output_dir: Path) -> Path | None:
    for candidate in (output_dir / "content_list.json", output_dir / f"{output_dir.name}_content_list.json"):
        if candidate.exists():
            return candidate
    nested = sorted(path for path in output_dir.rglob("*content_list.json") if not path.name.endswith("_v2.json"))
    return nested[0] if nested else None


def _find_assets_dir(output_dir: Path, *, markdown_path: Path | None = None) -> Path | None:
    if markdown_path is not None:
        for name in ("images", "assets"):
            candidate = markdown_path.parent / name
            if candidate.exists() and candidate.is_dir():
                return candidate
    for name in ("images", "assets"):
        candidate = output_dir / name
        if candidate.exists() and candidate.is_dir():
            return candidate
    nested = sorted(path for path in output_dir.rglob("*") if path.is_dir() and path.name in {"images", "assets"})
    if nested:
        return nested[0]
    return None


def _read_content_list(path: Path, *, output_dir: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("MinerU content_list.json must contain a list")
    return [_normalize_content_item(item, output_dir=output_dir) for item in raw if isinstance(item, dict)]


def _normalize_content_item(item: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    item_type = str(item.get("type") or item.get("category") or "").lower()
    if item_type == "text" and item.get("text_level") is not None:
        item_type = "title"
    normalized = {
        "type": item_type,
        "text": item.get("text") or item.get("content") or "",
        "caption": item.get("caption") or "",
        "page": _normalized_page(item),
        "bbox": item.get("bbox"),
    }
    if item_type == "title":
        normalized["heading"] = normalized["text"]
        normalized["level"] = item.get("level") or item.get("text_level") or 1
    if item_type in {"image", "chart", "table"}:
        path = item.get("path") or item.get("img_path") or item.get("image_path")
        if path:
            normalized["path"] = path
        for key in ("img_path", "image_path", "table_path", "image_caption", "chart_caption", "table_caption", "table_body"):
            if key in item:
                normalized[key] = item[key]
    if item_type in {"image", "chart", "table", "text", "title"}:
        return normalized
    return {**item, **normalized}


def _normalized_page(item: dict[str, Any]) -> int:
    if item.get("page") is not None:
        return int(item["page"])
    if item.get("page_idx") is not None:
        return int(item["page_idx"]) + 1
    if item.get("page_number") is not None:
        return int(item["page_number"])
    return 1
