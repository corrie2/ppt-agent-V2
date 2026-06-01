from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ppt_agent.ingest.parser import ParseResult

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MinerUOptions:
    backend: str | None = None
    method: str | None = None
    lang: str | None = None
    start: int | None = None
    end: int | None = None
    formula: bool | None = None  # None = use MinerU default; False = skip MFR
    table: bool | None = None
    image_analysis: bool | None = None
    timeout_seconds: int | None = None
    batch_threshold: int = 8  # pages; switch to batched mode above this
    batch_size: int = 2  # pages per batch (1GB GPU, keep small to avoid OOM)


class MinerUAdapter:
    def __init__(self, options: MinerUOptions | None = None) -> None:
        self.options = options or MinerUOptions()

    def parse(self, input_path: Path, output_dir: Path) -> ParseResult:
        source_path = Path(input_path)
        target_dir = Path(output_dir)
        if not _has_complete_output(target_dir):
            page_count = _get_pdf_page_count(source_path)
            if page_count > self.options.batch_threshold:
                self._run_mineru_batched(source_path, target_dir, page_count)
            else:
                self._run_mineru(source_path, target_dir)
        return _read_mineru_output(source_path, target_dir)

    def _run_mineru_batched(self, input_path: Path, output_dir: Path, page_count: int) -> None:
        """Process large PDFs in batches using a persistent MinerU server (model loaded once)."""
        import signal
        import time
        import urllib.request

        batch_size = self.options.batch_size
        output_dir.mkdir(parents=True, exist_ok=True)

        # Clean up stale batch dirs from previous failed runs
        for old in output_dir.glob("batch_*"):
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)

        # Find a free port for MinerU server
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        api_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

        # Start MinerU server once
        _logger.info("Starting MinerU server on %s (model loads once)...", api_url)
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "mineru.cli.fast_api", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            # Wait for server to be ready
            for _ in range(120):  # up to 120s for model loading
                if server_proc.poll() is not None:
                    raise RuntimeError("MinerU server exited unexpectedly during startup")
                try:
                    urllib.request.urlopen(f"{api_url}/health", timeout=2)
                    break
                except Exception:
                    time.sleep(1)
            else:
                raise RuntimeError("MinerU server failed to start within 120s")
            _logger.info("MinerU server ready on %s", api_url)

            for batch_start in range(0, page_count, batch_size):
                batch_end = min(batch_start + batch_size, page_count)
                batch_dir = output_dir / f"batch_{batch_start:04d}_{batch_end:04d}"
                batch_opts = MinerUOptions(
                    backend=self.options.backend,
                    method=self.options.method,
                    lang=self.options.lang,
                    start=batch_start,
                    end=batch_end,
                    formula=self.options.formula,
                    table=self.options.table,
                    image_analysis=self.options.image_analysis,
                    timeout_seconds=self.options.timeout_seconds,
                    batch_threshold=999999,
                    batch_size=999999,
                )
                batch_adapter = MinerUAdapter(batch_opts)
                batch_adapter._run_mineru(input_path, batch_dir, api_url=api_url)
                if not _has_mineru_output(batch_dir):
                    raise RuntimeError(
                        f"MinerU batch {batch_start}-{batch_end} produced no output"
                    )

            _merge_batch_outputs(output_dir, page_count, batch_size)
            (output_dir / ".mineru_complete").write_text("ok")
            for old in output_dir.glob("batch_*"):
                if old.is_dir():
                    shutil.rmtree(old, ignore_errors=True)

        except Exception:
            for old in output_dir.glob("batch_*"):
                if old.is_dir():
                    shutil.rmtree(old, ignore_errors=True)
            sentinel = output_dir / ".mineru_complete"
            if sentinel.exists():
                sentinel.unlink()
            raise
        finally:
            # Always stop the server
            _logger.info("Stopping MinerU server...")
            server_proc.send_signal(signal.SIGTERM)
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()

    def _run_mineru(self, input_path: Path, output_dir: Path, *, api_url: str | None = None) -> None:
        if shutil.which("mineru") is None:
            raise RuntimeError("MinerU is not installed or not found in PATH")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _build_mineru_command(input_path=input_path, output_dir=output_dir, options=self.options, api_url=api_url)
        env = os.environ.copy()
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.options.timeout_seconds,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            page_range = ""
            if self.options.start is not None or self.options.end is not None:
                page_range = f" (pages {self.options.start or 0}-{self.options.end or '?'})"
            message = f"MinerU failed{page_range} with exit code {exc.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from exc
        except subprocess.TimeoutExpired as exc:
            page_range = ""
            if self.options.start is not None or self.options.end is not None:
                page_range = f" (pages {self.options.start or 0}-{self.options.end or '?'})"
            raise RuntimeError(
                f"MinerU timed out{page_range} after {self.options.timeout_seconds} seconds"
            ) from exc


