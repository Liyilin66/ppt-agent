import json

import pytest

from ppt_agent.v2.htmldeck import charts, graphic_checks, layout_plan
from ppt_agent.v2.htmldeck.convert import chart_spec


def pt(label, value, display, projected=False, page=12):
    return {"label": label, "value": value, "display": display, "ref": f"caict_ai_2024.pdf 第{page}页", "projected": projected}


def series(kind, points, unit="%", **extra):
    return {"metric": "融资占全行业融资比例", "unit": unit, "kind": kind, "scope": "全球",
            "period": "2022年至2024年上半年", "points": points, **extra}


SHARE = series("time", [pt("2022年", 4.5, "4.5%"), pt("2024年上半年", 12.1, "12.1%")])


@pytest.mark.parametrize("made, kind", [
    (SHARE, "column"),
    (series("time", [pt("2021年", 1, "1%"), pt("2022年", 2, "2%"), pt("2023年", 3, "3%")]), "line"),
    (series("category", [pt("A", 48, "48%"), pt("B", 67, "67%")]), "bar"),
    (series("distribution", [pt("小型", 20, "20%"), pt("大型", 30, "30%")]), "column"),
    (series("share", [pt("大模型", 60, "60%"), pt("其他", 40, "40%")]), "pie"),
    (series("share", [pt("大模型", 60, "60%"), pt("芯片", 15, "15%")]), None),  # not a whole: no pie
])
def test_each_series_gets_the_chart_that_draws_it_honestly(made, kind):
    assert charts.kind_for(made) == kind


def test_the_attribute_is_made_by_code_and_converts():
    spec = chart_spec(charts.attr(SHARE))
    assert spec["categories"] == ["2022年", "2024年上半年"] and spec["series"][0]["values"] == [4.5, 12.1]
    assert spec["highlight"] == 1 and spec["format"] == '#,##0.0"%"'
    ranked = charts.spec(series("category", [pt("GPT-3.5", 48, "48%"), pt("GPT-4", 67, "67%")]))
    assert ranked["categories"] == ["GPT-4", "GPT-3.5"] and ranked["highlight"] == 0
    money = charts.spec(series("time", [pt("2023年", 5132, "5132亿美元"), pt("2024年", 6233, "6233亿美元", True)], unit="亿美元"))
    assert money["projected"] == [1] and money["format"] == '#,##0"亿美元"'


def test_the_caption_says_what_whose_when_and_where_from():
    assert charts.caption(SHARE) == "融资占全行业融资比例（%） · 全球 · 2022年至2024年上半年 · 原文第 12 页"
    rough = series("category", [pt("深度合成算法", 1919, "近1919个", page=57), pt("生成式AI服务", 190, "190个", True, page=58)],
                   unit="个", period=None)
    assert charts.caption(rough) == "融资占全行业融资比例（个） · 全球 · 生成式AI服务为预测值 · 原文作「近1919个」 · 原文第 57–58 页"


def page(attr):
    return f"<div class=\"page\"><div class=\"r\" style=\"grid-column:1/8;grid-row:3/9\"><div class=\"chart\" data-chart='{attr}'></div></div></div>"


def test_a_chart_must_say_exactly_what_the_series_says():
    content = {"series": [SHARE]}
    assert graphic_checks.find(page(charts.attr(SHARE)), content) == []
    changed = charts.attr(SHARE).replace("12.1", "12.7")
    assert [d.kind for d in graphic_checks.find(page(changed), content)] == ["bad_chart"]
    swapped = json.dumps({**charts.spec(SHARE), "categories": ["2024年上半年", "2022年"]}, ensure_ascii=False)
    assert [d.kind for d in graphic_checks.find(page(swapped), content)] == ["bad_chart"]
    assert [d.kind for d in graphic_checks.find(page(charts.attr(SHARE)), {"items": []})] == ["bad_chart"]


RICH = {"archetype": "rich", "relation": "single", "tier": "visual", "highlight": {"value": "316亿美元", "text": "融资"},
        "items": [{"heading": "占比", "body": "升至12.1%"}]}


def test_a_page_with_a_series_is_planned_as_a_chart_and_charts_may_follow_each_other():
    assert layout_plan.candidates({**RICH, "series": [SHARE]})[0] == "chart"
    assert "chart" not in layout_plan.candidates(RICH)
    saved = [layout_plan.Fingerprint(page=4, form="ledger"), layout_plan.Fingerprint(page=5, form="poster", focus="right")]
    plan = layout_plan.repair(saved, {4: {**RICH, "series": [SHARE]}, 5: {**RICH, "series": [SHARE]}})
    assert [(item.form, item.focus) for item in plan] == [("chart", "left"), ("chart", "right")]
    assert layout_plan.repair(saved, {4: RICH, 5: RICH}) == saved
    after = layout_plan.Fingerprint(page=6, form="chart", focus="left", ratio="even")  # same form, another structure
    assert layout_plan.conflict(after, plan, total=20) is None


def test_a_page_given_a_chart_is_planned_as_one_whatever_was_proposed():
    contents = {4: {**RICH, "series": [SHARE], "visual": {"role": "primary"}},
                5: {**RICH, "series": [SHARE], "visual": {"role": "supporting"}}, 6: RICH}
    plan = layout_plan.resolve({4: {"form": "ledger"}, 5: {"form": "poster"}, 6: {"form": "poster"}}, contents)
    assert [item.form for item in plan] == ["chart", "chart", "poster"]
    assert plan[0].structure != plan[1].structure


def test_bars_start_at_zero_in_the_rendered_chart(tmp_path):
    from pptx import Presentation

    from ppt_agent.v2.ir import DeckDesign
    from ppt_agent.v2.render import render_deck

    deck = DeckDesign.model_validate_json(open("data/html-pages/p3/scale/caict/deck_design.json").read()) \
        if __import__("pathlib").Path("data/html-pages/p3/scale/caict/deck_design.json").is_file() else None
    if deck is None:
        import pytest
        pytest.skip("P3 run not present")
    path = render_deck(deck, tmp_path / "d.pptx")
    charts = [shape.chart for slide in Presentation(path).slides for shape in slide.shapes if shape.has_chart]
    bars = [chart for chart in charts if chart.chart_type is not None and "BAR" in str(chart.chart_type) or "COLUMN" in str(chart.chart_type)]
    assert bars and all(chart.value_axis.minimum_scale == 0 for chart in bars)
