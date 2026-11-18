from ppt_agent.v2 import completeness
from ppt_agent.v2.htmldeck import charts
from ppt_agent.v2.visual.content import DataSeries


def _series(pairs, quote="{label} {display}", kind="category"):
    return DataSeries(metric="备案数量", unit="家", kind=kind, points=[
        {"label": label, "value": value, "display": f"{value}家",
         "quote": quote.format(label=label, display=f"{value}家"), "ref": "r.pdf 第24页"} for label, value in pairs])


def test_a_series_whose_figure_has_more_members_is_an_excerpt(monkeypatch):
    from ppt_agent.v2 import figure_text
    monkeypatch.setattr(figure_text, "axes", lambda page: [[(name, None) for name in ["北京", "广东", "上海", "浙江", "江苏"]]])
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


def test_an_item_showing_a_series_value_takes_the_source_category_name():
    from ppt_agent.v2.typeset_pipeline import name_categories
    from ppt_agent.v2.visual.content import RichContent

    content = RichContent(title="生活助手使用率达30.0%", source="r.pdf 第17页", items=[
        {"heading": "办公提效", "value": "29.7%", "body": "会议纪要类用途"},
        {"heading": "休闲娱乐", "value": "23.6%", "body": "娱乐用途"}])
    series = DataSeries(metric="用途", unit="%", kind="category", points=[
        {"label": "生成会议纪要、PPT", "value": 29.7, "display": "29.7%", "quote": "生成会议纪要、PPT 29.7%", "ref": "r.pdf 第17页"},
        {"label": "休闲娱乐", "value": 23.6, "display": "23.6%", "quote": "休闲娱乐 23.6%", "ref": "r.pdf 第17页"}])
    fixed, renamed = name_categories(content, [series])
    assert fixed.items[0].heading == "生成会议纪要、PPT" and fixed.items[1].heading == "休闲娱乐"
    assert renamed == [{"value": "29.7%", "was": "办公提效", "now": "生成会议纪要、PPT"}]


def test_the_figure_value_replaces_a_text_approximation_and_names_match_without_the_province_word(monkeypatch):
    from ppt_agent.v2 import figure_text
    axis = [("北京", "158"), ("广东", "92"), ("上海", "92"), ("浙江", "43"), ("江苏", "39"), ("山东", "17")]
    monkeypatch.setattr(figure_text, "axes", lambda page: [axis])
    monkeypatch.setattr("pypdf.PdfReader", lambda path: type("R", (), {"pages": [None] * 30})())
    series = DataSeries(metric="备案数量", unit="家", kind="category", points=[
        {"label": "北京", "value": 158, "display": "158家", "quote": "北京数量最多，达到158家", "ref": "r.pdf 第24页"},
        {"label": "浙江省", "value": 40, "display": "40家左右", "quote": "浙江省与江苏省的备案数量均在40家左右", "ref": "r.pdf 第24页"},
        {"label": "江苏省", "value": 40, "display": "40家左右", "quote": "浙江省与江苏省的备案数量均在40家左右", "ref": "r.pdf 第24页"}])
    found = completeness.assess(series, {"r.pdf": "r.pdf"})
    zhejiang = found.points[1]
    assert (zhejiang.label, zhejiang.value, zhejiang.display, zhejiang.stated) == ("浙江省", 43.0, "43家", "40家左右")
    assert found.coverage == "excerpt" and found.of_total == 6
    assert "浙江省正文作「40家左右」" in charts.caption(found.model_dump())
    assert "texts" not in charts.spec(found.model_dump())


def test_an_approximation_left_standing_is_labelled_on_its_bar():
    series = _series([("北京", 158), ("上海", 92)]).model_dump()
    series["points"].append({"label": "浙江", "value": 40, "display": "约40家", "quote": "q", "ref": "r"})
    assert charts.spec(series)["texts"] == ["158家", "92家", "约40家"]