def _build_mineru_command(*, input_path: Path, output_dir: Path, options: MinerUOptions, api_url: str | None = None) -> list[str]:
    command = ["mineru", "-p", str(input_path), "-o", str(output_dir)]
    if api_url:
        command.extend(["--api-url", api_url])
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
        command.extend(["--formula", str(options.formula).lower()])
    if options.table is not None:
        command.extend(["--table", str(options.table).lower()])
    if options.image_analysis is not None:
        command.extend(["--image-analysis", str(options.image_analysis).lower()])
    return command


def _has_mineru_output(output_dir: Path) -> bool:
    """Check if MinerU produced output in the given directory."""
    if not output_dir.exists():
        return False
    return _find_markdown(output_dir) is not None or (output_dir / "content_list.json").exists()


def _has_complete_output(output_dir: Path) -> bool:
    """Check if MinerU output is complete (sentinel file exists, not just partial batch output)."""
    if not output_dir.exists():
        return False
    # For batched output: sentinel file marks completion
    if (output_dir / ".mineru_complete").exists():
        return True
    # For non-batch output: check directly (no batch_* dirs should exist)
    if any(output_dir.glob("batch_*")):
        # Stale batch dirs from a failed run — treat as incomplete
        return False
    return _has_mineru_output(output_dir)


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


