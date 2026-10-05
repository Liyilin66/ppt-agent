"""Tests for the deterministic typesetter behind image-derived slides."""

from __future__ import annotations

from pathlib import Path

import pytest

from ppt_agent.v2.design import get_builtin_theme
from ppt_agent.v2.image_layout import (
    CropBox,
    ImageSlideContent,
    SlideCallout,
    SlideFact,
    SlideSection,
    build_slide,
    refine_crop,
)
from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH, IconItem, ImageItem, TextItem
from ppt_agent.v2.metrics import estimated_overflow_ratio, text_height_units
from ppt_agent.v2.qa import review_page


def _sections(count: int = 3) -> list[SlideSection]:
    return [
        SlideSection(
            heading=f"要点 {index + 1}",
            icon="layers",
            body="这是一段用于验证排版的正文，长度接近抽取步骤允许的上限，用来占满整行。",
        )
        for index in range(count)
    ]


def _content(**overrides: object) -> ImageSlideContent:
    payload: dict[str, object] = {
        "title": "示例主题",
        "kicker": "分类标签",
        "subtitle": "一句话说明这一页为什么重要。",
        "layout": "points",
        "sections": _sections(),
    }
    payload.update(overrides)
    return ImageSlideContent.model_validate(payload)


class TestLayoutSelection:
    def test_spec_cards_needs_facts(self) -> None:
        assert _content(layout="spec_cards").effective_layout() == "points"
        with_facts = _content(layout="spec_cards", facts=[SlideFact(value="900mg", label="含量")])
        assert with_facts.effective_layout() == "spec_cards"

    def test_compare_needs_two_panes(self) -> None:
        one_pane = _content(
            layout="compare", compare=[{"heading": "现状", "points": ["甲"]}]
        )
        assert one_pane.effective_layout() == "points"

    def test_flow_needs_at_least_two_steps(self) -> None:
        assert _content(layout="flow", sections=_sections(1)).effective_layout() == "points"
        assert _content(layout="flow").effective_layout() == "flow"

    def test_numbers_plus_sections_upgrade_to_spec_cards(self) -> None:
        content = _content(layout="points", facts=[SlideFact(value="42", label="项")])
        assert content.effective_layout() == "spec_cards"

    def test_flow_steps_are_numbered_in_order(self) -> None:
        page = build_slide(_content(layout="flow"), page_number=1)
        numbers = [
            item.text for item in page.elements
            if isinstance(item, TextItem) and item.id.startswith("sec_num_")
        ]
        assert numbers == ["1", "2", "3"]


class TestFramesFitTheirText:
    @pytest.mark.parametrize(
        "content",
        [
            _content(layout="points"),
            _content(layout="spec_cards", facts=[
                SlideFact(value="900mg", label="每粒 Omega-3"),
                SlideFact(value="150 粒", label="规格"),
            ]),
            _content(layout="flow", sections=_sections(4)),
            _content(layout="compare", compare=[
                {"heading": "现状", "icon": "target", "points": ["甲要点一", "甲要点二"]},
                {"heading": "改进后", "icon": "growth", "points": ["乙要点一", "乙要点二"]},
            ]),
        ],
        ids=["points", "spec_cards", "flow", "compare"],
    )
    def test_no_text_overflows_and_nothing_leaves_the_canvas(self, content) -> None:
        page = build_slide(content, page_number=1)
        for item in page.elements:
            frame = getattr(item, "frame", None)
            if frame is None:
                continue
            assert frame.x >= 0 and frame.y >= 0
            assert frame.x + frame.w <= CANVAS_WIDTH + 0.5
            assert frame.y + frame.h <= CANVAS_HEIGHT + 0.5
            if isinstance(item, TextItem):
                overflow = estimated_overflow_ratio(
                    item.text,
                    role=item.role,
                    size_pt=item.size_pt or 12.0,
                    frame_width_units=frame.w,
                    frame_height_units=frame.h,
                )
                assert overflow == 0.0, f"{item.id} overflows by {overflow:.0%}"

    def test_a_long_callout_shrinks_the_body_instead_of_clipping(self) -> None:
        page = build_slide(
            _content(callout=SlideCallout(
                heading="重要提醒",
                points=["第一条注意事项写得相当长，用来把提醒条撑高" * 2, "第二条", "第三条"],
            )),
            page_number=1,
        )
        callout = next(item for item in page.elements if item.id == "callout_bg")
        sections = [item for item in page.elements if item.id.startswith("sec_bg_")]
        assert sections, "sections should still be drawn"
        assert max(s.frame.y + s.frame.h for s in sections) <= callout.frame.y + 0.5

    def test_body_text_survives_the_crowded_page(self) -> None:
        """Headline numbers, three sections and a caveat band all at once — the
        arrangement that silently dropped every section body."""
        page = build_slide(
            _content(
                layout="spec_cards",
                facts=[
                    SlideFact(value="150粒", label="规格"),
                    SlideFact(value="900mg", label="每粒 Omega-3"),
                    SlideFact(value="540mg", label="EPA"),
                    SlideFact(value="360mg", label="DHA"),
                ],
                callout=SlideCallout(heading="重要提醒", points=["甲", "乙", "丙"]),
            ),
            page_number=1,
            image_src="subject.png",
        )
        bodies = [item.id for item in page.elements if item.id.startswith("sec_body_")]
        assert len(bodies) == 3, "every section should keep its body text"

    def test_a_fourth_section_is_dropped_before_body_text_is(self) -> None:
        """Three rows that say something beat four bare labels."""
        page = build_slide(
            _content(
                layout="spec_cards",
                sections=_sections(4),
                facts=[SlideFact(value=f"{n}", label="项") for n in range(4)],
                callout=SlideCallout(heading="提醒", points=["甲", "乙", "丙"]),
            ),
            page_number=1,
        )
        headings = [i.id for i in page.elements if i.id.startswith("sec_head_")]
        bodies = [i.id for i in page.elements if i.id.startswith("sec_body_")]
        assert len(headings) == 3
        assert len(bodies) == 3

    def test_qa_finds_nothing_to_repair(self) -> None:
        page = build_slide(
            _content(layout="spec_cards", facts=[SlideFact(value="96%", label="覆盖率")]),
            page_number=1,
        )
        _, result = review_page(page, get_builtin_theme("slate"))
        assert result.errors == []


