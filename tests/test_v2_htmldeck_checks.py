import asyncio

import pytest

from conftest import needs_office_fonts

from ppt_agent.v2.htmldeck import browser, sanitize
from ppt_agent.v2.htmldeck.checks import allowed_numbers, find_defects, numbers_in
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.htmldeck.fonts import office_font_css


def test_sanitize_keeps_layout_and_removes_everything_else():
    cleaned, rejected = sanitize.clean(
        '```html\n<div class="card big!" style="display:flex; color:red; gap:12px; background:url(x)" '
        'onclick="x()" id="a">文字<script>alert(1)</script><img src="http://x/y.png">'
        '<a href="http://x">链接</a><svg><circle/></svg><br/></div>\n```'
    )
    assert cleaned == '<div class="card big" style="display:flex;gap:12px">文字链接<br></div>'
    assert set(rejected) == {"内联样式 color", "内联样式 background", "属性 onclick", "<script>",
                             "<img>", "<a>", "<svg>"}


def test_sanitize_keeps_chart_data_and_tables():
    cleaned, rejected = sanitize.clean(
        "<div class=\"chart\" data-chart='{\"kind\":\"bar\"}'></div>"
        '<table data-variant="strong"><tr><th>a</th></tr><tr><td>1 &lt; 2</td></tr></table>'
    )
    assert "data-chart=\"{&quot;kind&quot;:&quot;bar&quot;}\"" in cleaned
    assert "<td>1 &lt; 2</td>" in cleaned and rejected == []


def test_numbers_are_compared_by_value():
    assert numbers_in("同比 +21.5%，约 6,233 亿") == numbers_in("21.50 和 6233")
    allowed = allowed_numbers([{"title": "规模 6233 亿美元", "values": [4.5, 12.1], "n": None}])
    assert numbers_in("12.1%") <= allowed and not numbers_in("13%") <= allowed


needs_browser = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")
CSS = """
.slide{background:#fff;color:#111;font-family:'Microsoft YaHei',sans-serif;font-size:18px;padding:60px}
.card{background:#eee;padding:16px;width:300px;height:80px}
.tall{height:520px}
.tiny{font-size:9px}
.spin{transform:rotate(8deg)}
"""


def _defects(body, **kwargs):
    async def run():
        async with browser.Session() as session:
            return await session.measure(wrap(body, CSS, office_font_css()[0]), screenshot=False)

    return [defect.kind for defect in find_defects(asyncio.run(run()), **kwargs)]


@needs_browser
def test_a_clean_page_has_no_defects():
    assert _defects('<div class="card tall">研发成本降低约 70%</div><div class="pageno">07</div>',
                    allowed=numbers_in("70%")) == []


@needs_browser
@pytest.mark.parametrize("body, kind", [
    ('<div class="card"><p>一段比色块高得多的文字。</p><p>第二行</p><p>第三行</p><p>第四行</p></div>'
     '<div class="tall">下面的内容</div>', "overflow"),
    ('<div class="tall"></div><div class="tall" style="display:flex;align-items:flex-end">底部</div>', "off_canvas"),
    ('<div class="tall" style="position:relative"><p style="position:absolute;left:0;top:0">第一段文字</p>'
     '<p style="position:absolute;left:10px;top:4px">第二段文字</p><p style="position:absolute;top:480px">底</p></div>', "overlap"),
    ('<div class="tall" style="position:relative"><p style="position:absolute;top:70px">被色块盖住一半的一行字</p>'
     '<div class="card" style="position:absolute;left:0;top:0"></div><p style="position:absolute;top:480px">底</p></div>', "overlap"),
    ('<div class="card tall"><span class="tiny">很小的字</span></div>', "small_text"),
    ('<div class="card">只有上面一点内容</div>', "sparse"),
    ('<div class="card tall">准确率 93%</div>', "extra_number"),
    ('<div class="card tall">文字</div><div class="chart" style="width:300px;height:200px" data-chart="{bad"></div>', "bad_chart"),
    ('<div class="card tall" style="display:flex"><div class="spin">转</div></div>', "unsupported"),
], ids=["overflow", "off_canvas", "overlap", "cut-by-block", "small_text", "sparse", "extra_number", "bad_chart", "unsupported"])
def test_each_kind_of_defect_is_found(body, kind):  # noqa: D103
    found = _defects(body, allowed=numbers_in("70%"))
    assert kind in found, found


