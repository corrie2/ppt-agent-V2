from __future__ import annotations

import re
from pathlib import Path

from ppt_agent.shell.session import ShellSession


TOPIC_PHRASES = (
    "\u8bba\u6587\u4ecb\u7ecd",
    "\u8bb2\u89e3pdf",
    "\u6280\u672f\u603b\u7ed3",
    "\u7ba1\u7406\u5c42\u6c47\u62a5",
)


def merge_text_into_draft(session: ShellSession, text: str) -> dict:
    extracted = extract_request_constraints(text)
    if extracted:
        session.draft_request.merge(extracted)
    _sync_selected_sources(session)
    return extracted


def extract_request_constraints(text: str) -> dict:
    values: dict = {}
    normalized = text.lower()
    if any(token in normalized for token in ("guizang-ppt-skill", "杂志风", "横向翻页", "网页 ppt", "html deck", "single html", "electronic magazine", "editorial deck")):
        values["output_format"] = "html"
        values["applied_skills"] = ["guizang-ppt-skill"]
    if "杂志风" in text or "magazine" in normalized or "editorial" in normalized:
        values["theme"] = "magazine"
    if _is_exclude_other_sources_confirmation(text):
        values["exclude_other_sources"] = True
    requested_pdf = _extract_requested_pdf_name(text)
    if requested_pdf:
        values["requested_pdf_name"] = requested_pdf
    requested_index = _extract_requested_pdf_index(text)
    if requested_index:
        values["requested_pdf_index"] = requested_index
    min_slides = _extract_min_slides(text)
    if min_slides:
        values["min_slides"] = min_slides
    slide_count = _extract_slide_count(text)
    if slide_count and not min_slides:
        values["slide_count"] = slide_count
    audience = _extract_audience(text)
    if audience:
        values["audience"] = audience
    topic = _extract_topic(text)
    if topic:
        values["topic"] = topic
    return values


def try_resolve_draft_sources(session: ShellSession) -> tuple[bool, str | None]:
    pdfs = [item for item in session.discovered_sources if item["file_type"] == "pdf"]
    if not pdfs:
        return False, None

    if session.draft_request.requested_pdf_index:
        index = session.draft_request.requested_pdf_index
        if 1 <= index <= len(pdfs):
            _set_selected_sources(session, [pdfs[index - 1]["path"]])
            _apply_default_topic_if_ready(session)
            return True, None
        return False, f"Requested PDF index {index} is out of range. Available PDFs: {_available_pdf_names(pdfs)}"

    if session.draft_request.requested_pdf_name:
        matches = [item for item in pdfs if matches_pdf_name(session.draft_request.requested_pdf_name or "", item["name"])]
        if len(matches) == 1:
            _set_selected_sources(session, [matches[0]["path"]])
            _apply_default_topic_if_ready(session)
            return True, None
        if len(matches) > 1:
            return False, f"Multiple PDFs match '{session.draft_request.requested_pdf_name}': {_available_pdf_names(matches)}"
        return False, (
            f"Could not find the requested PDF: {session.draft_request.requested_pdf_name}\n"
            f"Available PDFs: {_available_pdf_names(pdfs)}"
        )

    _sync_selected_sources(session)
    _apply_default_topic_if_ready(session)
    return bool(session.draft_request.selected_sources), None


def draft_has_enough_for_plan(session: ShellSession) -> bool:
    return bool(session.draft_request.selected_sources and session.draft_request.topic)


def render_draft_feedback(session: ShellSession) -> list[str]:
    draft = session.draft_request
    lines = ["\u5df2\u8bb0\u5f55\u5f53\u524d\u9700\u6c42\uff1a"]
    if draft.selected_sources:
        lines.append(f"- Source PDFs: {', '.join(Path(path).name for path in draft.selected_sources)}")
    elif draft.requested_pdf_name:
        lines.append(f"- Requested PDF: {draft.requested_pdf_name}")
    if draft.audience:
        lines.append(f"- Audience: {draft.audience}")
    if draft.min_slides:
        lines.append(f"- Minimum slides: {draft.min_slides}")
    elif draft.slide_count:
        lines.append(f"- Slides: {draft.slide_count}")
    if draft.topic:
        lines.append(f"- Topic: {draft.topic}")
    if draft.applied_skills:
        lines.append(f"- Applied skill: {', '.join(draft.applied_skills)}")
    if draft.output_format:
        lines.append(f"- Output format: {draft.output_format}")
    else:
        default_topic = infer_default_topic(session)
        if default_topic:
            lines.append(f'\u8fd8\u7f3a\u5c11\u4e3b\u9898\u3002\u4f60\u53ef\u4ee5\u8f93\u5165\u201c\u8bba\u6587\u4ecb\u7ecd\u201d\uff0c\u6216\u8f93\u5165\u201c\u5f00\u59cb\u201d\u4f7f\u7528\u9ed8\u8ba4\u4e3b\u9898\uff1a{default_topic}\u3002')
        else:
            lines.append("\u8fd8\u7f3a\u5c11\u4e3b\u9898\u3002")
    return lines


def infer_default_topic(session: ShellSession) -> str | None:
    sources = session.draft_request.selected_sources or session.selected_pdf_paths()
    if len(sources) != 1:
        return None
    if not (session.draft_request.audience or session.draft_request.min_slides or session.draft_request.slide_count):
        return None
    return f"{Path(sources[0]).stem} \u8bba\u6587\u4ecb\u7ecd"


def ensure_default_topic(session: ShellSession) -> bool:
    if session.draft_request.topic:
        return False
    default_topic = infer_default_topic(session)
    if not default_topic:
        return False
    session.draft_request.topic = default_topic
    return True


