from ppt_agent.v2 import relations as rel

CNNIC = ("截至2025年6月，我国生成式人工智能用户规模达5.15亿人，较2024年12月增长2.66亿人；"
         "普及率达到36.5%，较2024年12月提升18.8个百分点。2024年12月普及率为17.7%。"
         "全球人工智能产业规模同比增长21.5%，达到6233亿美元。")


def test_classes_come_from_the_words_before_a_figure():
    assert rel.classify("5.15亿人", CNNIC) == "total"
    assert rel.classify("2.66亿人", CNNIC) == "increment"
    assert rel.classify("36.5%", CNNIC) == "share"
    assert rel.classify("18.8个百分点", CNNIC) == "points"
    assert rel.classify("21.5%", CNNIC) == "growth"
    assert rel.classify("99.9%", CNNIC) is None


def test_an_arrow_joins_only_one_kind_of_quantity():
    assert rel.check_link("17.7%", "36.5%", CNNIC) is None
    assert "增量" in rel.check_link("2.66亿人", "5.15亿人", CNNIC)       # the P2 error
    assert "百分点" in rel.check_link("18.8个百分点", "36.5%", CNNIC)
    assert "增长率" in rel.check_link("21.5%", "6233亿美元", CNNIC)


def test_doubling_and_multiples_must_be_true_or_stated():
    assert rel.check_text("普及率半年翻倍：17.7%→36.5%", CNNIC, where="标题") == []
    assert rel.check_text("用户规模翻倍至5.15亿人", CNNIC, where="标题")          # nothing on the page doubles
    assert rel.check_text("普及率是半年前的3倍，达36.5%", CNNIC + "17.7%", where="标题")


def test_a_cause_the_source_does_not_give_is_flagged():
    assert rel.check_text("普及率提升带动用户增长2.66亿人", CNNIC, where="标题")
    assert rel.check_text("用户增长2.66亿人，普及率达到36.5%", CNNIC, where="标题") == []


def test_content_check_reads_title_highlight_and_items():
    content = {"title": "用户规模达5.15亿人", "highlight": {"value": "2.66亿人", "text": "半年新增"},
               "items": [{"heading": "规模", "value": "2.66亿人→5.15亿人", "body": ""}]}
    problems = rel.check_content(content, CNNIC)
    assert len(problems) == 1 and problems[0].startswith("第 1 条")


def test_a_designed_arrow_between_unrelated_figures_is_a_defect():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic

    content = {"title": "用户规模达5.15亿人", "items": [{"value": "2.66亿人"}],
               "series": [{"points": [{"value": 17.7}, {"value": 36.5}]}]}
    bad = '<div class="page"><div class="r"><p class="b">半年新增 2.66亿人 → 5.15亿人</p></div></div>'
    good = '<div class="page"><div class="r"><p class="b">GPT-3.5→GPT-4；普及率 17.7% → 36.5%</p></div></div>'
    assert [d.kind for d in find_graphic(bad, content)] == ["unbound_graphic"]
    assert find_graphic(good, content) == []


def test_doubling_against_the_value_printed_beside_it_in_the_source():
    source = "普及率2024年12月为17.7%，2025年6月为36.5%。用户规模5.15亿人。"
    assert rel.check_text("普及率半年翻倍至36.5%", source, where="标题") == []
    assert rel.check_text("用户规模翻倍至5.15亿人", source, where="标题")


def test_display_figures_are_values_and_categories_keep_their_names():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic

    content = {"title": "安全测试覆盖25家模型", "highlight": {"value": "25家"}, "items": [{"value": "50余万条", "ref": "第49-50页"}],
               "series": [{"points": [{"label": "生成会议纪要、PPT", "value": 29.7, "display": "29.7%"},
                                      {"label": "休闲娱乐", "value": 23.6, "display": "23.6%"}]}]}
    page = ('<div class="page"><div class="r"><div class="d1">25家</div></div>'
            '<div class="r"><div class="d3">第 49–50 页</div></div>'
            '<div class="r"><p class="h">办公提效</p><p class="d3">29.7%</p></div>'
            '<div class="r"><p class="h">休闲娱乐</p><p class="d3">23.6%</p></div></div>')
    messages = [d.message for d in find_graphic(page, content)]
    assert any("第49–50页" in m for m in messages)
    assert any("生成会议纪要、PPT" in m for m in messages)
    assert not any("休闲娱乐" in m or "「25家」" in m for m in messages)
