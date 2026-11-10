from ppt_agent.v2.htmldeck import signature
from ppt_agent.v2.htmldeck.ledger import FactLedger, fact_keys, page_facts
from ppt_agent.v2.ir import BleedFrame, BoxItem, ChartItem, ChartSeries, Frame, PageDesign, Paint, RichTextItem, TextRun


def box(x, y, w, h):
    return BoxItem(id=f"b{x}-{y}-{w}", frame=BleedFrame(x=x, y=y, w=w, h=h), fill=Paint(hex="112233"))


def page(*elements):
    text = RichTextItem(id="t", frame=BleedFrame(x=0, y=0, w=10, h=10), runs=[TextRun(text="x")])
    return PageDesign(page_number=1, elements=[*elements, text])


def test_signature_ignores_detail_and_backdrops_and_snaps_to_a_grid():
    a = page(box(0, 0, 1280, 720), box(64, 180, 560, 460), box(660, 180, 556, 460), box(70, 190, 40, 20))
    b = page(box(60, 176, 566, 470), box(655, 185, 560, 455))
    assert signature.of(a) == signature.of(b) == "box:0-3x1-4|box:3-6x1-4"
    chart = ChartItem(id="c", frame=Frame(x=64, y=180, w=700, h=460), chart="bar",
                      categories=["a", "b"], series=[ChartSeries(name="s", values=[1, 2])])
    assert signature.of(page(chart)).startswith("chart:0-")
    assert signature.of(page()) == "text"


def test_repetition_is_measured_within_a_window_and_overall():
    report = signature.conflicts(["a", "b", "a", "c", "d", "e", "f", "a"] + ["z"] * 2, window=5, max_share=0.25)
    assert report["repeats_within_window"] == [2, 9]
    assert report["overused"] == {"a": 3} and report["largest_share"] == 0.3
    assert report["distinct"] == 7


def test_facts_keep_their_unit_and_skip_years_and_small_counts():
    assert fact_keys("2024 年规模 6,233 亿美元，同比 +21.5%，分 3 步，另有 95 %") == ["6233亿", "21.5%", "95%"]


def test_a_page_leads_with_its_title_and_headline_figure():
    lead, support = page_facts({"title": "采购自动化率超 85%", "source": "第 43 页",
                                "metrics": [{"value": "95%", "label": "现货率"}, {"value": "30天", "label": "周转"}],
                                "takeaway": "对比 2022 年的 70%"})
    assert lead == {"85%", "95%"} and support == {"70%"}


def test_the_ledger_reports_overuse():
    ledger = FactLedger()
    assert ledger.register(3, {"title": "现货率 95%"}) == []
    assert ledger.register(7, {"title": "现货率达到 95%", "items": [{"body": "成本降 70%"}]}) == ["95%"]
    for number in (8, 9, 10):
        ledger.register(number, {"title": "其他", "items": [{"body": "成本降 70%"}]})
    audit = ledger.audit()
    assert audit["led_more_than_once"] == {"95%": [3, 7]}
    assert audit["supported_more_than_twice"] == {"70%": [7, 8, 9, 10]}
    assert audit["most_used"] == {"fact": "70%", "pages": [7, 8, 9, 10]}