def matches_pdf_name(requested_name: str, actual_name: str) -> bool:
    requested = _normalize_pdf_name(requested_name)
    actual = _normalize_pdf_name(actual_name)
    actual_stem = _normalize_pdf_name(Path(actual_name).stem)
    return requested in {actual, actual_stem} or requested in actual or actual_stem in requested


def _sync_selected_sources(session: ShellSession) -> None:
    selected = session.selected_sources or session.draft_request.selected_sources
    if selected:
        _set_selected_sources(session, selected)


def _set_selected_sources(session: ShellSession, sources: list[str]) -> None:
    session.selected_sources = list(sources)
    session.draft_request.selected_sources = list(sources)
    if len(sources) == 1:
        session.draft_request.requested_pdf_name = Path(sources[0]).name


def _apply_default_topic_if_ready(session: ShellSession) -> None:
    ensure_default_topic(session)


def _extract_requested_pdf_name(text: str) -> str | None:
    match = re.search(r"([A-Za-z0-9][A-Za-z0-9 _.-]*?\.pdf)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    for pattern in (
        r"(?:\u4e0d\u8981\u7528.+?\uff0c\s*)?(?:\u6539\u7528|\u53ea\u7528|\u4ec5\u4f7f\u7528|\u4f7f\u7528|\u7528|\u505a|\u57fa\u4e8e)\s*([A-Za-z0-9][A-Za-z0-9 _.-]{0,80})",
        r"(?:switch to|use|make|based on)\s+([A-Za-z0-9][A-Za-z0-9 _.-]{0,80})",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = re.split(r"[,，。；;\s]|\d", match.group(1).strip(), maxsplit=1)[0].strip(" .")
            if candidate.lower() not in {"pdf", "ppt", "deck", "plan", "slides"}:
                return candidate
    return None


def _is_exclude_other_sources_confirmation(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.lower())
    return any(
        phrase in normalized
        for phrase in (
            "\u4e0d\u5305\u542b\u5176\u4ed6pdf",
            "\u4e0d\u5305\u542b\u53e6\u4e00\u4e2a",
            "\u4e0d\u7528\u53e6\u4e00\u4e2a",
            "\u53ea\u7528\u8fd9\u4e2a",
            "\u4ec5\u4f7f\u7528\u8fd9\u4e2a",
            "\u4e0d\u5305\u542b\u5176\u4ed6\u6587\u4ef6",
            "\u6392\u9664\u5176\u4ed6pdf",
        )
    )


def _extract_requested_pdf_index(text: str) -> int | None:
    if not any(token in text for token in ("\u4ec5\u4f7f\u7528", "\u53ea\u7528", "\u4f7f\u7528", "\u7528")):
        return None
    match = re.search(r"\u7b2c\s*(\d+)\s*\u4e2a", text)
    return int(match.group(1)) if match else None


def _extract_min_slides(text: str) -> int | None:
    for pattern in (
        r"\u6570\u91cf\u5728\s*(\d+)\s*\u4ee5\u4e0a",
        r"\u81f3\u5c11\s*(\d+)\s*\u9875",
        r"\u4e0d\u5c11\u4e8e\s*(\d+)\s*\u9875",
        r"(\d+)\s*\+\s*\u9875",
        r"(\d+)\s*\u9875\s*\u4ee5\u4e0a",
        r"at least\s+(\d+)\s*slides?",
        r"(\d+)\s*slides?\s*or more",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _extract_slide_count(text: str) -> int | None:
    for pattern in (r"(\d+)\s*\u9875", r"(\d+)\s*\u5f20", r"(\d+)\s*slides?"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _extract_audience(text: str) -> str | None:
    normalized = text.lower()
    compact = re.sub(r"\s+", "", text)
    if any(
        token in normalized or token in compact
        for token in (
            "\u7814\u7a76\u751f\u8bba\u6587\u8bb2\u89e3",
            "\u7ed9\u7814\u7a76\u751f\u8bb2",
            "\u9762\u5411\u7814\u7a76\u751f",
            "\u7814\u7a76\u751f\u8bfe\u7a0b",
            "\u7814\u7a76\u751f\u6559\u5b66",
            "graduate-level",
            "graduate students",
        )
    ):
        return "\u7814\u7a76\u751f"
    for pattern in (
        r"\u53d7\u4f17\u662f?\s*([^,，。；;\s]+)",
        r"\u9762\u5411\s*([^,，。；;\s]+)",
        r"\u7ed9\s*([^,，。；;\s]+)\u8bb2",
        r"audience\s+is\s+([^,.;]+)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_topic(text: str) -> str | None:
    normalized = text.lower()
    if any(token in text for token in ("\u8bba\u6587\u8bb2\u89e3", "\u8bba\u6587\u4ecb\u7ecd")) or any(
        token in normalized for token in ("paper explanation", "teaching deck")
    ):
        return "\u8bba\u6587\u8bb2\u89e3"
    for pattern in (
        r"\u4e3b\u9898\u662f\s*([^,，。；;]+)",
        r"topic\s+is\s+([^,.;]+)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    stripped = text.strip()
    if stripped in TOPIC_PHRASES:
        return stripped
    if any(phrase in stripped for phrase in TOPIC_PHRASES):
        for phrase in TOPIC_PHRASES:
            if phrase in stripped:
                return phrase
    return None


def _normalize_pdf_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return "".join(char for char in normalized if char.isalnum())


def _available_pdf_names(pdfs: list[dict]) -> str:
    return ", ".join(item["name"] for item in pdfs) if pdfs else "none"
