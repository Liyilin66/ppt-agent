import asyncio
from pathlib import Path

import pytest

from ppt_agent.v2 import figures
from ppt_agent.v2.htmldeck import browser, graphic_checks, layout_plan, sanitize, themes
from ppt_agent.v2.htmldeck.convert import to_page
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.htmldeck.fonts import office_font_css
from ppt_agent.v2.ir import ImageItem

REPORT = Path(__file__).parent.parent / "eval" / "sources" / "caict_ai_2024.pdf"
needs_browser = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")


@pytest.mark.skipif(not REPORT.is_file(), reason="the sample report is not checked out")
def test_captioned_pictures_are_taken_from_the_report_with_their_credit(tmp_path):
    found = figures.extract([REPORT], tmp_path)
    assert [item["label"] for item in found] == [f"图 {n}" for n in range(1, 9)]
    first = found[0]
    assert first["title"] == "全球人工智能产业规模（单位：亿美元）" and first["credit"] == "IDC" and first["page"] == 12
    assert (tmp_path / first["id"]).is_file() and first["width"] > first["height"]
    assert figures.caption(first) == "图 1 全球人工智能产业规模（单位：亿美元） · 来源：IDC · 原文第 12 页"
    # The running header on every page is not a figure.
    assert all(item["height"] >= figures.MIN_HEIGHT for item in found)


def figure(page, title, name="f.png"):
    return {"id": name, "label": "图 1", "title": title, "credit": "IDC", "document": "r.pdf", "page": page,
            "ref": f"r.pdf 第{page}页", "width": 657, "height": 387}


def test_a_figure_goes_to_the_page_that_cites_it_and_is_about_it():
    pages = {
        4: {"title": "2024年全球AI产业将达6233亿美元", "source": "r.pdf 第11-12页",
            "highlight": {"value": "6233亿美元", "text": "IDC预测的2024年全球人工智能产业规模"}, "items": []},
        5: {"title": "全球人工智能产业规模持续扩大", "source": "r.pdf 第30页", "items": []},   # about it, but cites elsewhere
        6: {"title": "企业应尽快搭建能力平台", "source": "r.pdf 第12页", "items": []},          # cites it, about something else
    }
    scale = figure(12, "全球人工智能产业规模（单位：亿美元）")
    placed = figures.assign([scale], pages)
    assert list(placed) == [4] and placed[4]["id"] == "f.png" and placed[4]["ratio"] == round(657 / 387, 3)
    assert figures.assign([scale], pages, taken={4}) == {}  # the page already draws a chart


def test_a_page_with_a_figure_is_planned_around_it_and_may_use_no_other():
    content = {"archetype": "rich", "relation": "trend", "items": [{"heading": "a"}],
               "figure": {"id": "f.png", "caption": "图 1", "ratio": 1.7}}
    assert layout_plan.candidates(content)[0] == "figure"
    page = '<div class="page"><div class="r" style="grid-column:6/13;grid-row:2/9"><div class="plate" data-figure="{}" data-ratio="1.7"></div></div></div>'
    assert graphic_checks.find(page.format("f.png"), content) == []
    assert [d.kind for d in graphic_checks.find(page.format("other.png"), content)] == ["unsupported"]
    assert [d.kind for d in graphic_checks.find(page.format("f.png"), {"items": []})] == ["unsupported"]


@needs_browser
def test_the_plate_keeps_the_figures_proportions_and_becomes_a_picture():
    async def run():
        theme, font_css = themes.get("studio"), office_font_css()[0]
        body, rejected = sanitize.clean(themes.studio_example("figure"))
        async with browser.Session() as session:
            return rejected, await session.measure(wrap(body, theme.css, font_css, theme.backdrop), screenshot=False)

    rejected, measured = asyncio.run(run())
    assert rejected == []
    picture = next(item for item in measured.items if item["kind"] == "image")
    assert picture["figure"] == "sample.png" and abs(picture["w"] / picture["h"] - 1.7) < 0.08 and picture["w"] > 400
    page = to_page(measured, page_number=4)
    image = next(element for element in page.elements if isinstance(element, ImageItem))
    assert image.src == "sample.png"
