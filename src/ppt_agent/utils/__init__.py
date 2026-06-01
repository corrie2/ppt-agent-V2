"""Shared utilities."""

import re

# Patterns for extracting output filename from user messages
OUTPUT_NAME_PATTERNS: list[re.Pattern] = [
    re.compile(r"输出文件名叫\s*([A-Za-z0-9_.\-]+)"),
    re.compile(r"文件名叫\s*([A-Za-z0-9_.\-]+)"),
    re.compile(r"命名为\s*([A-Za-z0-9_.\-]+)"),
    re.compile(r"输出到\s*([A-Za-z0-9_.\-]+)"),
    re.compile(r"ppt名叫\s*([A-Za-z0-9_.\-]+)"),
    re.compile(r"名叫\s*([A-Za-z0-9_.\-]+)"),
    re.compile(r"叫\s*([A-Za-z0-9_.\-]+)"),
]

# Style name detection from user messages
_STYLE_KEYWORDS: dict[str, list[str]] = {
    "academic": ["学术", "论文", "paper", "academic", "研究生", "graduate"],
    "modern": ["现代", "简约", "modern", "简洁", "clean"],
    "minimal": ["极简", "minimal", "黑白", "minimalist"],
    "corporate": ["商务", "corporate", "正式", "business", "professional"],
}


def detect_style_from_text(text: str) -> str | None:
    """Detect a style name from user message text. Returns None if no match."""
    lower = text.lower()
    for style_name, keywords in _STYLE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return style_name
    return None
