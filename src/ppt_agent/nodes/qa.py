from __future__ import annotations

import re
from typing import Any

from ppt_agent.domain.models import DeckIntent, PptSpec, QaIssue
from ppt_agent.utils.state import append_transition, state_get

PLACEHOLDER_PHRASES = {
    "context and objective",
    "primary recommendation",
    "near-term actions",
    "next steps",
    "key message",
}


def qa_node(state: dict[str, Any]) -> dict[str, Any]:
    spec = PptSpec.model_validate(state_get(state, "spec"))
    intent = _intent_from_state(state)
    issues: list[QaIssue] = []

    if len(spec.slides) < 6:
        issues.append(QaIssue(code="too_few_slides", message="Deck should contain at least six slides for a formal business presentation."))

    visual_pages = 0
    homogenous_bullet_pages = 0

    for index, slide in enumerate(spec.slides, start=1):
        if not slide.title.strip():
            issues.append(QaIssue(code="missing_title", message=f"Slide {index} has no title."))
        if not slide.bullets:
            issues.append(QaIssue(code="empty_slide", message=f"Slide {index} has no bullets."))
        if not slide.visual_type:
            issues.append(QaIssue(code="missing_visual_type", message=f"Slide {index} is missing visual_type."))
        if len(slide.core_message.strip()) < 20:
            issues.append(QaIssue(code="weak_core_message", message=f"Slide {index} needs a stronger core_message."))
        if len(slide.objective.strip()) < 12:
            issues.append(QaIssue(code="weak_objective", message=f"Slide {index} needs a clearer objective.", severity="warning"))
        if slide.visual_type and slide.visual_type in {"hero_image", "market_scene", "workspace_photo", "customer_moment"}:
            if not slide.image_query and not slide.image_prompt:
                issues.append(QaIssue(code="missing_image_brief", message=f"Slide {index} needs image_query or image_prompt."))
            if slide.resolved_asset.get("type") != "image_file":
                issues.append(QaIssue(code="missing_real_image", message=f"Slide {index} has no resolved real image asset.", severity="warning"))
                if not slide.resolved_asset.get("warning") and not slide.resolved_asset.get("fallback_reason"):
                    issues.append(QaIssue(code="missing_image_degrade_note", message=f"Slide {index} is missing an acceptable image fallback note.", severity="warning"))
        if slide.visual_spec or slide.resolved_asset or slide.visual_type:
            visual_pages += 1
        if len(slide.bullets) <= 3 and not slide.supporting_points:
            issues.append(QaIssue(code="thin_content", message=f"Slide {index} is too thin for a business deck."))
        if all(len(item.split()) <= 3 for item in slide.bullets[:3]):
            homogenous_bullet_pages += 1
        if slide.title.strip().lower() in PLACEHOLDER_PHRASES:
            issues.append(QaIssue(code="placeholder_title", message=f"Slide {index} title still looks like placeholder copy.", severity="warning"))
        if "visual_required" in slide.visual_spec and slide.visual_spec["visual_required"] and not slide.resolved_asset:
            issues.append(QaIssue(code="missing_visual_region", message=f"Slide {index} needs a resolved visual area."))

    if visual_pages == 0:
        issues.append(QaIssue(code="no_visual_pages", message="Deck has no visual pages; add image or diagram-driven layouts."))
    if homogenous_bullet_pages >= 3:
        issues.append(QaIssue(code="too_many_plain_bullet_pages", message="Deck still looks too uniform and text-heavy.", severity="warning"))

    issues.extend(_soft_qa(spec))
    issues.extend(_academic_grounding_qa(spec))
    issues.extend(_guizang_preference_qa(spec, intent=intent))
    return {
        "qa_issues": [issue.model_dump(mode="json") for issue in issues],
        "transitions": append_transition(state, "qa"),
    }


def _soft_qa(spec: PptSpec) -> list[QaIssue]:
    issues: list[QaIssue] = []
    conclusion_titles = 0
    for index, slide in enumerate(spec.slides, start=1):
        if any(token in slide.title.lower() for token in {"should", "must", "can", "will", "improves", "reduces", "accelerates"}):
            conclusion_titles += 1
        if len(slide.supporting_points) < 2:
            issues.append(QaIssue(code="soft_low_business_detail", message=f"Slide {index} could use more business detail or evidence.", severity="warning"))
        if slide.visual_type in {"hero_image", "workspace_photo"} and not slide.image_rationale:
            issues.append(QaIssue(code="soft_weak_visual_match", message=f"Slide {index} should explain why its visual matches the argument.", severity="warning"))
    if conclusion_titles == 0:
        issues.append(QaIssue(code="soft_titles_not_conclusion_led", message="Titles are not conclusion-led enough for an executive deck.", severity="warning"))
    return issues


