from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches, Pt

from ppt_agent.domain.models import Artifact, PptSpec, SlideSpec

SLIDE_WIDTH = Inches(13.333)
SLIDE_HEIGHT = Inches(7.5)

NAVY = RGBColor(16, 37, 66)
BLUE = RGBColor(32, 91, 172)
TEAL = RGBColor(36, 124, 136)
LIGHT = RGBColor(244, 247, 251)
MID = RGBColor(221, 228, 237)
DARK = RGBColor(47, 58, 74)
WHITE = RGBColor(255, 255, 255)
GRAY = RGBColor(110, 122, 138)


def build_pptx(spec: PptSpec, output_path: Path) -> Artifact:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    for slide_spec in spec.slides:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _paint_background(slide, spec.theme)
        layout = _resolve_layout(slide_spec)
        _render_layout(slide, slide_spec, layout)
        if slide_spec.speaker_notes:
            slide.notes_slide.notes_text_frame.text = slide_spec.speaker_notes

    prs.save(output_path)
    return Artifact(path=output_path)


def _resolve_layout(slide_spec: SlideSpec) -> str:
    if slide_spec.layout_hint:
        return slide_spec.layout_hint
    mapping = {
        "hero_image": "title_cover",
        "market_scene": "hero_image_plus_argument",
        "workspace_photo": "two_column_text_image",
        "customer_moment": "hero_image_plus_argument",
        "three_card_summary": "three_card_summary",
        "process_timeline": "process_timeline",
        "comparison_table": "comparison_table",
    }
    return mapping.get(slide_spec.visual_type, "two_column_text_image")


def _render_layout(slide, slide_spec: SlideSpec, layout: str) -> None:
    renderer = {
        "title_cover": _render_title_cover,
        "hero_image_plus_argument": _render_hero_image_plus_argument,
        "two_column_text_image": _render_two_column_text_image,
        "three_card_summary": _render_three_card_summary,
        "process_timeline": _render_process_timeline,
        "comparison_table": _render_comparison_table,
    }.get(layout, _render_two_column_text_image)
    renderer(slide, slide_spec)


def _render_title_cover(slide, slide_spec: SlideSpec) -> None:
    _add_textbox(slide, Inches(0.8), Inches(0.7), Inches(6.2), Inches(0.8), slide_spec.title, 28, True, WHITE)
    _add_textbox(slide, Inches(0.8), Inches(1.7), Inches(5.8), Inches(1.2), slide_spec.core_message or slide_spec.objective, 18, False, WHITE)
    _add_textbox(slide, Inches(0.8), Inches(3.0), Inches(4.8), Inches(2.2), _join_lines(slide_spec.bullets[:3]), 15, False, WHITE)
    _render_visual_area(slide, slide_spec, Inches(7.0), Inches(0.7), Inches(5.1), Inches(5.5), accent=TEAL)


def _render_hero_image_plus_argument(slide, slide_spec: SlideSpec) -> None:
    _add_section_title(slide, slide_spec.title)
    _add_textbox(slide, Inches(0.8), Inches(1.5), Inches(4.7), Inches(0.8), slide_spec.core_message or slide_spec.objective, 22, True, NAVY)
    _add_textbox(slide, Inches(0.8), Inches(2.4), Inches(4.7), Inches(2.8), _join_lines(slide_spec.bullets[:4]), 16, False, DARK)
    _add_textbox(slide, Inches(0.8), Inches(5.4), Inches(4.7), Inches(1.0), _join_lines(slide_spec.supporting_points[:2]), 13, False, GRAY)
    _render_visual_area(slide, slide_spec, Inches(6.0), Inches(1.3), Inches(6.1), Inches(4.9), accent=BLUE)


def _render_two_column_text_image(slide, slide_spec: SlideSpec) -> None:
    _add_section_title(slide, slide_spec.title)
    _add_textbox(slide, Inches(0.8), Inches(1.4), Inches(5.1), Inches(0.7), slide_spec.objective or slide_spec.core_message, 17, True, BLUE)
    _add_textbox(slide, Inches(0.8), Inches(2.1), Inches(5.0), Inches(2.8), _join_lines(slide_spec.bullets[:4]), 15, False, DARK)
    _add_textbox(slide, Inches(0.8), Inches(5.0), Inches(5.0), Inches(1.2), _join_lines(slide_spec.supporting_points[:3]), 13, False, GRAY)
    _render_visual_area(slide, slide_spec, Inches(6.2), Inches(1.4), Inches(5.8), Inches(4.8), accent=TEAL)


