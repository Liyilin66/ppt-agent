from ppt_agent.v2.text_units import MEASURE, join_lines, sentences, weight


def test_english_lines_keep_their_spaces_and_chinese_lines_do_not():
    assert join_lines("the electric\ncar market") == "the electric car market"
    assert join_lines("较2024 年12 月增长1717\n万人") == "较2024 年12 月增长1717万人"


def test_english_sentences_split_at_full_stops_but_not_decimals():
    text = "Sales rose 35% to 17.3 million in 2024. China accounted for almost two-thirds."
    assert sentences(text) == ["Sales rose 35% to 17.3 million in 2024.", "China accounted for almost two-thirds."]


def test_english_measures():
    found = MEASURE.findall("sales of 17 million cars, 1.3 TWh of batteries, USD 30 000 and 40%")
    assert [f.strip() for f in found] == ["17 million", "1.3 TWh", "USD 30 000", "40%"]


def test_weight_puts_both_languages_on_one_scale():
    assert weight("我国网民规模达十一亿人") == 11
    assert weight("Electric car sales grew strongly") == 10


def test_series_read_english_figures_and_forecasts():
    from ppt_agent.v2.data_series import _FORECAST, _WHEN, _number, _squash

    assert _number("23 000 2023 USD") == ("23000", "2023USD")
    assert _FORECAST.search(_squash("Electric car sales are set to exceed 20 million in 2025"))
    assert _WHEN.search("Q1 2025") and _WHEN.search("first quarter")


def test_a_currency_before_the_amount_is_its_unit():
    from ppt_agent.v2.data_series import verify

    text = "[gevo.pdf | report p50]\\nIn 2024 the truck cost USD 40 400, and in 2030 it is expected to cost USD 31 200. In 2027 it costs USD 35 000."
    text = text.replace("\\\\n", "\\n")
    raw = [{"kind": "time", "metric": "truck cost", "unit": "USD", "points": [
        {"label": "2024", "display": "40 400", "value": 40400, "quote": "In 2024 the truck cost USD 40 400"},
        {"label": "2027", "display": "35 000", "value": 35000, "quote": "In 2027 it costs USD 35 000"},
        {"label": "2030", "display": "31 200", "value": 31200, "projected": True,
         "quote": "in 2030 it is expected to cost USD 31 200"}]}]
    kept, rejected = verify(raw, text)
    assert not [r for r in rejected if "单位" in r["why"] or "数字和单位" in r["why"]], rejected


def test_financial_shorthand_is_a_measure_and_a_magnitude():
    from ppt_agent.v2.evidence_check import _NUMBER, _magnitude

    assert MEASURE.findall("800MM users, 5.5B searches and 27K startups") == ["800MM", "5.5B", "27K"]
    assert _magnitude(_NUMBER.search("800MM").group()) == _magnitude("8亿")
    assert not MEASURE.findall("a 3 m wide lane")
