"""Tests for the v2 PageDesign IR and the design-token system."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ppt_agent.v2.design import (
    BUILTIN_THEMES,
    TYPE_SCALE,
    ThemePalette,
    best_text_color,
    contrast_ratio,
    get_builtin_theme,
    normalize_theme,
)
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ChartItem,
    DeckDesign,
    Frame,
    PageDesign,
    TableItem,
    TextItem,
    normalize_page_payload,
)


def _text(id_: str, x: float = 100, y: float = 100) -> dict:
    return {
        "type": "text",
        "id": id_,
        "frame": {"x": x, "y": y, "w": 200, "h": 50},
        "text": "hello",
    }


class TestFrame:
    def test_rejects_zero_width(self) -> None:
        with pytest.raises(ValidationError):
            Frame(x=0, y=0, w=0, h=10)

    def test_allows_moderate_bleed_for_anchor_decor(self) -> None:
        frame = Frame(x=-160, y=-120, w=460, h=460)
        assert frame.x == -160

    def test_iou_of_disjoint_frames_is_zero(self) -> None:
        a = Frame(x=0, y=0, w=100, h=100)
        b = Frame(x=200, y=200, w=100, h=100)
        assert a.intersection_over_union(b) == 0.0


class TestNormalizePagePayload:
    def test_clamps_out_of_canvas_frames(self) -> None:
        payload = {
            "role": "content",
            "elements": [
                {
                    "type": "text",
                    "id": "t1",
                    "frame": {"x": 1400, "y": -50, "w": 900, "h": 9000},
                    "text": "clamped",
                }
            ],
        }
        page = PageDesign.model_validate(normalize_page_payload(payload, page_number=3))
        frame = page.elements[0].frame
        assert page.page_number == 3
        assert 0 <= frame.x <= CANVAS_WIDTH
        assert frame.x + frame.w <= CANVAS_WIDTH
        assert frame.y + frame.h <= CANVAS_HEIGHT

    def test_drops_unknown_element_keys(self) -> None:
        payload = {
            "role": "content",
            "elements": [{**_text("t1"), "made_up_key": 1}],
        }
        page = PageDesign.model_validate(normalize_page_payload(payload, page_number=1))
        assert page.elements[0].id == "t1"

    def test_duplicate_ids_rejected(self) -> None:
        payload = {"role": "content", "elements": [_text("a"), _text("a", x=400)]}
        with pytest.raises(ValidationError, match="Duplicate element id"):
            PageDesign.model_validate(normalize_page_payload(payload, page_number=1))


class TestChartAndTable:
    def test_chart_series_length_must_match_categories(self) -> None:
        with pytest.raises(ValidationError, match="values"):
            ChartItem(
                id="c",
                frame=Frame(x=0, y=0, w=400, h=300),
                chart="column",
                categories=["a", "b", "c"],
                series=[{"name": "s", "values": [1, 2]}],
            )

    def test_table_rows_must_match_headers(self) -> None:
        with pytest.raises(ValidationError, match="cells"):
            TableItem(
                id="t",
                frame=Frame(x=0, y=0, w=400, h=300),
                headers=["a", "b"],
                rows=[["only-one"]],
            )


class TestDeckDesign:
    def test_pages_must_be_sequential(self) -> None:
        theme = get_builtin_theme("aurora")
        page = PageDesign.model_validate(
            normalize_page_payload({"role": "content", "elements": [_text("t")]}, page_number=2)
        )
        with pytest.raises(ValidationError, match="numbered 1..N"):
            DeckDesign(deck_title="d", theme=theme, pages=[page])


class TestDesignSystem:
    def test_every_builtin_theme_is_readable(self) -> None:
        for theme in BUILTIN_THEMES.values():
            assert contrast_ratio(theme.palette.text, theme.palette.background) >= 4.5
            assert contrast_ratio(theme.palette.on_primary, theme.palette.primary) >= 3.0

    def test_type_scale_covers_all_text_roles(self) -> None:
        from ppt_agent.v2.design import TextRole
        from typing import get_args

        assert set(get_args(TextRole)) == set(TYPE_SCALE)

    def test_normalize_theme_fixes_unreadable_text(self) -> None:
        bad = get_builtin_theme("aurora").model_copy(
            update={
                "palette": get_builtin_theme("aurora").palette.model_copy(
                    update={"text": "#F0F0F0"}  # light gray on near-white background
                )
            }
        )
        fixed = normalize_theme(bad)
        assert contrast_ratio(fixed.palette.text, fixed.palette.background) >= 4.5

    def test_best_text_color_picks_light_on_dark(self) -> None:
        palette = get_builtin_theme("aurora").palette
        assert best_text_color(palette, palette.primary) == "on_primary"
        assert best_text_color(palette, palette.background) == "text"
