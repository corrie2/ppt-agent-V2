from pathlib import Path

from ppt_agent.domain.models import DeckIntent, PptSpec, SlideSpec
from ppt_agent.nodes.qa import qa_node
from ppt_agent.runtime.html_deck import build_html_deck
from ppt_agent.runtime.planner import deterministic_plan_spec
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.storage.plan_io import read_plan_document, validate_plan_document, write_plan_document


def test_deterministic_planner_generates_visual_and_image_fields():
    spec = deterministic_plan_spec(DeckIntent(topic="AI Sales Enablement", audience="sales leadership"))

    assert len(spec.slides) >= 8
    assert all(slide.visual_type for slide in spec.slides)
    assert any(slide.image_query for slide in spec.slides)
    assert any(slide.layout_hint == "process_timeline" for slide in spec.slides)


def test_rich_schema_can_be_written_read_and_validated(tmp_path):
    spec = PptSpec(
        title="Executive Plan",
        audience="leadership",
        theme="executive_blue",
        slides=[
            SlideSpec(
                title="Executive Plan",
                objective="Frame the decision.",
                core_message="A focused pilot should start this quarter.",
                bullets=["State the business trigger.", "Explain the decision needed.", "Set the success threshold."],
                supporting_points=["Decision owner is explicit.", "Pilot scope is bounded."],
                visual_type="hero_image",
                image_query="executive team strategy session",
                layout_hint="title_cover",
                style_tags=["executive"],
                visual_spec={"visual_required": True, "asset_kind": "image"},
                resolved_asset={"type": "image_placeholder", "status": "planned"},
                evidence_refs=["source-1:abstract"],
                grounding_status="grounded",
                source_notes="Grounded in abstract.",
            )
        ],
    )
    path = tmp_path / "rich-plan.json"

    write_plan_document(
        path,
        intent=DeckIntent(topic="Executive Plan", audience="leadership"),
        spec=spec,
        mode="plan",
        approved=False,
        transitions=["plan", "asset_plan", "asset_resolve"],
    )

    document = read_plan_document(path)
    report = validate_plan_document(path)

    assert document.spec.theme == "executive_blue"
    assert document.spec.slides[0].visual_type == "hero_image"
    assert document.spec.slides[0].evidence_refs == ["source-1:abstract"]
    assert document.payload["slides"][0]["grounding_status"] == "grounded"
    assert document.payload["slides"][0]["source_notes"] == "Grounded in abstract."
    assert report.ok is True
    assert report.schema_version == 2
    assert report.format == "formal schema"


def test_build_routes_different_layouts(monkeypatch, tmp_path):
    seen_layouts: list[str] = []

    def fake_render(slide, slide_spec, layout):
        seen_layouts.append(layout)

    monkeypatch.setattr("ppt_agent.runtime.pptx._render_layout", fake_render)

    spec = PptSpec(
        title="Ops Review",
        audience="leadership",
        slides=[
            SlideSpec(title="Cover", bullets=["A", "B", "C"], visual_type="hero_image", layout_hint="title_cover"),
            SlideSpec(title="Roadmap", bullets=["A", "B", "C"], visual_type="process_timeline", layout_hint="process_timeline"),
            SlideSpec(title="Compare", bullets=["A", "B", "C"], visual_type="comparison_table", layout_hint="comparison_table"),
        ],
    )

    build_pptx(spec, tmp_path / "layouts.pptx")

    assert seen_layouts == ["title_cover", "process_timeline", "comparison_table"]


def test_build_html_deck_injects_slides_into_template_deck_container(tmp_path):
    template = tmp_path / "template.html"
    template.write_text(
        """<!doctype html>
<html>
<head><title>{{title}}</title></head>
<body>
  <div id="deck">
    <!-- SLIDES_HERE -->
  </div>
  <script>
    const deck = document.getElementById('deck');
    const slides = deck.querySelectorAll('.slide');
  </script>
</body>
</html>""",
        encoding="utf-8",
    )
    spec = PptSpec(
        title="HTML Deck",
        audience="leadership",
        slides=[
            SlideSpec(title="One", objective="First", core_message="Message one", bullets=["A"]),
            SlideSpec(title="Two", objective="Second", core_message="Message two", bullets=["B"]),
        ],
    )

    output = build_html_deck(spec, tmp_path / "deck.html", template_path=template)
    html = output.read_text(encoding="utf-8")
    deck_start = html.index('<div id="deck">')
    deck_end = html.index("</div>\n  <script>", deck_start)
    deck_html = html[deck_start:deck_end]

    assert "<!-- SLIDES_HERE -->" not in html
    assert '<main class="deck">' not in html
    assert deck_html.count('<section class="slide') == len(spec.slides)
    assert html.count('<section class="slide') == len(spec.slides)
    assert 'data-slide-index="1"' in deck_html
    assert 'data-slide-index="2"' in deck_html


