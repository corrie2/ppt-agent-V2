from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from pydantic import BaseModel


class ImageAssetError(RuntimeError):
    """Raised when image asset resolution fails."""


class ImageAssetResult(BaseModel):
    local_path: str
    source_url: str
    source_name: str
    license_note: str
    match_reason: str


class WikimediaCommonsImageProvider:
    api_url = "https://commons.wikimedia.org/w/api.php"
    source_name = "Wikimedia Commons"

    def search(self, query: str, *, timeout: float = 20.0) -> dict:
        response = httpx.get(
            self.api_url,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 5,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "iiurlwidth": 1600,
                "format": "json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        pages = (data.get("query") or {}).get("pages") or {}
        candidates = sorted(pages.values(), key=lambda page: page.get("index", 999))
        for page in candidates:
            imageinfo = (page.get("imageinfo") or [None])[0]
            if imageinfo and imageinfo.get("thumburl"):
                return {
                    "title": page.get("title", "Untitled image"),
                    "image_url": imageinfo.get("thumburl"),
                    "source_url": imageinfo.get("descriptionurl") or imageinfo.get("thumburl"),
                    "license_note": _extract_license_note(imageinfo.get("extmetadata") or {}),
                }
        raise ImageAssetError(f"no image result found for query: {query}")


def resolve_image_asset(*, query: str, prompt: str = "", cache_dir: Path | None = None, provider: WikimediaCommonsImageProvider | None = None) -> ImageAssetResult:
    search_term = (query or prompt).strip()
    if not search_term:
        raise ImageAssetError("image query is empty")

    resolved_provider = provider or WikimediaCommonsImageProvider()
    match = resolved_provider.search(search_term)
    cache_root = cache_dir or default_image_cache_dir()
    local_path = _download_image(match["image_url"], cache_root)
    return ImageAssetResult(
        local_path=str(local_path),
        source_url=match["source_url"],
        source_name=resolved_provider.source_name,
        license_note=match["license_note"],
        match_reason=f"Matched query '{search_term}' to '{match['title']}'.",
    )


def default_image_cache_dir(cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / ".ppt-agent" / "assets" / "images"


def _download_image(url: str, cache_dir: Path, *, timeout: float = 30.0) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix or ".jpg"
    filename = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + suffix
    target = cache_dir / filename
    if target.exists():
        return target

    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target


def _extract_license_note(metadata: dict) -> str:
    fields = [
        metadata.get("LicenseShortName", {}).get("value"),
        metadata.get("UsageTerms", {}).get("value"),
        metadata.get("License", {}).get("value"),
    ]
    for field in fields:
        if field:
            return str(field)
    return "See source page for license details."
