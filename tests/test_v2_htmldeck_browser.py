import asyncio

import pytest

from ppt_agent.v2.htmldeck import browser
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.htmldeck.fonts import office_font_css

pytestmark = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")

CSS = """
.slide{background:#101418;color:#fff;font-family:'Microsoft YaHei',sans-serif;font-size:20px}
.card{position:absolute;left:100px;top:200px;width:400px;height:240px;padding:20px;
  background:#223;border-radius:16px;border:1px solid rgba(255,255,255,.2)}
.big{font-size:40px;font-weight:700;line-height:50px}
.grad{background:linear-gradient(90deg,#6ea8ff,#3ff0d0);-webkit-background-clip:text;color:transparent}
.pill{display:inline-block;background:#3ff0d0;color:#000;border-radius:99px;padding:2px 12px;font-size:14px}
"""
BODY = """
<div class="card"><div class="pill">落地快</div>
  <div class="big">研发<span class="grad">设计</span></div>
  <p>知识密集，理论基础扎实，普遍拥有高质量数据，适合作为第一批试点场景</p>
</div>
<div class="chart" style="position:absolute;left:600px;top:200px;width:500px;height:300px"
  data-chart='{"kind":"column","categories":["2022","2024"],"series":[{"name":"占比","values":[4.5,12.1]}]}'></div>
<table style="position:absolute;left:600px;top:540px;width:500px"><tr><th>环节</th><th>成效</th></tr>
  <tr><td>研发</td><td>成本降 70%</td></tr></table>
"""


def _measure(body):
    async def run():
        async with browser.Session() as session:
            return await session.measure(wrap(body, CSS, office_font_css()[0]))

    return asyncio.run(run())


def test_boxes_text_charts_and_tables_are_measured_in_slide_pixels():
    measured = _measure(BODY)
    kinds = [item["kind"] for item in measured.items]
    assert kinds == ["box", "box", "text", "text", "text", "chart", "table"]
    card = measured.items[0]
    assert (card["x"], card["y"], card["w"], card["h"]) == (100, 200, 400, 240)
    assert card["radius"] == [16, 16, 16, 16]
    pill, heading, body = [item for item in measured.items if item["kind"] == "text"]
    assert pill["lines"] == 1 and "".join(run["t"] for run in pill["runs"]).strip() == "落地快"
    assert [run["t"] for run in heading["runs"]] == ["研发", "设计"]
    assert heading["runs"][1]["grad"].startswith("linear-gradient")
    assert heading["linePx"] == 50 and heading["glyphPx"] > 50
    assert body["lines"] == 2 and body["box"]["x"] == pytest.approx(121, abs=1)
    assert body["parent"]["w"] == 400
    chart = measured.items[5]
    assert (chart["x"], chart["w"], chart["h"]) == (600, 500, 300) and '"column"' in chart["spec"]
    table = measured.items[6]
    assert table["headers"] == ["环节", "成效"] and table["rows"] == [["研发", "成本降 70%"]]
    assert measured.screenshot_png[:4] == b"\x89PNG"
    assert measured.notes == [] and measured.clipped == []


def test_unsupported_css_and_clipped_content_are_reported():
    measured = _measure(
        '<div style="transform:rotate(5deg)">x</div>'
        '<div style="width:200px;height:30px;overflow:hidden;font-size:20px">'
        + "很长的文字" * 20
        + "</div>"
    )
    assert "transform" in measured.notes
    assert len(measured.clipped) == 1