def _render_three_card_summary(slide, slide_spec: SlideSpec) -> None:
    _add_section_title(slide, slide_spec.title)
    _add_textbox(slide, Inches(0.8), Inches(1.3), Inches(11.4), Inches(0.6), slide_spec.core_message or slide_spec.objective, 18, False, DARK)
    items = (slide_spec.supporting_points or slide_spec.bullets)[:3]
    while len(items) < 3:
        items.append("Add supporting business detail")
    lefts = [Inches(0.8), Inches(4.45), Inches(8.1)]
    for idx, item in enumerate(items):
        shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, lefts[idx], Inches(2.0), Inches(3.1), Inches(3.2))
        shape.fill.solid()
        shape.fill.fore_color.rgb = WHITE
        shape.line.color.rgb = MID
        _set_text(shape.text_frame, f"{idx + 1:02d}\n{item}", 18, True, NAVY)


def _render_process_timeline(slide, slide_spec: SlideSpec) -> None:
    _add_section_title(slide, slide_spec.title)
    _add_textbox(slide, Inches(0.8), Inches(1.3), Inches(11.2), Inches(0.7), slide_spec.core_message or slide_spec.objective, 17, False, DARK)
    steps = (slide_spec.supporting_points or slide_spec.bullets)[:4]
    while len(steps) < 4:
        steps.append("Execution checkpoint")
    start_left = Inches(1.0)
    top = Inches(3.5)
    width = Inches(2.6)
    for idx, step in enumerate(steps):
        left = start_left + Inches(3.0) * idx
        circle = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, left, Inches(2.4), Inches(0.6), Inches(0.6))
        circle.fill.solid()
        circle.fill.fore_color.rgb = BLUE
        circle.line.color.rgb = BLUE
        _set_text(circle.text_frame, str(idx + 1), 12, True, WHITE, center=True)
        box = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, left - Inches(0.2), top, width, Inches(1.4))
        box.fill.solid()
        box.fill.fore_color.rgb = WHITE
        box.line.color.rgb = MID
        _set_text(box.text_frame, step, 13, False, DARK)
        if idx < len(steps) - 1:
            connector = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.CHEVRON, left + Inches(0.8), Inches(2.55), Inches(1.6), Inches(0.3))
            connector.fill.solid()
            connector.fill.fore_color.rgb = TEAL
            connector.line.color.rgb = TEAL


def _render_comparison_table(slide, slide_spec: SlideSpec) -> None:
    _add_section_title(slide, slide_spec.title)
    _add_textbox(slide, Inches(0.8), Inches(1.3), Inches(11.2), Inches(0.7), slide_spec.core_message or slide_spec.objective, 17, False, DARK)
    rows = 4
    table = slide.shapes.add_table(rows, 3, Inches(0.8), Inches(2.0), Inches(11.4), Inches(3.5)).table
    headers = ["Dimension", "Current State", "Target State"]
    for col, value in enumerate(headers):
        cell = table.cell(0, col)
        cell.text = value
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY
        _style_cell(cell, True, WHITE)
    points = slide_spec.supporting_points or slide_spec.bullets
    rows_data = [
        ("Seller workflow", points[0] if len(points) > 0 else "Manual prep", slide_spec.bullets[0] if slide_spec.bullets else "AI-guided prep"),
        ("Manager visibility", points[1] if len(points) > 1 else "Lagging signals", slide_spec.bullets[1] if len(slide_spec.bullets) > 1 else "Weekly leading indicators"),
        ("Business impact", points[2] if len(points) > 2 else "Inconsistent execution", slide_spec.core_message or "Consistent value delivery"),
    ]
    for row_idx, row in enumerate(rows_data, start=1):
        for col_idx, value in enumerate(row):
            cell = table.cell(row_idx, col_idx)
            cell.text = value
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if row_idx % 2 else LIGHT
            _style_cell(cell, False, DARK)


def _paint_background(slide, theme: str) -> None:
    background = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT)
    background.fill.solid()
    background.fill.fore_color.rgb = LIGHT if theme != "midnight" else NAVY
    background.line.color.rgb = background.fill.fore_color.rgb
    slide.shapes._spTree.remove(background._element)
    slide.shapes._spTree.insert(2, background._element)


def _add_section_title(slide, title: str) -> None:
    _add_textbox(slide, Inches(0.8), Inches(0.45), Inches(11.4), Inches(0.6), title, 24, True, NAVY)


def _render_visual_area(slide, slide_spec: SlideSpec, left, top, width, height, *, accent: RGBColor) -> None:
    local_path = (slide_spec.resolved_asset or {}).get("local_path")
    if local_path and Path(local_path).exists():
        _add_picture(slide, local_path, left, top, width, height, caption=slide_spec.image_caption or slide_spec.core_message)
        return
    _draw_visual_placeholder(slide, slide_spec, left, top, width, height, accent=accent)