def _academic_grounding_qa(spec: PptSpec) -> list[QaIssue]:
    if not _is_academic_deck(spec):
        return []
    issues: list[QaIssue] = []
    digest_text = str(spec.source_digest or "").lower()
    suspicious = ("roi", "customer case", "github star", "citation count", "neurips", "icml", "vldb", "客户案例", "引用量")
    seen_bodies: set[str] = set()
    not_provided_occurrences = 0
    for index, slide in enumerate(spec.slides, start=1):
        text = " ".join([slide.title, slide.core_message, *slide.bullets, *slide.supporting_points]).lower()
        if not slide.evidence_refs:
            issues.append(QaIssue(code="missing_evidence_refs", message=f"Academic slide {index} has no evidence_refs."))
        for token in suspicious:
            if token in text and token.lower() not in digest_text:
                issues.append(QaIssue(code="ungrounded_academic_claim", message=f"Slide {index} contains ungrounded academic/business claim: {token}"))
        if _contains_unsupported_specific_conclusion(text, digest_text):
            issues.append(
                QaIssue(
                    code="unsupported_specific_conclusion",
                    message=f"Slide {index} appears to make a specific conclusion that is not present in the source digest.",
                )
            )
        if re.match(r"^研究补充\s*\d+$", slide.title.strip()):
            issues.append(QaIssue(code="research_supplement_placeholder", message=f"Slide {index} still uses placeholder title: {slide.title}."))
        not_provided_occurrences += text.count("论文未提供") + text.count("not provided by source")
        if slide.title.lower().startswith("supporting appendix") or "adds supporting context" in text or "keeps the main deck aligned" in text:
            issues.append(QaIssue(code="placeholder_appendix", message=f"Slide {index} looks like a repeated Supporting Appendix placeholder."))
        body = "|".join([slide.core_message, *slide.bullets]).strip().lower()
        if body in seen_bodies:
            issues.append(QaIssue(code="duplicate_slide_content", message=f"Slide {index} duplicates earlier slide content."))
        seen_bodies.add(body)
    if not_provided_occurrences >= max(4, len(spec.slides) // 4):
        issues.append(
            QaIssue(
                code="too_many_not_provided_markers",
                message="Academic deck uses too many 'not provided by source' / '论文未提供' placeholders.",
                severity="warning",
            )
        )
    if spec.audience in {"general business audience", ""}:
        issues.append(QaIssue(code="audience_overwritten", message="Academic deck audience was overwritten by the default business audience."))
    return issues


def _guizang_preference_qa(spec: PptSpec, *, intent: DeckIntent | None) -> list[QaIssue]:
    if not _is_guizang_deck(spec):
        return []
    preference_text = _preference_text(intent)
    check_empty_boxes = not preference_text or any(token in preference_text for token in ("空方框", "空框", "empty box", "placeholder"))
    check_text_density = not preference_text or any(token in preference_text for token in ("正文", "文字太多", "too much text", "text-heavy"))
    issues: list[QaIssue] = []

    previous_layout = ""
    run_start = 0
    run_length = 0
    for index, slide in enumerate(spec.slides, start=1):
        if check_empty_boxes and _looks_like_empty_box(slide):
            issues.append(QaIssue(code="guizang_empty_box_preference_violation", message=f"Slide {index} appears to leave an empty visual box."))
        if check_text_density and _is_text_dense(slide):
            issues.append(QaIssue(code="guizang_text_too_dense", message=f"Slide {index} has too much body text for a guizang-style deck.", severity="warning"))

        layout = slide.layout_hint or slide.visual_type or "unknown"
        if layout == previous_layout:
            run_length += 1
        else:
            previous_layout = layout
            run_start = index
            run_length = 1
        if run_length == 3:
            issues.append(
                QaIssue(
                    code="guizang_consecutive_homogenous_layout",
                    message=f"Slides {run_start}-{index} repeat the same layout '{layout}' three times in a row.",
                    severity="warning",
                )
            )
    return issues


def _contains_unsupported_specific_conclusion(text: str, digest_text: str) -> bool:
    patterns = (
        r"\b\d+(?:\.\d+)?\s*%",
        r"\b\d+(?:\.\d+)?\s*x\b",
        r"\b(?:reduces|reduced|improves|improved|outperforms|outperformed|proves|significant)\b",
        r"(提升|降低|减少|优于|证明|显著|达到|超过)\s*\d*",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group(0).lower() not in digest_text:
            return True
    return False


def _looks_like_empty_box(slide) -> bool:
    payload = " ".join(
        [
            str(slide.visual_type),
            str(slide.layout_hint),
            str(slide.visual_spec),
            str(slide.resolved_asset),
            " ".join(slide.style_tags),
        ]
    ).lower()
    if any(token in payload for token in ("empty_box", "empty box", "空方框", "空框")):
        return True
    if slide.resolved_asset.get("type") in {"image_placeholder", "placeholder"}:
        return True
    return bool(slide.visual_spec.get("visual_required") and not slide.resolved_asset)


def _is_text_dense(slide) -> bool:
    body_parts = [slide.core_message, *slide.bullets, *slide.supporting_points, slide.speaker_notes]
    body = " ".join(part for part in body_parts if part)
    cjk_chars = sum(1 for char in body if "\u4e00" <= char <= "\u9fff")
    word_count = len(body.split())
    return len(slide.bullets) > 5 or len(slide.supporting_points) > 4 or cjk_chars > 260 or word_count > 95


def _is_academic_deck(spec: PptSpec) -> bool:
    audience = spec.audience.lower()
    return bool(spec.source_digest) or any(token in audience for token in ("graduate", "research", "academic")) or "研究生" in spec.audience


def _is_guizang_deck(spec: PptSpec) -> bool:
    return "guizang-ppt-skill" in spec.applied_skills or (spec.output_format == "html" and spec.theme in {"magazine", "editorial"})


def _intent_from_state(state: dict[str, Any]) -> DeckIntent | None:
    raw = state_get(state, "intent", None)
    if raw is None:
        return None
    try:
        return DeckIntent.model_validate(raw)
    except Exception:
        return None


def _preference_text(intent: DeckIntent | None) -> str:
    if not intent:
        return ""
    return " ".join(str(item.get("preference") or item) for item in intent.project_preferences).lower()
