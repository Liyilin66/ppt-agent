import json
from pathlib import Path

import pytest

from ppt_agent.v2.htmldeck import graphic_checks, layout_plan, sanitize, themes

FIXTURES = Path(__file__).parent / "fixtures" / "studio20"
GROWTH = {"archetype": "rich", "relation": "trend", "highlight": {"value": "6233亿美元", "text": "IDC预测的2024年全球AI产业规模"},
          "items": [{"heading": "产业规模增速", "body": "IDC预测，2024年全球人工智能产业规模将同比增长21.5%。", "value": "21.5%"},
                    {"heading": "模型迭代活跃", "body": "2024年以来新增或迭代近百个。", "value": "近百个"}]}
SHARE = {"archetype": "rich", "relation": "trend", "highlight": {"value": "12.1%", "text": "AI融资占全行业融资比例"},
         "items": [{"heading": "2022年", "body": "人工智能领域融资占全行业融资比例为4.5%。", "value": "4.5%"},
                   {"heading": "准确率", "body": "TCL视觉质检准确率超90%。", "value": "超90%"}]}


def kinds(html, content):
    return [defect.kind for defect in graphic_checks.find(sanitize.clean(html)[0], content)]


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text("utf-8")


def test_a_growth_rate_is_not_a_share():
    assert graphic_checks.shares(GROWTH) == set()
    assert graphic_checks.shares(SHARE) == {12.1, 4.5, 90.0}
    assert "strip" not in layout_plan.candidates(GROWTH) and "units" not in layout_plan.candidates(GROWTH)
    assert "strip" in layout_plan.candidates(SHARE)


def test_the_page_that_drew_growth_as_a_share_is_caught():
    found = graphic_checks.find(sanitize.clean(fixture("failed_004"))[0], GROWTH)
    assert any(defect.kind == "unbound_graphic" and "21.5%" in defect.message for defect in found)


def test_columns_without_data_and_nested_regions_are_caught():
    found = kinds(fixture("failed_009"), {"archetype": "rich", "items": [{"heading": "高匹配度", "body": "语料丰富"}]})
    assert "unbound_graphic" in found and "unsupported" in found
    assert "unsupported" in kinds(fixture("page_011"), {})


@pytest.mark.parametrize("html, content, bad", [
    ('<div class="strip"><div class="fill red" style="width:12.1%"></div></div>', SHARE, False),
    ('<div class="strip"><div class="fill" style="width:30%"></div></div>', SHARE, True),
    ('<div class="units">' + '<div class="u on"></div>' * 9 + '<div class="u"></div></div>', SHARE, False),
    ('<div class="units">' + '<div class="u on"></div>' * 7 + '<div class="u"></div>' * 3 + "</div>", SHARE, True),
    ('<div class="cols"><div class="col" style="height:100%">研发设计落地快，数据质量高</div>'
     '<div class="col low" style="height:46%">生产制造落地慢，场景选择难</div></div>', {}, False),
    ('<div class="cols"><div class="col" style="height:100%">回报高</div><div class="col low">待探索</div></div>', {}, True),
    ('<div class="cols"><div class="col" style="height:100%">研发成本降低 70%，周期缩短</div>'
     '<div class="col" style="height:50%">采购自动化率 35%，覆盖主要品类</div>'
     '<div class="col" style="height:20%">质检环节提升 14%，仍在试点</div></div>', {}, False),
    ('<div class="cols"><div class="col" style="height:100%">研发成本降低 70%，周期缩短</div>'
     '<div class="col" style="height:80%">采购自动化率 35%，覆盖主要品类</div>'
     '<div class="col" style="height:60%">质检环节提升 14%，仍在试点</div></div>', {}, True),
], ids=["strip-share", "strip-invented", "units-ok", "units-wrong", "cols-two-levels", "cols-empty",
        "cols-proportional", "cols-disproportionate"])
def test_drawn_quantities_must_match_the_content(html, content, bad):
    assert ("unbound_graphic" in kinds(html, content)) is bad


