import asyncio
from pathlib import Path

import pytest

from conftest import needs_office_fonts

from ppt_agent.v2.htmldeck import aesthetics, browser, sanitize, themes
from ppt_agent.v2.htmldeck.browser import Measured
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.htmldeck.fonts import office_font_css

FIXTURES = Path(__file__).parent / "fixtures" / "studio20"
needs_browser = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")

# Pages of the 2026-11-13 run, as the model wrote them.
BAD = {"page_011": {"long_display", "focal_conflict"}, "page_017": {"hole", "focal_conflict"},
       "page_020": {"tight"}}
GOOD = {"page_005": "visual", "page_012": "visual", "page_018": "visual", "page_013": "standard",
        "page_006": "standard"}


def measure_fixtures(names):
    async def run():
        theme, font_css = themes.get("studio"), office_font_css()[0]
        async with browser.Session() as session:
            found = {}
            for name in names:
                body, _ = sanitize.clean((FIXTURES / f"{name}.html").read_text("utf-8"))
                found[name] = await session.measure(wrap(body, theme.css, font_css, theme.backdrop),
                                                    screenshot=False)
            return found

    return asyncio.run(run())


@pytest.fixture(scope="module")
def pages():
    return measure_fixtures([*BAD, *GOOD])


@needs_browser
@pytest.mark.parametrize("name", BAD)
def test_known_bad_pages_are_flagged(pages, name):
    found = {defect.kind for defect in aesthetics.find(pages[name])}
    assert BAD[name] <= found, found


@needs_browser
@needs_office_fonts
@pytest.mark.parametrize("name", GOOD)
def test_known_good_pages_stay_clean(pages, name):
    assert [defect.message for defect in aesthetics.find(pages[name], tier=GOOD[name])] == []


def text(words, *, size, x=80, y=100, w=400, lines=1, cls="b", color="rgb(26, 25, 22)"):
    height = size * 1.2 * lines
    return {"kind": "text", "runs": [{"t": words, "size": size, "color": color}], "lines": lines, "cls": cls,
            "linePx": size * 1.2, "text": {"x": x, "y": y, "w": w, "h": height},
            "box": {"x": x, "w": w}, "parent": {"x": 0, "y": 0, "w": 1280, "h": 720}}


def kinds(items, **kwargs):
    return [defect.kind for defect in aesthetics.find(Measured(background="", items=items), **kwargs)]


FOOT = text("一段撑到页面底部的正文", size=19, y=600)


def test_display_type_needs_air():
    close = [text("上一段正文", size=19, y=300), text("一句大字", size=54, y=300 + 23 + 10, cls="q1"), FOOT]
    apart = [text("上一段正文", size=19, y=300), text("一句大字", size=54, y=300 + 23 + 40, cls="q1"), FOOT]
    assert "tight" in kinds(close) and "tight" not in kinds(apart)


def test_a_long_sentence_is_not_display_type():
    sentence = "管理层应优先在工程约束明确可仿真验证的设计任务中投入试点"
    titled = [text("页面标题", size=32, cls="t2"), text(sentence, size=54, y=200, lines=4, cls="q1"), FOOT]
    assert "long_display" in kinds(titled)
    # A statement page is nothing but its statement, and may run to three lines.
    assert "long_display" not in kinds([text(sentence, size=54, y=200, w=1000, lines=2, cls="q1"), FOOT])


def test_one_focus_per_page():
    # Beside a large title the figure must clearly lead; a 58px figure does not, a 150px one does.
    assert "focal_conflict" in kinds([text("大标题", size=44, cls="t1"), text("超10亿", size=58, y=220, cls="d4"), FOOT])
    assert "focal_conflict" not in kinds([text("大标题", size=44, cls="t1"), text("90%", size=150, y=220, cls="d2"), FOOT])
    # Nearly one size is indecision; exactly one size is a pair to compare.
    assert "focal_conflict" in kinds([text("316", size=96, cls="d3"), text("84%", size=82, y=300, cls="d3"), FOOT])
    assert "focal_conflict" not in kinds([text("48%", size=96, cls="d3"), text("67%", size=96, x=600, cls="d3"), FOOT])
    assert "focal_conflict" not in kinds([text("标题", size=32, cls="t2"), text("316", size=230, y=200, cls="d1"),
                                          text("84%", size=58, x=700, y=300, cls="d4"), FOOT])


def test_a_hole_in_a_column_and_a_page_that_stops_short():
    side = [text(f"边栏第 {index} 条", size=19, x=700, y=150 + 40 * index) for index in range(6)]
    assert "hole" in kinds([text("标题", size=32, cls="t2"), text("正文", size=19, y=400), FOOT, *side])
    # The same gap across the whole page is a pause, until it is most of the page.
    assert "hole" not in kinds([text("标题", size=32, cls="t2"), text("正文", size=19, y=400), FOOT])
    assert "hole" in kinds([text("标题", size=32, cls="t2"), text("正文", size=19, y=480), FOOT])
    assert "hole" not in kinds([text("标题", size=32, cls="t2"), text("正文", size=19, y=200), FOOT])
    short = [text("标题", size=32, cls="t2"), text("正文", size=19, y=200), text("中段", size=19, y=340),
             text("末段", size=19, y=470)]
    assert "hole" in kinds(short, tier="deep") and "hole" not in kinds(short, tier="visual")


def test_red_is_counted_and_small_notes_are_caught():
    red = "rgb(197, 52, 28)"
    many = [text(f"红字{index}", size=19, y=100 + 60 * index, color=red) for index in range(5)] + [FOOT]
    assert "red_overuse" in kinds(many) and "red_overuse" not in kinds(many[1:])
    assert "red_overuse" not in kinds([text("标签", size=13, cls="kicker", color=red)] + many[1:])
    assert "small_note" in kinds([text("说明", size=14, cls="c"), FOOT])
    assert "small_note" not in kinds([text("标签", size=13, cls="k"), text("说明", size=15, cls="c"), FOOT])
