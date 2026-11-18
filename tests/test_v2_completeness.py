from ppt_agent.v2 import completeness
from ppt_agent.v2.htmldeck import charts
from ppt_agent.v2.visual.content import DataSeries


def _series(pairs, quote="{label} {display}", kind="category"):
    return DataSeries(metric="备案数量", unit="家", kind=kind, points=[
        {"label": label, "value": value, "display": f"{value}家",
         "quote": quote.format(label=label, display=f"{value}家"), "ref": "r.pdf 第24页"} for label, value in pairs])


def test_a_series_whose_figure_has_more_members_is_an_excerpt(monkeypatch):
    from ppt_agent.v2 import figure_text
    monkeypatch.setattr(figure_text, "axis_labels", lambda page: [["北京", "广东", "上海", "浙江", "江苏"]])
    monkeypatch.setattr("pypdf.PdfReader", lambda path: type("R", (), {"pages": [None] * 30})())
    top = completeness.assess(_series([("北京", 158), ("上海", 92), ("广东", 92)]), {"r.pdf": "r.pdf"})
    assert top.coverage == "excerpt" and top.of_total == 5
    assert "原图共 5 项" in charts.caption(top.model_dump())
    whole = completeness.assess(_series([("北京", 158), ("广东", 92), ("上海", 92), ("浙江", 43), ("江苏", 39)]),
                                {"r.pdf": "r.pdf"})
    assert whole.coverage == "complete"


def test_quotes_that_pick_out_members_make_an_excerpt_and_never_a_share():
    quote = "北京数量最多，达到{display}"
    picked = completeness.assess(DataSeries(metric="占比", unit="%", kind="share", points=[
        {"label": "北京", "value": 38.2, "display": "38.2%", "quote": "北京最多，占38.2%", "ref": "r.md 第1页"},
        {"label": "广东", "value": 18.7, "display": "18.7%", "quote": "其次为广东，占18.7%", "ref": "r.md 第1页"}]), {})
    assert picked.coverage == "excerpt" and picked.kind == "category"