def test_the_hand_written_samples_obey_the_rules():
    samples = {
        "strip": {"items": [{"heading": "融资占比", "body": "AI 融资占全行业融资比例 4.5%", "value": "4.5%"},
                            {"heading": "融资占比", "body": "AI 融资占全行业融资比例 12.1%", "value": "12.1%"}]},
        "units": {"highlight": {"value": "90%", "text": "面板缺陷检测准确率"}},
        "margin": {"series": [{"metric": "正确率", "unit": "%", "kind": "category", "points": [
            {"label": "GPT-3.5", "value": 48, "display": "48%", "ref": "r"}, {"label": "GPT-4", "value": 67, "display": "67%", "ref": "r"}]}]},
        "figure": {"figure": {"id": "sample.png", "caption": "图 1", "ratio": 1.7}},
        "chart": {"highlight": {"value": "约86倍"}, "series": [{"metric": "参数规模", "unit": "亿", "kind": "category", "points": [
            {"label": "GPT-3（2020）", "value": 1750, "display": "1750亿", "ref": "a.pdf 第15页"},
            {"label": "MiniCPM-2.4B（2024）", "value": 24, "display": "24亿", "ref": "a.pdf 第15页"}]}]},
    }
    for form in themes.STUDIO_FORMS:
        found = graphic_checks.find(sanitize.clean(themes.studio_example(form))[0], samples.get(form, {}))
        assert found == [], (form, [defect.message for defect in found])


def test_a_saved_plan_keeps_every_page_whose_form_still_suits_it():
    saved = [layout_plan.Fingerprint(page=4, form="strip", focus="right"),
             layout_plan.Fingerprint(page=5, form="ledger", focus="left", ratio="wide", core_visual="账本")]
    plan = layout_plan.repair(saved, {4: GROWTH, 5: SHARE})
    assert plan[1] == saved[1]
    assert plan[0].form in layout_plan.candidates(GROWTH) and plan[0].form not in ("strip", "ledger")


def test_regions_may_not_share_cells():
    def page(second_rows):
        return ('<div class="page"><div class="r" style="grid-column:1/8;grid-row:5/9"><div class="b">数字和说明</div></div>'
                f'<div class="r" style="grid-column:1/8;grid-row:{second_rows}"><div class="call">结论</div></div></div>')

    assert "unsupported" in kinds(page("8/9"), {}) and kinds(page("9/10"), {}) == []
    assert "unsupported" not in kinds('<div class="page"><div class="r" style="grid-column:1/7;grid-row:2/9">左</div>'
                                      '<div class="r" style="grid-column:7/13;grid-row:2/9">右</div></div>', {})


def test_an_arrow_between_two_figures_needs_them_to_be_one_measure():
    row = ('<div class="page"><div class="r" style="grid-column:1/8;grid-row:4/7"><div class="rowx base">'
           '<div class="d3">{}</div><div class="t3 red">→</div><div class="d2 red">{}</div></div></div></div>')
    series = [{"metric": "普及率", "unit": "%", "kind": "time", "points": [
        {"label": "2024年12月", "value": 17.7, "display": "17.7%", "ref": "r"},
        {"label": "2025年6月", "value": 36.5, "display": "36.5%", "ref": "r"}]}]
    assert kinds(row.format("17.7%", "36.5%"), {"series": series}) == []
    assert "unbound_graphic" in kinds(row.format("2.66亿", "5.15亿"), {"series": series})   # an increase is not a starting point
    assert kinds(row.format("2.66亿", "5.15亿"), {}) == ["unbound_graphic"]


def test_a_claimed_speed_up_must_show_its_figures():
    from ppt_agent.v2.htmldeck import graphic_checks
    content = {"title": "备案登记建立合规底座", "lead": "2025年新增服务与应用登记均明显提速",
               "items": [{"heading": "服务备案", "value": "748款"}, {"heading": "服务提速", "value": "87.4%"},
                         {"heading": "应用提速", "value": "214.3%"}]}
    dropped = '<div class="page"><div class="r" style="grid-column:1/13;grid-row:1/9"><div class="d1">748款</div></div></div>'
    shown = dropped.replace('748款</div>', '748款</div><div class="t3">87.4%</div>')
    assert any("提速" in d.message for d in graphic_checks.find(dropped, content))
    assert not any("提速" in d.message for d in graphic_checks.find(shown, content))
