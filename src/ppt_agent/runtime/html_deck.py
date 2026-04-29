from __future__ import annotations

import html
import re
from pathlib import Path

from ppt_agent.domain.models import PptSpec, SlideSpec


def build_html_deck(
    spec: PptSpec,
    output_path: Path,
    *,
    template_path: Path | None = None,
    theme: str | None = None,
    references: list[Path] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(_render_slide(slide, index, len(spec.slides)) for index, slide in enumerate(spec.slides, start=1))
    rendered = _apply_template(
        _read_template(template_path),
        title=spec.title,
        theme=theme or spec.theme,
        slides_html=body,
        references=references or [],
        slide_count=len(spec.slides),
    )
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def _read_template(template_path: Path | None) -> str:
    if template_path and template_path.exists() and template_path.is_file():
        return template_path.read_text(encoding="utf-8")
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{title}}</title>
  <style>
    :root { color-scheme: light; font-family: Inter, "Noto Sans SC", Arial, sans-serif; }
    body { margin: 0; background: #f4f1eb; color: #171717; overflow: hidden; }
    .deck { height: 100vh; display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scroll-behavior: smooth; }
    .slide { scroll-snap-align: start; flex: 0 0 100vw; height: 100vh; box-sizing: border-box; padding: 5vw; display: grid; grid-template-columns: 1.1fr .9fr; gap: 4vw; align-items: center; position: relative; }
    .slide::after { content: attr(data-page); position: absolute; right: 3vw; bottom: 3vh; color: #7a7064; font-size: 14px; }
    .slide.cover, .slide.section-divider { grid-template-columns: 1fr; align-content: center; }
    h1 { font-size: 58px; line-height: 1.02; margin: 0 0 24px; max-width: 1100px; }
    h2 { font-size: 42px; line-height: 1.08; margin: 0 0 18px; }
    .message { font-size: 23px; line-height: 1.35; color: #38332e; }
    ul { margin: 28px 0 0; padding-left: 22px; font-size: 21px; line-height: 1.45; }
    .visual { min-height: 360px; background: #151515; color: #f9f4ea; display: grid; place-items: center; padding: 40px; box-sizing: border-box; }
    .visual-inner { border: 1px solid rgba(255,255,255,.3); width: 100%; height: 100%; display: grid; place-items: center; text-align: center; font-size: 26px; }
    .big-number .visual-inner { font-size: 96px; font-weight: 800; }
    .quote .message { font-size: 34px; font-family: Georgia, serif; }
    .comparison .visual-inner, .image-grid-fallback .visual-inner { background: repeating-linear-gradient(135deg, #222, #222 16px, #303030 16px, #303030 32px); }
    body.magazine .slide { background: #f7f0e2; }
    body.magazine .slide:nth-child(2n) { background: #e9eef0; }
  </style>
</head>
<body class="{{theme}}">
  <main class="deck" id="deck">
    {{slides}}
  </main>
  <script>
    document.addEventListener('keydown', (event) => {
      const deck = document.getElementById('deck');
      if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') deck.scrollBy({ left: window.innerWidth, behavior: 'smooth' });
      if (event.key === 'ArrowLeft' || event.key === 'PageUp') deck.scrollBy({ left: -window.innerWidth, behavior: 'smooth' });
      if (event.key === 'Home') deck.scrollTo({ left: 0, behavior: 'smooth' });
      if (event.key === 'End') deck.scrollTo({ left: deck.scrollWidth, behavior: 'smooth' });
    });
  </script>
</body>
</html>
"""


def _apply_template(template: str, *, title: str, theme: str, slides_html: str, references: list[Path], slide_count: int) -> str:
    rendered = template
    replacements = {
        "{{title}}": html.escape(title),
        "{{deck_title}}": html.escape(title),
        "{{theme}}": html.escape(theme),
        "{{theme_class}}": html.escape(theme),
        "<!-- SLIDES_HERE -->": slides_html,
        "{{slides}}": slides_html,
        "{{slides_html}}": slides_html,
        "{{slide_count}}": str(slide_count),
        "{{references}}": "\n".join(html.escape(path.read_text(encoding="utf-8")[:2000]) for path in references if path.exists()),
    }
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    rendered = _replace_spaced_template_variables(rendered, title=title, theme=theme, slides_html=slides_html, slide_count=slide_count)
    rendered = _ensure_html_title(rendered, title)
    if slides_html not in rendered:
        rendered = _inject_slides_into_existing_deck(rendered, slides_html)
    if slides_html not in rendered:
        rendered = rendered.replace("</body>", f"<main class=\"deck\">{slides_html}</main></body>")
    rendered = _ensure_deck_container(rendered)
    rendered = _ensure_runtime_assets(rendered)
    return rendered


def _replace_spaced_template_variables(rendered: str, *, title: str, theme: str, slides_html: str, slide_count: int) -> str:
    replacements = {
        "title": html.escape(title),
        "deck_title": html.escape(title),
        "theme": html.escape(theme),
        "theme_class": html.escape(theme),
        "slides": slides_html,
        "slides_html": slides_html,
        "slide_count": str(slide_count),
    }
    for name, value in replacements.items():
        rendered = re.sub(r"{{\s*" + re.escape(name) + r"\s*}}", lambda match, replacement=value: replacement, rendered)
    return rendered


def _ensure_html_title(rendered: str, title: str) -> str:
    escaped_title = html.escape(title)
    title_pattern = re.compile(r"<title\b[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
    match = title_pattern.search(rendered)
    if not match:
        return rendered.replace("</head>", f"<title>{escaped_title}</title>\n</head>") if "</head>" in rendered else rendered
    current = html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())
    placeholder_tokens = ("{{", "}}", "[必填]", "Deck Title", "PPT 标题")
    if current == title or not any(token in current for token in placeholder_tokens):
        return rendered
    return title_pattern.sub(lambda match: f"<title>{escaped_title}</title>", rendered, count=1)


def _render_slide(slide: SlideSpec, index: int, total: int) -> str:
    layout = _layout_class(slide)
    title_tag = "h1" if index == 1 or layout in {"cover", "section-divider"} else "h2"
    bullets = "".join(f"<li>{html.escape(item)}</li>" for item in slide.bullets)
    visual_label = _visual_label(slide)
    page = f"{index} / {total}"
    return f"""<section class="slide {layout}" data-slide-index="{index}" data-page="{html.escape(page)}">
  <div class="content">
    <{title_tag}>{html.escape(slide.title)}</{title_tag}>
    <p class="objective">{html.escape(slide.objective)}</p>
    <p class="message">{html.escape(slide.core_message or slide.objective)}</p>
    <ul>{bullets}</ul>
  </div>
  <div class="visual"><div class="visual-inner">{visual_label}</div></div>
  <div class="page-indicator">{html.escape(page)}</div>
</section>"""


def _layout_class(slide: SlideSpec) -> str:
    value = (slide.layout_hint or slide.visual_type or "").lower()
    if "cover" in value or "hero" in value:
        return "cover"
    if "section" in value or "divider" in value:
        return "section-divider"
    if "number" in value or "metric" in value:
        return "big-number"
    if "quote" in value:
        return "quote"
    if "comparison" in value or "table" in value:
        return "comparison"
    if "grid" in value:
        return "image-grid-fallback"
    return "text-visual"


def _visual_label(slide: SlideSpec) -> str:
    caption = slide.image_caption or slide.objective or slide.title
    return f"<span>{html.escape(caption)}</span>"


def _ensure_deck_container(rendered: str) -> str:
    if re.search(r"\bid\s*=\s*['\"]deck['\"]", rendered, flags=re.IGNORECASE):
        return rendered
    if 'class="deck"' in rendered or "class='deck'" in rendered:
        return rendered
    return re.sub(r"<main(?![^>]*class=)", '<main class="deck" id="deck"', rendered, count=1, flags=re.IGNORECASE)


def _inject_slides_into_existing_deck(rendered: str, slides_html: str) -> str:
    pattern = re.compile(
        r"(<(?P<tag>[a-zA-Z][\w:-]*)\b(?=[^>]*\bid\s*=\s*['\"]deck['\"])[^>]*>)(?P<body>.*?)(</(?P=tag)>)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(lambda match: f"{match.group(1)}{slides_html}{match.group(4)}", rendered, count=1)


def _ensure_runtime_assets(rendered: str) -> str:
    style = """
<style data-ppt-agent-html-deck>
.deck { height: 100vh; display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scroll-behavior: smooth; }
.slide { scroll-snap-align: start; flex: 0 0 100vw; min-width: 100vw; height: 100vh; box-sizing: border-box; position: relative; }
.page-indicator { position: absolute; right: 3vw; bottom: 3vh; font-size: 14px; color: #6f685f; }
</style>"""
    script = """
<script data-ppt-agent-html-deck>
document.addEventListener('keydown', (event) => {
  const deck = document.getElementById('deck') || document.querySelector('.deck');
  if (!deck) return;
  if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') deck.scrollBy({ left: window.innerWidth, behavior: 'smooth' });
  if (event.key === 'ArrowLeft' || event.key === 'PageUp') deck.scrollBy({ left: -window.innerWidth, behavior: 'smooth' });
  if (event.key === 'Home') deck.scrollTo({ left: 0, behavior: 'smooth' });
  if (event.key === 'End') deck.scrollTo({ left: deck.scrollWidth, behavior: 'smooth' });
});
</script>"""
    if "data-ppt-agent-html-deck" not in rendered:
        rendered = rendered.replace("</head>", f"{style}\n</head>") if "</head>" in rendered else style + rendered
        rendered = rendered.replace("</body>", f"{script}\n</body>") if "</body>" in rendered else rendered + script
    return rendered


def validate_html_deck(html_text: str, *, expected_slides: int, requested_min_slides: int | None = None) -> list[str]:
    errors: list[str] = []
    section_count = len(re.findall(r"<section\b[^>]*\bclass=[\"'][^\"']*\bslide\b", html_text, flags=re.IGNORECASE))
    if section_count < expected_slides:
        errors.append(f"HTML deck has {section_count} slide sections; expected at least {expected_slides}.")
    if requested_min_slides and section_count < requested_min_slides:
        errors.append(f"HTML deck has {section_count} slide sections; requested minimum is {requested_min_slides}.")
    forbidden = ("VISUAL AREA", "image_query", "image_prompt", "Supporting Appendix", '{"type":', '{ "type":')
    for token in forbidden:
        if token in html_text:
            errors.append(f"HTML deck contains forbidden raw content: {token}")
    return errors
