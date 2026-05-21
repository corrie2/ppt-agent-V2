from __future__ import annotations

import re
from dataclasses import dataclass

from ppt_agent.domain.models import PptSpec, SlideContent, SlideSpec


MESSAGE_WORD_LIMIT = 24
BULLET_WORD_LIMIT = 18
CAPTION_WORD_LIMIT = 18
MAX_BULLETS = 3


@dataclass(frozen=True)
class PolishPlanResult:
    spec: PptSpec
    slides_changed: int
    bullets_shortened: int
    notes_extended: int


def polish_plan_spec(spec: PptSpec) -> PolishPlanResult:
    slides: list[SlideSpec] = []
    slides_changed = 0
    bullets_shortened = 0
    notes_extended = 0

    for index, slide in enumerate(spec.slides):
        polished, stats = _polish_slide(slide, index=index, total=len(spec.slides))
        slides.append(polished)
        slides_changed += int(polished.model_dump(mode="json") != slide.model_dump(mode="json"))
        bullets_shortened += stats["bullets_shortened"]
        notes_extended += stats["notes_extended"]

    return PolishPlanResult(
        spec=spec.model_copy(update={"slides": slides}),
        slides_changed=slides_changed,
        bullets_shortened=bullets_shortened,
        notes_extended=notes_extended,
    )


def _polish_slide(slide: SlideSpec, *, index: int, total: int) -> tuple[SlideSpec, dict[str, int]]:
    stats = {"bullets_shortened": 0, "notes_extended": 0}
    original_message = slide.message or slide.core_message
    title = _polish_title(slide.title)
    message, moved_message = _shorten_text(original_message, word_limit=MESSAGE_WORD_LIMIT)
    if moved_message:
        stats["notes_extended"] += 1

    source_bullets = slide.bullets or slide.content.bullets
    bullets: list[str] = []
    moved_bullets: list[str] = []
    for bullet in source_bullets[:MAX_BULLETS]:
        short, overflow = _shorten_text(bullet, word_limit=BULLET_WORD_LIMIT)
        if short:
            bullets.append(short)
        if overflow:
            moved_bullets.append(bullet)
            stats["bullets_shortened"] += 1
    moved_bullets.extend(source_bullets[MAX_BULLETS:])

    speaker_notes = _extend_notes(
        slide.speaker_notes,
        original_message=original_message if moved_message else "",
        moved_bullets=moved_bullets,
    )
    if speaker_notes != slide.speaker_notes:
        stats["notes_extended"] += 1

    content_payload = slide.content.model_dump(mode="json")
    content_payload["bullets"] = bullets
    content_payload["visual_reason"] = _shorten_plain_sentence(content_payload.get("visual_reason", ""), word_limit=24)
    content_payload["callouts"] = _polish_callouts(content_payload.get("callouts", []))
    content_payload["result_summary"] = _polish_result_summary(content_payload.get("result_summary", []))

    layout = _presentation_layout(slide, index=index, total=total)
    caption = _shorten_plain_sentence(slide.image_caption, word_limit=CAPTION_WORD_LIMIT)

    payload = slide.model_dump(mode="json")
    payload.update(
        {
            "title": title,
            "message": message,
            "core_message": message,
            "layout": layout,
            "layout_hint": layout,
            "content": content_payload,
            "bullets": bullets,
            "speaker_notes": speaker_notes,
            "image_caption": caption,
        }
    )
    return SlideSpec.model_validate(payload), stats


def _polish_title(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.sub(r"^(the\s+)?(challenge|problem|motivation|summary|takeaways?)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    return _shorten_plain_sentence(cleaned, word_limit=9)


def _shorten_text(value: str, *, word_limit: int) -> tuple[str, bool]:
    cleaned = _clean_text(value)
    if not cleaned:
        return "", False
    sentences = _split_sentences(cleaned)
    candidate = sentences[0] if sentences else cleaned
    shortened = _shorten_plain_sentence(candidate, word_limit=word_limit)
    return shortened, _word_count(cleaned) > word_limit or shortened != cleaned


def _shorten_plain_sentence(value: str, *, word_limit: int) -> str:
    cleaned = _clean_text(value)
    words = cleaned.split()
    if len(words) <= word_limit:
        return cleaned
    return " ".join(words[:word_limit]).rstrip(" ,;:.") + "."


def _presentation_layout(slide: SlideSpec, *, index: int, total: int) -> str:
    existing = (slide.layout or slide.layout_hint or "").lower()
    figure_ids = slide.content.figure_ids
    table_ids = slide.content.table_ids
    role = slide.role.lower()
    visual_type = slide.visual_type.lower()
    if "title" in existing or "cover" in role or role == "title":
        return "title_cover"
    if table_ids or existing in {"table", "result_table_summary"} or visual_type == "table":
        return "result_cards"
    if figure_ids and ("method" in role or "diagram" in visual_type or "method" in slide.title.lower()):
        return "figure_walkthrough"
    if figure_ids or existing == "image" or visual_type in {"chart", "diagram", "figure"}:
        return "figure_walkthrough"
    if "method" in role or "construction" in slide.title.lower():
        return "method_step_flow"
    if index == total - 1 or "summary" in role or "takeaway" in slide.title.lower():
        return "result_cards"
    if len(slide.bullets or slide.content.bullets) >= 3:
        return "concept_explainer"
    return "concept_explainer"


def _polish_callouts(callouts: list[dict]) -> list[dict]:
    polished: list[dict] = []
    for index, callout in enumerate(callouts[:3], start=1):
        item = dict(callout)
        item["label"] = str(item.get("label") or index)
        item["text"] = _shorten_plain_sentence(str(item.get("text") or ""), word_limit=12)
        polished.append(item)
    return polished


def _polish_result_summary(items: list[dict]) -> list[dict]:
    polished: list[dict] = []
    for item in items[:3]:
        result = dict(item)
        result["finding"] = _shorten_plain_sentence(str(result.get("finding") or ""), word_limit=14)
        polished.append(result)
    return polished


def _extend_notes(notes: str, *, original_message: str, moved_bullets: list[str]) -> str:
    additions: list[str] = []
    if original_message:
        additions.append(f"Full message: {original_message}")
    if moved_bullets:
        additions.append("Detail for narration: " + " ".join(_clean_text(item) for item in moved_bullets if _clean_text(item)))
    if not additions:
        return notes
    existing = notes.strip()
    suffix = "\n\n".join(additions)
    return f"{existing}\n\n{suffix}" if existing else suffix


def _split_sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _word_count(value: str) -> int:
    return len(_clean_text(value).split())