def _line(words, *, size, y, cls):
    return {"kind": "text", "runs": [{"t": words, "size": size, "color": "rgb(26, 25, 22)"}], "lines": 1, "cls": cls,
            "linePx": size * 1.2, "text": {"x": 700, "y": y, "w": 300, "h": size * 1.2},
            "box": {"x": 700, "w": 300}, "parent": {"x": 0, "y": 0, "w": 1280, "h": 720}}


def test_half_a_unit_under_its_figure_is_a_mistake_and_a_whole_one_is_a_choice():
    from ppt_agent.v2.htmldeck.browser import Measured
    from ppt_agent.v2.htmldeck.checks import find_defects

    def kinds(figure, unit, gap=4):
        items = [_line(figure, size=58, y=200, cls="d4"), _line(unit, size=24, y=200 + 70 + gap, cls="unit")]
        return [d.kind for d in find_defects(Measured(background="", items=items), None, require_fill=False)]

    assert "split_unit" in kinds("6233亿", "美元")
    assert "split_unit" not in kinds("1287", "兆瓦时")      # the whole unit under the figure
    beside = [_line("6233", size=58, y=200, cls="d4"), {**_line("亿美元", size=24, y=230, cls="unit")}]
    assert "split_unit" not in [d.kind for d in find_defects(Measured(background="", items=beside), None, require_fill=False)]


@needs_browser
@needs_office_fonts
def test_fix7_orphan_repeated_figure_and_glyph_overlap():
    css_page = '<div class="tall" style="position:relative">'
    orphan = css_page + '<p style="width:200px;font-size:20px">一二三四五六七八九十一二三四五六七八九十甲</p></div>'
    assert "orphan" in _defects(orphan, allowed=numbers_in(""), roles=True)
    repeated = css_page + '<div class="d1">483.8</div><div class="t3">483.8万个</div><div class="t3">28.5→29.4</div></div>'
    found = _defects(repeated, allowed=numbers_in("483.8 28.5 29.4"), roles=True)
    assert found.count("repeated_figure") == 1
    # a figure running one character into a long label: 7% of the label's area, still a collision
    collide = (css_page + '<p style="position:absolute;left:0;top:0;font-size:80px;white-space:nowrap;margin:0">6.02亿人</p>'
               '<p style="position:absolute;left:260px;top:40px;font-size:24px;white-space:nowrap;margin:0">'
               '2025年12月生成式人工智能用户规模说明文字</p></div>')
    assert "overlap" in _defects(collide, allowed=numbers_in("6.02 2025 12"), roles=True)


@needs_browser
def test_a_one_character_unit_stacked_under_its_figure_is_split():
    body = ('<div class="tall"><div class="d1" style="font-size:150px;line-height:1.05">29.4</div>'
            '<div class="unit" style="font-size:24px">个</div></div>')
    assert "split_unit" in _defects(body, allowed=numbers_in("29.4"), roles=True)
    whole = ('<div class="tall"><div class="d1" style="font-size:150px;line-height:1.05">29.4</div>'
             '<div class="unit" style="font-size:24px">万户家庭宽带接入</div></div>')
    assert "split_unit" not in _defects(whole, allowed=numbers_in("29.4"), roles=True)


@needs_browser
def test_fix8_small_stacked_unit_and_undeclared_half_bleed():
    stacked = ('<div class="tall"><div class="d1" style="font-size:230px;line-height:1.1">15.97</div>'
               '<div class="unit" style="font-size:24px">万亿元</div></div>')
    assert "split_unit" in _defects(stacked, allowed=numbers_in("15.97"), roles=True)
    panel = ('<div class="tall" style="position:relative"><div style="position:absolute;right:-60px;top:100px;width:600px;'
             'height:300px;background:#1a1916"></div></div>')
    assert "off_canvas" in _defects(panel, allowed=numbers_in(""), roles=True)
