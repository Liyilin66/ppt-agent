import asyncio

import pytest

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
    ('<div class="tall"></div><div class="tall">底部</div>', "off_canvas"),
    ('<div class="tall" style="position:relative"><p style="position:absolute;left:0;top:0">第一段文字</p>'
     '<p style="position:absolute;left:10px;top:4px">第二段文字</p><p style="position:absolute;top:480px">底</p></div>', "overlap"),
    ('<div class="card tall"><span class="tiny">很小的字</span></div>', "small_text"),
    ('<div class="card">只有上面一点内容</div>', "sparse"),
    ('<div class="card tall">准确率 93%</div>', "extra_number"),
    ('<div class="card tall">文字</div><div class="chart" style="width:300px;height:200px" data-chart="{bad"></div>', "bad_chart"),
    ('<div class="card tall" style="display:flex"><div class="spin">转</div></div>', "unsupported"),
], ids=["overflow", "off_canvas", "overlap", "small_text", "sparse", "extra_number", "bad_chart", "unsupported"])
def test_each_kind_of_defect_is_found(body, kind):  # noqa: D103
    found = _defects(body, allowed=numbers_in("70%"))
    assert kind in found, found