def _get_pdf_page_count(pdf_path: Path) -> int:
    """Get total page count of a PDF using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf is required for page count detection. Install it with: pip install pypdf")
    try:
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception as exc:
        raise RuntimeError(f"Failed to read PDF page count: {exc}") from exc


def _merge_batch_outputs(output_dir: Path, total_pages: int, batch_size: int) -> None:
    """Merge batch outputs into a single output compatible with _read_mineru_output."""
    # Sort by parsing numeric start from batch_XXXX_YYYY names (CRITICAL-1)
    batch_dirs = sorted(
        (d for d in output_dir.glob("batch_*") if d.is_dir()),
        key=lambda d: _parse_batch_start(d.name),
    )
    if not batch_dirs:
        return

    # Merge markdown files
    merged_md = ""
    # Store raw content_list items (no double normalization — MEDIUM-4)
    all_content_list_raw: list[dict[str, Any]] = []
    merged_images_dir = output_dir / "images"
    merged_images_dir.mkdir(exist_ok=True)

    for batch_dir in batch_dirs:
        batch_start = _parse_batch_start(batch_dir.name)
        md_path = _find_markdown(batch_dir)
        if md_path:
            batch_md = md_path.read_text(encoding="utf-8")
            # Fix image references to match prefixed filenames (MEDIUM-1)
            batch_images = _find_assets_dir(batch_dir, markdown_path=md_path)
            if batch_images and batch_images.exists():
                batch_md = _rewrite_image_refs(batch_md, batch_images, batch_dir.name, merged_images_dir)
            merged_md += batch_md + "\n\n"
        cl_path = _find_content_list(batch_dir)
        if cl_path:
            # Read raw items and offset page numbers (CRITICAL-3)
            raw_items = json.loads(cl_path.read_text(encoding="utf-8"))
            if isinstance(raw_items, list):
                for item in raw_items:
                    if isinstance(item, dict):
                        _offset_page(item, batch_start)
                        _remap_content_list_image_path(item, batch_dir.name)
                        all_content_list_raw.append(item)
        # Copy images recursively with batch prefix to avoid name collisions (MEDIUM-1, MEDIUM-6)
        batch_images = _find_assets_dir(batch_dir, markdown_path=md_path)
        if batch_images and batch_images.exists():
            _copy_images_with_prefix(batch_images, merged_images_dir, batch_dir.name)

    # Write merged markdown
    md_files = sorted(output_dir.glob("*.md"))
    if not md_files:
        md_name = output_dir.name + ".md"
        (output_dir / md_name).write_text(merged_md, encoding="utf-8")

    # Write merged content_list.json (raw items with corrected pages, no double normalization)
    if all_content_list_raw:
        (output_dir / "content_list.json").write_text(
            json.dumps(all_content_list_raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _parse_batch_start(dir_name: str) -> int:
    """Extract numeric start from batch_XXXX_YYYY directory name."""
    parts = dir_name.split("_")
    # batch_XXXX_YYYY -> parts = ["batch", "XXXX", "YYYY"]
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 0


def _offset_page(item: dict[str, Any], offset: int) -> None:
    """Offset page numbers in a content_list item by the given amount (CRITICAL-3)."""
    if offset == 0:
        return
    for key in ("page", "page_idx", "page_number"):
        if item.get(key) is not None:
            try:
                item[key] = int(item[key]) + offset
            except (ValueError, TypeError):
                pass


def _remap_content_list_image_path(item: dict[str, Any], batch_name: str) -> None:
    """Update image paths in a content_list item to match batch-prefixed filenames."""
    for key in ("img_path", "path", "image_path", "table_path"):
        value = item.get(key)
        if value and isinstance(value, str):
            filename = Path(value).name
            item[key] = f"images/{batch_name}_{filename}"


def _copy_images_with_prefix(src_dir: Path, dest_dir: Path, batch_name: str) -> None:
    """Copy images recursively, prefixing names with batch name to avoid collisions (MEDIUM-1, MEDIUM-6)."""
    prefix = batch_name  # e.g. "batch_0000_0003"
    for src_file in src_dir.rglob("*"):
        if not src_file.is_file():
            continue
        # Preserve subdirectory structure
        rel = src_file.relative_to(src_dir)
        dest_file = dest_dir / f"{prefix}_{rel}"
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        if not dest_file.exists():
            shutil.copy2(str(src_file), str(dest_file))


def _rewrite_image_refs(markdown: str, batch_images_dir: Path, batch_name: str, merged_images_dir: Path) -> str:
    """Rewrite image paths in markdown to match prefixed filenames after merge."""
    import re

    # Match markdown image syntax: ![alt](path) and HTML <img src="path">
    prefix = batch_name

    def _replace_md_ref(match: re.Match) -> str:
        alt = match.group(1)
        old_path = match.group(2)
        new_path = _remap_image_path(old_path, batch_images_dir, merged_images_dir, prefix)
        return f"![{alt}]({new_path})"

    def _replace_html_ref(match: re.Match) -> str:
        before = match.group(1)
        old_path = match.group(2)
        after = match.group(3)
        new_path = _remap_image_path(old_path, batch_images_dir, merged_images_dir, prefix)
        return f'{before}{new_path}{after}'

    markdown = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace_md_ref, markdown)
    markdown = re.sub(r'(<img\s+[^>]*src=["\'])([^"\']+)(["\'][^>]*>)', _replace_html_ref, markdown)
    return markdown


def _remap_image_path(old_path: str, batch_images_dir: Path, merged_images_dir: Path, prefix: str) -> str:
    """Map an old image path to the new prefixed path."""
    # Extract filename from the path
    filename = Path(old_path).name
    # Just use prefix_filename, stripping any directory like "images/"
    new_name = f"{prefix}_{filename}"
    # Return relative path from merged output dir
    return f"images/{new_name}"
