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
