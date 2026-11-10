import pytest

from ppt_agent.v2.htmldeck.browser import Measured
from ppt_agent.v2.htmldeck.convert import ConvertError, css_paint, to_page
from ppt_agent.v2.ir import BoxItem, ChartItem, RichTextItem, TableItem

SIDE0 = {"w": 0, "c": "rgb(0, 0, 0)", "st": "none"}


def box(**over):
    base = {"kind": "box", "x": 100, "y": 200, "w": 400, "h": 240, "bg": "rgb(34, 34, 51)",
            "img": "none", "sides": [SIDE0] * 4, "radius": [0, 0, 0, 0], "shadow": "none", "op": 1}
    return {**base, **over}


def run(text, **over):
    base = {"t": text, "color": "rgb(255, 255, 255)", "size": 40, "weight": 700, "italic": False,
            "font": 'Georgia, SimSun, serif', "spacing": 0, "grad": None}
    return {**base, **over}


def text(runs, lines=1, **over):
    base = {"kind": "text", "runs": runs, "lines": lines, "align": "left", "op": 1, "linePx": 50,
            "glyphPx": 52, "text": {"x": 120, "y": 219, "w": 160, "h": 52},
            "box": {"x": 120, "w": 360}, "parent": {"x": 100, "y": 200, "w": 400, "h": 240}}
    return {**base, **over}


def page(*items):
    return to_page(Measured(background="rgb(16, 20, 24)", items=list(items)), page_number=3)


def test_gradient_angle_is_converted_to_powerpoint_convention():
    paint = css_paint(None, "linear-gradient(90deg, rgb(110, 168, 255), rgba(63, 240, 208, 0.5))")
    assert paint.kind == "linear" and paint.angle_deg == 0
    assert [(stop.hex, stop.alpha, stop.pos) for stop in paint.stops] == [
        ("6EA8FF", 1.0, 0.0), ("3FF0D0", 0.5, 100.0)]
    assert css_paint(None, "linear-gradient(rgb(0, 0, 0), rgb(255, 255, 255))").angle_deg == 90
    radial = css_paint(None, "radial-gradient(circle, rgb(43, 92, 255) 0%, rgba(43, 92, 255, 0) 65%)", 0.5)
    assert radial.kind == "radial" and radial.stops[0].alpha == 0.5 and radial.stops[1].pos == 65


def test_boxes_become_shapes_with_radius_outline_and_side_rules():
    uniform = {"w": 2, "c": "rgba(255, 255, 255, 0.2)", "st": "solid"}
    result = page(
        box(radius=[16] * 4, sides=[uniform] * 4),
        box(x=600, w=80, h=80, radius=[40] * 4),
        box(x=700, w=60, h=200, radius=[12, 12, 0, 0]),
        box(x=800, bg="rgba(0, 0, 0, 0)", sides=[{"w": 4, "c": "rgb(200, 55, 31)", "st": "solid"},
                                                  SIDE0, SIDE0, SIDE0]),
        box(x=5000),
    )
    card, circle, bar, rule = result.elements
    assert all(isinstance(element, BoxItem) for element in result.elements)
    assert (card.shape, card.radius, card.stroke_hex, card.stroke_alpha, card.stroke_width) == (
        "round", 16, "FFFFFF", 0.2, 2)
    assert (card.frame.x, card.frame.w) == (101, 398)  # outline centred on the CSS border
    assert circle.shape == "ellipse" and bar.shape == "round_top"
    assert (rule.frame.y, rule.frame.h, rule.fill.hex) == (200, 4, "C8371F")
    assert result.background_hex == "101418" and result.show_chrome is False


def test_single_lines_do_not_wrap_and_sit_in_their_line_box():
    result = page(text([run("研发"), run("设计", grad="linear-gradient(90deg, rgb(110, 168, 255), rgb(63, 240, 208))")]),
                  text([run("居中")], align="center"))
    left, centered = result.elements
    assert isinstance(left, RichTextItem) and left.wrap is False and left.valign == "middle"
    assert (left.frame.x, left.frame.y, left.frame.h, left.line_pt) == (120, 220, 50, 37.5)
    assert left.frame.w > 160
    first, second = left.runs
    assert (first.font, first.ea_font, first.size_pt, first.bold) == ("Georgia", "SimSun", 30, True)
    assert second.paint.kind == "linear"
    assert centered.frame.x + centered.frame.w / 2 == pytest.approx(200)


def test_wrapped_text_keeps_the_browser_width_and_line_pitch():
    item = text([run("  第一段文字 ", size=20, weight=400), {"br": True}, run(" 第二段 ", size=20, weight=400)],
                lines=3, linePx=32, glyphPx=26, text={"x": 120, "y": 303, "w": 350, "h": 90})
    block = page(item).elements[0]
    assert block.wrap and block.valign == "top" and block.line_pt == 24
    assert (block.frame.x, block.frame.y, block.frame.w, block.frame.h) == (120, 300, 362, 96)
    assert [(part.text, part.line_break) for part in block.runs] == [
        ("第一段文字", False), ("", True), ("第二段", False)]


def test_charts_and_tables_become_native_elements():
    chart = {"kind": "chart", "x": 600, "y": 200, "w": 500, "h": 300,
             "spec": '{"kind":"column","categories":["2022","2024"],"series":[{"name":"占比","values":[4.5,12.1]}],"highlight":1,"format":"0.0\\"%\\""}'}
    table = {"kind": "table", "x": 600, "y": 540, "w": 500, "h": 80, "headers": ["环节", "成效"],
             "rows": [["研发", "成本降 70%"]], "widths": [100, 400], "size": 16, "variant": "strong"}
    chart_item, table_item = page(chart, table).elements
    assert isinstance(chart_item, ChartItem) and chart_item.highlight == 1
    assert chart_item.series[0].values == [4.5, 12.1] and not chart_item.show_legend
    assert isinstance(table_item, TableItem) and table_item.column_widths == [100, 400]
    assert table_item.strong_header and table_item.font_pt == 12


@pytest.mark.parametrize("spec", [
    "not json",
    '{"kind":"radar","categories":["a","b"],"series":[{"values":[1,2]}]}',
    '{"kind":"bar","categories":["a"],"series":[{"values":[1]}]}',
    '{"kind":"bar","categories":["a","b"],"series":[{"values":[1]}]}',
    '{"kind":"bar","categories":["a","b"],"series":[{"values":["1","2"]}]}',
])
def test_bad_chart_data_is_rejected(spec):
    with pytest.raises(ConvertError):
        page({"kind": "chart", "x": 0, "y": 0, "w": 100, "h": 100, "spec": spec})


def test_an_empty_page_is_rejected():
    with pytest.raises(ConvertError):
        page(box(x=5000))