def test_build_html_deck_keeps_slides_inside_guizang_style_deck_container(tmp_path):
    template = tmp_path / "template.html"
    template.write_text(
        """<!doctype html>
<html>
<head><title>{{title}}</title></head>
<body>
  <canvas id="bg-dark" class="bg"></canvas>
  <div id="deck">
    <!-- ============================================================
         SLIDES insert area
         ============================================================ -->
    <!-- SLIDES_HERE -->
  </div>
  <div id="nav"></div>
</body>
</html>""",
        encoding="utf-8",
    )
    spec = PptSpec(
        title="Guizang Style Deck",
        audience="leadership",
        slides=[
            SlideSpec(title="One", objective="First", core_message="Message one", bullets=["A"]),
            SlideSpec(title="Two", objective="Second", core_message="Message two", bullets=["B"]),
            SlideSpec(title="Three", objective="Third", core_message="Message three", bullets=["C"]),
        ],
    )

    output = build_html_deck(spec, tmp_path / "deck.html", template_path=template)
    html = output.read_text(encoding="utf-8")
    deck_start = html.index('<div id="deck">')
    deck_end = html.index('  <div id="nav">', deck_start)
    deck_html = html[deck_start:deck_end]

    assert "<!-- SLIDES_HERE -->" not in html
    assert '<main class="deck">' not in html
    assert deck_html.count('<section class="slide') == len(spec.slides)
    assert html.count('<section class="slide') == len(spec.slides)
    assert html.index('<section class="slide') < html.index('  <div id="nav">')


def test_build_html_deck_uses_existing_deck_container_without_placeholder(tmp_path):
    template = tmp_path / "template.html"
    template.write_text("<!doctype html><html><body><div id=\"deck\"></div></body></html>", encoding="utf-8")
    spec = PptSpec(
        title="HTML Deck",
        audience="leadership",
        slides=[SlideSpec(title="Only", objective="One", core_message="Message", bullets=["A"])],
    )

    output = build_html_deck(spec, tmp_path / "deck.html", template_path=template)
    html = output.read_text(encoding="utf-8")

    assert '<div id="deck"><section class="slide' in html
    assert '<main class="deck">' not in html
    assert html.count('<section class="slide') == 1


def test_build_html_deck_replaces_title_placeholders(tmp_path):
    spec = PptSpec(
        title="SIEVE Research Deck",
        audience="graduate students",
        slides=[SlideSpec(title="Only", objective="One", core_message="Message", bullets=["A"])],
    )

    spaced_template = tmp_path / "spaced-title.html"
    spaced_template.write_text(
        "<!doctype html><html><head><title>{{ title }}</title></head><body><div id=\"deck\"><!-- SLIDES_HERE --></div></body></html>",
        encoding="utf-8",
    )
    required_template = tmp_path / "required-title.html"
    required_template.write_text(
        "<!doctype html><html><head><title>[必填] 替换为 PPT 标题 · Deck Title</title></head><body><div id=\"deck\"><!-- SLIDES_HERE --></div></body></html>",
        encoding="utf-8",
    )

    spaced_html = build_html_deck(spec, tmp_path / "spaced.html", template_path=spaced_template).read_text(encoding="utf-8")
    required_html = build_html_deck(spec, tmp_path / "required.html", template_path=required_template).read_text(encoding="utf-8")

    for html in (spaced_html, required_html):
        assert "<title>SIEVE Research Deck</title>" in html
        assert "{{ title }}" not in html
        assert "[必填]" not in html
        assert "Deck Title" not in html


def test_qa_flags_text_only_placeholder_deck():
    slides = [
        SlideSpec(title="Key Message", bullets=["Context", "Actions"], core_message="Short"),
        SlideSpec(title="Next Steps", bullets=["Owners", "Metrics"], core_message="Short"),
        SlideSpec(title="Key Message", bullets=["Context", "Actions"], core_message="Short"),
        SlideSpec(title="Next Steps", bullets=["Owners", "Metrics"], core_message="Short"),
        SlideSpec(title="Key Message", bullets=["Context", "Actions"], core_message="Short"),
        SlideSpec(title="Next Steps", bullets=["Owners", "Metrics"], core_message="Short"),
    ]
    spec = PptSpec(title="Thin Deck", audience="leadership", slides=slides)

    result = qa_node({"spec": spec.model_dump(mode="json")})
    codes = {issue["code"] for issue in result["qa_issues"]}

    assert "no_visual_pages" in codes
    assert "too_many_plain_bullet_pages" in codes
    assert "placeholder_title" in codes
