"""Tests for image understanding & editable page reconstruction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pptx import Presentation

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.rebuild import RebuildItem, rebuild_deck
from ppt_agent.v2.revise import revise_deck


def _image(tmp_path: Path, name: str) -> Path:
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (640, 360), color=(40, 90, 150)).save(path)
    return path


class TestRebuildDeck:
    def test_multi_image_multi_route_rebuild(self, tmp_path: Path) -> None:
        items = [
            RebuildItem(image_path=str(_image(tmp_path, "slide_a.png")), route="rebuild"),
            RebuildItem(image_path=str(_image(tmp_path, "chart.png")), route="design_from_content"),
            RebuildItem(image_path=str(_image(tmp_path, "photo.png")), route="embed_with_notes"),
        ]
        result = rebuild_deck(
            items=items, output_dir=tmp_path / "out",
            client=MockLLMClient(), progress=lambda _: None,
        )
        assert result.page_count == 3
        assert result.rebuilt_pages == 3
        assert len(Presentation(result.pptx_path).slides) == 3
        design = json.loads(Path(result.deck_design_path).read_text(encoding="utf-8"))
        assert design["theme"]["name"] == "extracted-from-images"
        routes = [
            next(e["text"] for e in page["elements"] if e["id"].startswith("route_tag_"))
            for page in design["pages"]
        ]
        assert routes == ["route:rebuild", "route:design_from_content", "route:embed_with_notes"]

    def test_crop_regions_become_real_assets(self, tmp_path: Path) -> None:
        result = rebuild_deck(
            items=[RebuildItem(image_path=str(_image(tmp_path, "slide_b.png")), route="rebuild")],
            output_dir=tmp_path / "out",
            client=MockLLMClient(), progress=lambda _: None,
        )
        out = tmp_path / "out"
        crop = out / "assets" / "crop_p001_1.png"
        assert crop.is_file()
        from PIL import Image

        with Image.open(crop) as cropped:
            assert cropped.width < 640 and cropped.height < 360
        design = json.loads(Path(result.deck_design_path).read_text(encoding="utf-8"))
        srcs = [e["src"] for e in design["pages"][0]["elements"] if e["type"] == "image"]
        assert srcs == ["crop_p001_1.png"]

    def test_failed_reconstruction_falls_back_to_embedding_original(self, tmp_path: Path) -> None:
        class NoImagePageClient(MockLLMClient):
            async def complete_json(self, *, task: str, **kwargs: Any) -> Any:
                if task == "image_page":
                    raise RuntimeError("vision offline")
                return await super().complete_json(task=task, **kwargs)

        result = rebuild_deck(
            items=[RebuildItem(image_path=str(_image(tmp_path, "slide_c.png")), route="rebuild")],
            output_dir=tmp_path / "out",
            client=NoImagePageClient(), progress=lambda _: None,
        )
        assert result.fallback_pages == 1
        design = json.loads(Path(result.deck_design_path).read_text(encoding="utf-8"))
        page = design["pages"][0]
        assert any(
            e["type"] == "image" and e["src"] == "slide_c.png" for e in page["elements"]
        )
        assert any("原图保留" in e.get("text", "") for e in page["elements"] if e["type"] == "text")

    def test_rebuilt_deck_supports_revision_chat(self, tmp_path: Path) -> None:
        rebuild_deck(
            items=[
                RebuildItem(image_path=str(_image(tmp_path, "slide_d.png")), route="rebuild"),
                RebuildItem(image_path=str(_image(tmp_path, "notes.png")), route="extract_text"),
            ],
            output_dir=tmp_path / "out",
            client=MockLLMClient(), progress=lambda _: None,
        )
        result = revise_deck(
            output_dir=tmp_path / "out", deck_name="generated_long_deck_v2",
            message="第 1 页标题改大一点",
            client=MockLLMClient(), progress=lambda _: None,
        )
        assert result.revised_pages == [1]
        design = json.loads(
            (tmp_path / "out" / "generated_long_deck_v2_design.json").read_text(encoding="utf-8")
        )
        page1 = design["pages"][0]
        assert any(e.get("id") == "mock_revision_tag" for e in page1["elements"])
