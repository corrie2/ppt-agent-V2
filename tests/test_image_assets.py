from pathlib import Path
import zipfile

from ppt_agent.domain.models import PptSpec, SlideSpec
from ppt_agent.nodes.asset_resolve import asset_resolve_node
from ppt_agent.runtime.image_assets import ImageAssetError
from ppt_agent.runtime.pptx import build_pptx


def test_asset_resolve_success_writes_real_image_asset(monkeypatch):
    def fake_resolve_image_asset(*, query: str, prompt: str = "", cache_dir=None, provider=None):
        return type(
            "Asset",
            (),
            {
                "local_path": "C:/tmp/example.jpg",
                "source_url": "https://example.com/image",
                "source_name": "Mock Source",
                "license_note": "CC BY",
                "match_reason": "Matched the slide topic.",
            },
        )()

    monkeypatch.setattr("ppt_agent.nodes.asset_resolve.resolve_image_asset", fake_resolve_image_asset)
    spec = PptSpec(
        title="Deck",
        audience="leadership",
        slides=[
            SlideSpec(
                title="Hero",
                bullets=["Business context"],
                visual_type="hero_image",
                image_query="executive sales dashboard",
                visual_spec={"asset_kind": "image", "visual_required": True},
            )
        ],
    )

    result = asset_resolve_node({"spec": spec.model_dump(mode="json")})
    slide = result["spec"]["slides"][0]

    assert slide["resolved_asset"]["type"] == "image_file"
    assert slide["resolved_asset"]["local_path"] == "C:/tmp/example.jpg"
    assert result["asset_warnings"] == []


def test_asset_resolve_failure_keeps_placeholder_and_warning(monkeypatch):
    def fake_resolve_image_asset(*, query: str, prompt: str = "", cache_dir=None, provider=None):
        raise ImageAssetError("mock search failure")

    monkeypatch.setattr("ppt_agent.nodes.asset_resolve.resolve_image_asset", fake_resolve_image_asset)
    spec = PptSpec(
        title="Deck",
        audience="leadership",
        slides=[
            SlideSpec(
                title="Hero",
                bullets=["Business context"],
                visual_type="hero_image",
                image_query="executive sales dashboard",
                visual_spec={"asset_kind": "image", "visual_required": True},
            )
        ],
    )

    result = asset_resolve_node({"spec": spec.model_dump(mode="json")})
    slide = result["spec"]["slides"][0]

    assert slide["resolved_asset"]["type"] == "image_placeholder"
    assert slide["resolved_asset"]["fallback_reason"] == "image_search_failed"
    assert result["asset_warnings"]


def test_build_uses_real_picture_path(monkeypatch, tmp_path):
    seen: list[str] = []

    def fake_add_picture(slide, image_path: str, left, top, width, height, *, caption: str = ""):
        seen.append(image_path)

    monkeypatch.setattr("ppt_agent.runtime.pptx._add_picture", fake_add_picture)
    image_path = tmp_path / "asset.jpg"
    image_path.write_bytes(b"fake-image")
    spec = PptSpec(
        title="Deck",
        audience="leadership",
        slides=[
            SlideSpec(
                title="Hero",
                bullets=["Business context"],
                visual_type="hero_image",
                layout_hint="hero_image_plus_argument",
                resolved_asset={"local_path": str(image_path), "type": "image_file"},
            )
        ],
    )

    build_pptx(spec, tmp_path / "deck.pptx")

    assert seen == [str(image_path)]


def test_build_fallback_does_not_render_visual_area_or_raw_image_query(tmp_path):
    output = tmp_path / "deck.pptx"
    spec = PptSpec(
        title="Deck",
        audience="leadership",
        slides=[
            SlideSpec(
                title="Hero",
                bullets=["Business context"],
                core_message="Clean business message",
                visual_type="hero_image",
                layout_hint="hero_image_plus_argument",
                image_query="business sieve funnel filter concept",
                image_prompt="five step process diagram sieve filter funnel stages",
                resolved_asset={"type": "image_placeholder", "status": "planned"},
            )
        ],
    )

    build_pptx(spec, output)

    with zipfile.ZipFile(output) as archive:
        xml = archive.read("ppt/slides/slide1.xml").decode("utf-8", errors="ignore")

    assert "VISUAL AREA" not in xml
    assert "business sieve funnel filter concept" not in xml
    assert "five step process diagram sieve filter funnel stages" not in xml
    assert "Clean business message" in xml
