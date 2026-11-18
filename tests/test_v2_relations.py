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


def test_a_grid_of_ten_does_not_draw_96_8_as_a_whole():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic
    content = {"highlight": {"value": "96.8%", "text": "光纤端口占比"}}
    page = ('<div class="page"><div class="r"><div class="units">' + '<i class="u on"></i>' * 10 + '</div></div></div>')
    assert [d.kind for d in find_graphic(page, content)] == ["unbound_graphic"]
    content = {"highlight": {"value": "60%", "text": "合成数据占比"}}
    page = ('<div class="page"><div class="r"><div class="units">' + '<i class="u on"></i>' * 6 + '<i class="u"></i>' * 4 + '</div></div></div>')
    assert find_graphic(page, content) == []


def test_a_display_figure_keeps_its_unit_on_its_line():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic
    content = {"highlight": {"value": "3162万个", "text": "千兆端口"}}
    apart = ('<div class="page"><div class="r"><div class="d1">3162</div></div>'
             '<div class="r"><p class="k">万个</p></div></div>')
    together = '<div class="page"><div class="r"><div class="rowx"><div class="d1">3162</div><div class="unit">万个</div></div></div></div>'
    assert "split_unit" in [d.kind for d in find_graphic(apart, content)]
    assert "split_unit" not in [d.kind for d in find_graphic(together, content)]


def test_part_of_a_unit_left_behind_is_a_split():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic
    content = {"highlight": {"value": "483.8万个", "text": "5G基站"}}
    apart = '<div class="page"><div class="r"><div class="d1">483.8万</div></div><div class="r"><p class="c">个</p></div></div>'
    together = '<div class="page"><div class="r"><div class="rowx"><div class="d1">483.8万</div><div class="unit">个</div></div></div></div>'
    assert "split_unit" in [d.kind for d in find_graphic(apart, content)]
    assert "split_unit" not in [d.kind for d in find_graphic(together, content)]


def test_an_arrow_inside_wrappers_between_different_units_is_caught():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic
    page = ('<div class="page"><div class="r"><div class="rowx base">'
            '<div class="g2"><div class="rowx base"><div class="d4">4198亿</div><div class="unit">个</div></div></div>'
            '<div class="g1"><div class="t3 red">→</div></div>'
            '<div class="g2"><div class="rowx base"><div class="d4 red">3958亿</div><div class="unit">GB</div></div></div>'
            '</div></div></div>')
    content = {"items": [{"value": "4198亿个"}, {"value": "3958亿GB"}]}
    assert "unbound_graphic" in [d.kind for d in find_graphic(page, content)]


def test_a_bar_labelled_as_something_else_than_its_share_is_caught():
    from ppt_agent.v2.htmldeck.graphic_checks import find as find_graphic
    content = {"highlight": {"value": "11.25亿", "text": "网民规模"},
               "items": [{"heading": "互联网普及率", "value": "80.1%", "body": "互联网普及率为80.1%"}]}
    wrong = ('<div class="page"><div class="r"><div class="c">截至2025年12月我国网民规模</div>'
             '<div class="strip"><div class="fill red" style="width:80.1%"></div></div></div></div>')
    right = wrong.replace("我国网民规模", "我国互联网普及率")
    assert "unbound_graphic" in [d.kind for d in find_graphic(wrong, content)]
    assert "unbound_graphic" not in [d.kind for d in find_graphic(right, content)]