class TestImageSlot:
    def test_image_is_drawn_when_supplied(self) -> None:
        page = build_slide(_content(), page_number=1, image_src="subject.png")
        images = [item for item in page.elements if isinstance(item, ImageItem)]
        assert [item.src for item in images] == ["subject.png"]

    def test_comparison_gives_way_so_the_image_survives(self) -> None:
        content = _content(layout="compare", compare=[
            {"heading": "左", "points": ["一"]}, {"heading": "右", "points": ["二"]},
        ])
        page = build_slide(content, page_number=1, image_src="subject.png")
        assert any(isinstance(item, ImageItem) for item in page.elements)

    def test_sections_move_aside_for_the_image(self) -> None:
        without = build_slide(_content(), page_number=1)
        with_image = build_slide(_content(), page_number=1, image_src="subject.png")
        left_without = next(i for i in without.elements if i.id == "sec_bg_0").frame.x
        left_with = next(i for i in with_image.elements if i.id == "sec_bg_0").frame.x
        assert left_with > left_without


class TestIconHandling:
    def test_off_catalog_icon_names_are_mapped(self) -> None:
        content = _content(sections=[
            SlideSection(heading="直播", icon="livestream", body="视频区"),
            SlideSection(heading="配送", icon="shipping", body="物流"),
        ])
        page = build_slide(content, page_number=1)
        names = [item.name for item in page.elements if isinstance(item, IconItem)]
        assert names == ["video", "truck"]

    def test_unmapped_icon_still_reports_once(self) -> None:
        content = _content(sections=[
            SlideSection(heading="未知", icon="zzz_not_an_icon", body="x"),
            SlideSection(heading="已知", icon="check", body="y"),
        ])
        page = build_slide(content, page_number=1)
        _, result = review_page(page, get_builtin_theme("slate"))
        codes = [issue.code for issue in result.issues]
        assert codes.count("unknown_icon") == 1


