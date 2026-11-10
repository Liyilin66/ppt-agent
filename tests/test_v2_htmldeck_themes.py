import asyncio

import pytest

from ppt_agent.v2.htmldeck import browser, sanitize, themes
from ppt_agent.v2.htmldeck.checks import find_defects
from ppt_agent.v2.htmldeck.convert import to_page
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.htmldeck.fonts import office_font_css
from ppt_agent.v2.ir import ChartItem, TableItem

FULL_PAGE = ("cover", "section", "statement")


def test_themes_share_one_vocabulary_and_differ_in_look():
    loaded = [themes.get(name) for name in themes.THEME_NAMES]
    assert [theme.name for theme in loaded] == ["editorial", "dark", "bright"]
    assert len({theme.css for theme in loaded}) == 3
    for theme in loaded:
        assert ".panel{" in theme.css and theme.spec.motif == "none"
        assert not theme.spec.chrome.show_footer
        for banned in ("::before", "::after", "transform:", "filter:", "url("):
            assert banned not in theme.css
    assert "data-chart" in themes.guide()
    with pytest.raises(ValueError):
        themes.get("neon")


def test_examples_survive_sanitising_untouched():
    for name in themes.EXAMPLE_NAMES:
        _, rejected = sanitize.clean(themes.example(name))
        assert rejected == [], name


@pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")
def test_every_example_is_clean_in_every_theme():
    async def run():
        fonts = office_font_css()[0]
        problems, kinds = [], set()
        async with browser.Session() as session:
            for theme_name in themes.THEME_NAMES:
                theme = themes.get(theme_name)
                for example in themes.EXAMPLE_NAMES:
                    full = example in FULL_PAGE
                    body, _ = sanitize.clean(themes.example(example))
                    measured = await session.measure(
                        wrap(body, theme.css, fonts, "" if full else theme.backdrop), screenshot=False)
                    for defect in find_defects(measured, require_fill=not full):
                        problems.append((theme_name, example, defect.message))
                    page = to_page(measured, page_number=1)
                    kinds.update(type(element) for element in page.elements)
        return problems, kinds

    problems, kinds = asyncio.run(run())
    assert problems == []
    assert ChartItem in kinds and TableItem in kinds