def _draw_visual_placeholder(slide, slide_spec: SlideSpec, left, top, width, height, *, accent: RGBColor) -> None:
    frame = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, left, top, width, height)
    frame.fill.solid()
    frame.fill.fore_color.rgb = WHITE
    frame.line.color.rgb = accent

    layout = _resolve_layout(slide_spec)
    if layout == "title_cover":
        _draw_cover_fallback(slide, slide_spec, left, top, width, height, accent=accent)
    elif layout == "hero_image_plus_argument":
        _draw_hero_fallback(slide, slide_spec, left, top, width, height, accent=accent)
    else:
        _draw_two_column_fallback(slide, slide_spec, left, top, width, height, accent=accent)


def _draw_cover_fallback(slide, slide_spec: SlideSpec, left, top, width, height, *, accent: RGBColor) -> None:
    band = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, left, top, width, Inches(1.0))
    band.fill.solid()
    band.fill.fore_color.rgb = accent
    band.line.color.rgb = accent

    circle = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, left + Inches(0.4), top + Inches(1.35), Inches(1.2), Inches(1.2))
    circle.fill.solid()
    circle.fill.fore_color.rgb = LIGHT
    circle.line.color.rgb = MID

    card = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
        left + Inches(1.9),
        top + Inches(1.2),
        width - Inches(2.4),
        height - Inches(1.7),
    )
    card.fill.solid()
    card.fill.fore_color.rgb = LIGHT
    card.line.color.rgb = MID
    label = slide_spec.image_caption or slide_spec.core_message or slide_spec.objective or slide_spec.title
    _set_text(card.text_frame, label, 20, True, NAVY)


def _draw_hero_fallback(slide, slide_spec: SlideSpec, left, top, width, height, *, accent: RGBColor) -> None:
    hero = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, left + Inches(0.3), top + Inches(0.3), width - Inches(0.6), height - Inches(0.6))
    hero.fill.solid()
    hero.fill.fore_color.rgb = LIGHT
    hero.line.color.rgb = MID

    for idx in range(3):
        bar = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.CHEVRON,
            left + Inches(0.6) + Inches(1.15) * idx,
            top + Inches(0.7),
            Inches(0.9),
            Inches(0.32),
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent if idx % 2 == 0 else TEAL
        bar.line.color.rgb = bar.fill.fore_color.rgb

    message = slide_spec.image_caption or slide_spec.core_message or slide_spec.objective or slide_spec.title
    textbox = slide.shapes.add_textbox(left + Inches(0.7), top + Inches(1.5), width - Inches(1.4), Inches(2.2))
    _set_text(textbox.text_frame, message, 18, True, NAVY)


def _draw_two_column_fallback(slide, slide_spec: SlideSpec, left, top, width, height, *, accent: RGBColor) -> None:
    panel = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, left + Inches(0.3), top + Inches(0.3), width - Inches(0.6), height - Inches(0.6))
    panel.fill.solid()
    panel.fill.fore_color.rgb = LIGHT
    panel.line.color.rgb = MID

    card_left = left + Inches(0.6)
    for idx in range(3):
        card = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
            card_left + Inches(1.55) * idx,
            top + Inches(0.75),
            Inches(1.15),
            Inches(2.5),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = WHITE
        card.line.color.rgb = accent if idx == 1 else MID
        _set_text(card.text_frame, f"{idx + 1:02d}", 18, True, accent if idx == 1 else NAVY, center=True)

    message = slide_spec.image_caption or slide_spec.core_message or slide_spec.objective or slide_spec.title
    caption = slide.shapes.add_textbox(left + Inches(0.7), top + Inches(3.6), width - Inches(1.4), Inches(1.0))
    _set_text(caption.text_frame, message, 16, False, DARK, center=True)


def _add_picture(slide, image_path: str, left, top, width, height, *, caption: str = "") -> None:
    slide.shapes.add_picture(image_path, left, top, width=width, height=height)
    if caption:
        caption_box = slide.shapes.add_textbox(left, top + height - Inches(0.45), width, Inches(0.4))
        fill = caption_box.fill
        fill.solid()
        fill.fore_color.rgb = NAVY
        _set_text(caption_box.text_frame, caption, 11, False, WHITE, center=True)


def _add_textbox(slide, left, top, width, height, text: str, size: int, bold: bool, color: RGBColor):
    textbox = slide.shapes.add_textbox(left, top, width, height)
    _set_text(textbox.text_frame, text, size, bold, color)
    return textbox


def _set_text(text_frame, text: str, size: int, bold: bool, color: RGBColor, *, center: bool = False) -> None:
    text_frame.clear()
    paragraph = text_frame.paragraphs[0]
    paragraph.text = text or " "
    if center:
        paragraph.alignment = 1
    run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = "Aptos"


def _style_cell(cell, bold: bool, color: RGBColor) -> None:
    paragraph = cell.text_frame.paragraphs[0]
    run = paragraph.runs[0]
    run.font.bold = bold
    run.font.size = Pt(12)
    run.font.color.rgb = color
    run.font.name = "Aptos"


def _join_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item)