class TestCropRefinement:
    def _subject(self, size: tuple[int, int]) -> "object":
        """A photo-like patch: many colours, so it reads as a photograph."""
        from PIL import Image

        width, height = size
        patch = Image.new("RGB", size)
        patch.putdata([
            (60 + (x * 7 + y * 3) % 190, 40 + (x * 3) % 200, 30 + (y * 5) % 180)
            for y in range(height)
            for x in range(width)
        ])
        return patch

    def _photo_on_a_card(self, path: Path) -> Path:
        from PIL import Image

        canvas = Image.new("RGB", (600, 800), color=(255, 255, 255))
        canvas.paste(self._subject((120, 160)), (240, 300))
        canvas.save(path)
        return path

    def test_a_box_with_no_backdrop_to_isolate_against_is_refused(self, tmp_path: Path) -> None:
        """Unverifiable means refused: the model's word alone is not enough."""
        source = self._photo_on_a_card(tmp_path / "page.png")
        # Expanding this box still lands wholly inside the subject.
        box = CropBox(x=0.44, y=0.44, w=0.12, h=0.06)
        assert not refine_crop(source, box, tmp_path / "subject.png")
        assert not (tmp_path / "subject.png").exists()

    def test_a_degenerate_box_is_refused_rather_than_saved(self, tmp_path: Path) -> None:
        source = self._photo_on_a_card(tmp_path / "page.png")
        box = CropBox(x=0.5, y=0.5, w=0.001, h=0.001)
        assert not refine_crop(source, box, tmp_path / "tiny.png")
        assert not (tmp_path / "tiny.png").exists()

    def test_an_unreadable_source_is_refused(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"not an image")
        box = CropBox(x=0.1, y=0.1, w=0.5, h=0.5)
        assert not refine_crop(broken, box, tmp_path / "out.png")

    def test_a_box_on_busy_artwork_is_refused(self, tmp_path: Path) -> None:
        """The leak that mattered: a promo poster has no isolatable subject, so
        trusting the box pasted price banners onto the slide as pixels."""
        from PIL import Image

        Image.effect_noise((600, 800), 90).convert("RGB").save(tmp_path / "poster.png")
        box = CropBox(x=0.35, y=0.35, w=0.2, h=0.2)
        assert not refine_crop(tmp_path / "poster.png", box, tmp_path / "subject.png")
        assert not (tmp_path / "subject.png").exists()

    def test_a_slab_of_the_page_is_refused(self, tmp_path: Path) -> None:
        """A box this big means no subject was identified; refining is pointless."""
        source = self._photo_on_a_card(tmp_path / "page.png")
        box = CropBox(x=0.05, y=0.05, w=0.9, h=0.5)
        assert not refine_crop(source, box, tmp_path / "out.png")
        assert not (tmp_path / "out.png").exists()

    def test_an_extreme_aspect_ratio_is_refused(self, tmp_path: Path) -> None:
        source = self._photo_on_a_card(tmp_path / "page.png")
        box = CropBox(x=0.05, y=0.4, w=0.9, h=0.05)
        assert not refine_crop(source, box, tmp_path / "out.png")

    def test_a_subject_is_isolated_from_the_text_beside_it(self, tmp_path: Path) -> None:
        """The failure that mattered: a loose box swallowing the copy next to
        the product, which then reaches the slide as pixels."""
        from PIL import Image, ImageDraw

        canvas = Image.new("RGB", (600, 800), color=(255, 255, 255))
        # Subject on the left, lines of "text" on the right, same card.
        canvas.paste(self._subject((110, 150)), (60, 320))
        draw = ImageDraw.Draw(canvas)
        for row in range(6):
            for column in range(9):
                x, y = 230 + column * 22, 330 + row * 20
                draw.rectangle([x, y, x + 14, y + 11], fill=(30, 30, 30))
        source = tmp_path / "card.png"
        canvas.save(source)

        # A box loose enough to cover subject and text together.
        box = CropBox(x=0.05, y=0.37, w=0.75, h=0.24)
        destination = tmp_path / "subject.png"
        assert refine_crop(source, box, destination)

        with Image.open(destination) as cropped:
            # The subject alone, not the subject plus the paragraph.
            assert 110 <= cropped.width <= 145
            assert 150 <= cropped.height <= 190

    def test_a_box_aimed_at_text_is_refused(self, tmp_path: Path) -> None:
        """The regression that shipped a single glyph as the product photo."""
        from PIL import Image, ImageDraw

        canvas = Image.new("RGB", (600, 800), color=(255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        for row in range(4):
            for column in range(10):
                x, y = 80 + column * 46, 340 + row * 44
                draw.rectangle([x, y, x + 34, y + 34], fill=(20, 20, 20))
        source = tmp_path / "text.png"
        canvas.save(source)

        box = CropBox(x=0.15, y=0.42, w=0.5, h=0.16)
        assert not refine_crop(source, box, tmp_path / "out.png")
        assert not (tmp_path / "out.png").exists()


class TestVisualHierarchy:
    """Defects only visible once the PPTX was opened in PowerPoint."""

    def test_compare_headings_stay_below_the_page_title(self) -> None:
        content = _content(layout="compare", compare=[
            {"heading": "现状", "points": ["甲"]}, {"heading": "改进后", "points": ["乙"]},
        ])
        page = build_slide(content, page_number=1)
        texts = {item.id: item for item in page.elements if isinstance(item, TextItem)}
        title_pt = next(i.size_pt for i in page.elements
                        if isinstance(i, TextItem) and i.role == "title")
        assert texts["cmp_head_0"].size_pt < title_pt

    def test_section_text_is_centred_on_its_icon(self) -> None:
        """Tall rows must not pin text to the top while the icon sits centred."""
        page = build_slide(_content(sections=_sections(2)), page_number=1)
        by_id = {item.id: item for item in page.elements}
        icon = by_id["sec_icon_0"].frame
        head = by_id["sec_head_0"].frame
        body = by_id["sec_body_0"]
        block_top = head.y
        block_bottom = body.frame.y + text_height_units(
            body.text, body.size_pt, 1.3, body.frame.w
        )
        icon_mid = icon.y + icon.h / 2
        assert abs((block_top + block_bottom) / 2 - icon_mid) < 12
