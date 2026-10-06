"""Invariants of the code-typeset visual system (profiles x archetypes)."""

from __future__ import annotations

import itertools
import zipfile
from pathlib import Path

import pytest

from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH, ChartItem, LineItem, TextItem
from ppt_agent.v2.metrics import fit_font_size
from ppt_agent.v2.render import render_deck
from ppt_agent.v2.visual.archetypes import (
    MIN_BODY_PT,
    PointItem,
    PointsContent,
    _cjk_line_chars,
    _widow_safe_width,
    typeset_deck,
    typeset_page,
)
from ppt_agent.v2.visual.profiles import PROFILES
from ppt_agent.v2.visual.compositions import COMPOSITIONS
from ppt_agent.v2.visual.samples import CNNIC_DECK, SAMPLES

ALL_CONTENT = SAMPLES + CNNIC_DECK
# Every composition, under every profile, on every sample it can lay out.
CASES = [
    (profile, content, comp.key)
    for profile in PROFILES.values()
    for comp in COMPOSITIONS
    for content in ALL_CONTENT
    if comp.archetype == content.archetype and comp.valid(content)
]
CASE_IDS = [f"{profile.name}-{key}-{content.title[:6]}" for profile, content, key in CASES]


@pytest.mark.parametrize(("profile", "content", "key"), CASES, ids=CASE_IDS)
class TestTypesetInvariants:
    def test_every_element_stays_on_the_canvas(self, profile, content, key) -> None:
        page, _ = typeset_page(content, profile, page_number=1, deck_title="测试", composition=key)
        for element in page.elements:
            if isinstance(element, LineItem):
                continue
            frame = element.frame
            assert frame.x >= 0 and frame.y >= 0, element.id
            assert frame.right <= CANVAS_WIDTH + 0.5, element.id
            assert frame.bottom <= CANVAS_HEIGHT + 0.5, element.id

    def test_text_frames_never_overlap(self, profile, content, key) -> None:
        page, _ = typeset_page(content, profile, page_number=1, deck_title="测试",
                               composition=key)
        texts = [e for e in page.elements if isinstance(e, TextItem)]
        for a, b in itertools.combinations(texts, 2):
            ix = min(a.frame.right, b.frame.right) - max(a.frame.x, b.frame.x)
            iy = min(a.frame.bottom, b.frame.bottom) - max(a.frame.y, b.frame.y)
            assert ix <= 1 or iy <= 1, f"{a.text!r} overlaps {b.text!r}"

    def test_reading_text_respects_the_minimum_size(self, profile, content, key) -> None:
        page, _ = typeset_page(content, profile, page_number=1, deck_title="测试",
                               composition=key)
        for text in page.elements:
            if isinstance(text, TextItem) and text.role in ("body", "h3", "subtitle"):
                assert text.size_pt >= MIN_BODY_PT, text.text

    def test_renderer_keeps_the_typeset_size(self, profile, content, key) -> None:
        """The frame is tall enough that the renderer's fit step never shrinks."""

        page, _ = typeset_page(content, profile, page_number=1, deck_title="测试",
                               composition=key)
        for text in page.elements:
            if isinstance(text, TextItem):
                fitted = fit_font_size(
                    text.text, role=text.role, frame_width_units=text.frame.w,
                    frame_height_units=text.frame.h, requested_size_pt=text.size_pt,
                )
                assert fitted == text.size_pt, text.text

    def test_charts_get_most_of_the_content_height(self, profile, content, key) -> None:
        page, _ = typeset_page(content, profile, page_number=1, deck_title="测试",
                               composition=key)
        charts = [e for e in page.elements if isinstance(e, ChartItem)]
        for chart in charts:
            assert chart.frame.h >= 200


def test_sparse_pages_grow_type_instead_of_leaving_empty_cards() -> None:
    page, notes = typeset_page(SAMPLES[1], PROFILES["training"], page_number=1, deck_title="t")
    assert any("->" in note for note in notes)
    bodies = [e for e in page.elements if isinstance(e, TextItem) and e.role == "body"]
    assert min(b.size_pt for b in bodies) > PROFILES["training"].sizes.body


def test_dense_pages_shrink_but_never_below_the_floor() -> None:
    long_body = "这是一段明显超过常规长度的说明文字，用来把版面挤满，检验排版器的收缩策略是否守住底线。"
    content = PointsContent(
        title="五个要点同时出现时的排版",
        items=[PointItem(heading=f"要点 {n}", body=long_body[:80]) for n in range(5)],
        takeaway="收缩到底线后仍放不下时必须如实报告，而不是溢出。",
    )
    page, _ = typeset_page(content, PROFILES["launch"], page_number=1, deck_title="t")
    bodies = [e for e in page.elements if isinstance(e, TextItem) and e.role == "body"]
    assert min(b.size_pt for b in bodies) >= MIN_BODY_PT


def test_widow_guard_matches_rendered_wrap_and_fixes_it() -> None:
    text = "客户资料、报价和未公开的项目文件，不要粘贴进外部 AI 工具。"
    # PowerPoint rendered this frame as 13 / 16 / 2 characters (one-char widow + "。").
    assert _cjk_line_chars(text, 16, 290.67) == [13, 16, 2]
    narrowed = _widow_safe_width(text, 16, 290.67)
    assert _cjk_line_chars(text, 16, narrowed)[-1] >= 3


def test_kinsoku_carries_the_previous_character_down() -> None:
    # Six one-em slots: "。" may not start line two, so "五" moves down with it.
    assert _cjk_line_chars("一二三四五六。", 12, 12 * 96 / 72 * 6) == [5, 2]


def test_rendered_pptx_marks_chinese_runs_for_line_breaking(tmp_path: Path) -> None:
    deck, _ = typeset_deck(SAMPLES, PROFILES["corporate"], deck_title="测试")
    output = render_deck(deck, tmp_path / "deck.pptx")
    with zipfile.ZipFile(output) as archive:
        slide = archive.read("ppt/slides/slide1.xml").decode("utf-8")
    assert 'lang="zh-CN"' in slide
    assert 'eaLnBrk="1"' in slide


def test_every_profile_renders_all_samples(tmp_path: Path) -> None:
    for name, profile in PROFILES.items():
        deck, notes = typeset_deck(SAMPLES, profile, deck_title="测试")
        assert render_deck(deck, tmp_path / f"{name}.pptx").exists()
        assert not any("exceeds" in note for page in notes for note in page)


def test_every_composition_is_exercised() -> None:
    assert {key for _, _, key in CASES} == {c.key for c in COMPOSITIONS}


@pytest.mark.parametrize("profile", PROFILES.values(), ids=list(PROFILES))
def test_a_deck_never_repeats_the_previous_pages_arrangement(profile) -> None:
    _, notes = typeset_deck(CNNIC_DECK, profile, deck_title="测试")
    keys = [page[0] for page in notes]
    assert all(a != b for a, b in zip(keys, keys[1:])), keys
    assert not any("dropped" in note for page in notes for note in page)


def test_styles_differ_in_arrangement_not_only_colour() -> None:
    arrangements = {
        name: tuple(page[0] for page in typeset_deck(CNNIC_DECK, p, deck_title="t")[1])
        for name, p in PROFILES.items()
    }
    assert len(set(arrangements.values())) == len(arrangements), arrangements
