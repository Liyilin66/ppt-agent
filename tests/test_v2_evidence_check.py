from ppt_agent.v2.evidence_check import check_content_numbers, sanitize_content_numbers
from ppt_agent.v2.visual.content import MetricsContent, StatementContent, ChartContent


def metric(value='30.0%'):
    return MetricsContent(title='用途', source='报告.pdf 第 18 页', metrics=[
        {'value': value, 'label': '会议纪要/PPT'}, {'value': '80.9%', 'label': '回答问题'}])


def test_large_page_seven_wrong_metric_is_rejected():
    text='回答问题 80.9%\n作为生活助手 30.0%\n生成会议纪要、PPT 29.7%'
    issues=check_content_numbers(metric(), text)
    assert [i['path'] for i in issues] == ['metrics.0.value']
    assert issues[0]['reason'] == 'metric_mismatch'
    assert check_content_numbers(metric('29.7%'), text) == []


def test_decimal_normalization_and_number_boundaries():
    assert not check_content_numbers(StatementContent(title='规模', statement='47.10%'), '比例 47.1%')
    assert check_content_numbers(StatementContent(title='规模', statement='1亿'), '31亿')
    assert check_content_numbers(StatementContent(title='规模', statement='1亿'), '1万')


def test_footer_and_notes_are_not_page_numbers():
    content=StatementContent(title='结论', statement='定性结论', source='报告2025.pdf 第 18 页', speaker_notes='999')
    assert not check_content_numbers(content, '')


def test_chart_values_are_bound_to_categories():
    content=ChartContent(title='用途', chart_title='用户用途', categories=['会议纪要/PPT','回答问题'], values=[30,80.9], unit_format='0.0"%"', insights=[{'text':'用途多样'}])
    assert check_content_numbers(content,'生活助手 30.0%\n会议纪要/PPT 29.7%\n回答问题 80.9%')[0]['path']=='values.0'


def test_sanitize_preserves_good_neighbor_and_valid_schema():
    text='会议纪要/PPT 29.7%\n回答问题 80.9%'
    content=metric()
    cleaned, removed=sanitize_content_numbers(content,check_content_numbers(content,text))
    assert removed and '30.0' not in cleaned.model_dump_json()
    assert '80.9' in cleaned.model_dump_json()
    assert not check_content_numbers(cleaned,text)


def test_inline_bad_number_removed_without_erasing_valid_number():
    content=StatementContent(title='结论',statement='正确47.1%，错误99.3%')
    clean,removed=sanitize_content_numbers(content,check_content_numbers(content,'47.1%'))
    assert '47.1%' in clean.statement and '99.3' not in clean.statement


def test_packet_checks_only_cited_page():
    from ppt_agent.v2.evidence import EvidencePacket
    packet=EvidencePacket(text='[报告.pdf | PDF p18]\n回答问题 80.9%\n[报告.pdf | PDF p19]\n会议纪要/PPT 29.7%',references=['报告.pdf'])
    issues=check_content_numbers(metric('29.7%'),packet)
    assert [i['path'] for i in issues]==['metrics.0.value']
    assert issues[0]['reason']=='number_missing'


def test_packet_without_citation_cannot_certify_number():
    from ppt_agent.v2.evidence import EvidencePacket
    page=StatementContent(title='结论',statement='47.1%')
    packet=EvidencePacket(text='[报告.pdf | PDF p18]\n比例47.1%',references=['报告.pdf'])
    assert check_content_numbers(page,packet)[0]['reason']=='number_missing'


def test_web_packet_accepts_only_cited_url():
    from ppt_agent.v2.evidence import EvidencePacket
    page=StatementContent(title='结论',statement='47.1%',source='https://example.com/report')
    packet=EvidencePacket(text='[https://example.com/report | web 报告]\n比例47.1%\n[https://example.org/other | web 报告]\n18.8%',references=['https://example.com/report','https://example.org/other'])
    assert not check_content_numbers(page,packet)
    page.statement='18.8%'
    assert check_content_numbers(page,packet)


def test_all_visible_archetype_fields_checked():
    from pydantic import TypeAdapter
    from ppt_agent.v2.visual.content import ArchetypeContent
    adapter=TypeAdapter(ArchetypeContent)
    variants=[
      {'archetype':'points','items':[{'heading':'指标90','body':'结论91'},{'heading':'定性','body':'内容'}]},
      {'archetype':'process','steps':[{'label':'步骤','body':'内容92'},{'label':'步骤','body':'内容'},{'label':'步骤','body':'内容'}]},
      {'archetype':'compare','left':{'heading':'过去93','points':['变化94','变化']},'right':{'heading':'现在','points':['变化','变化']}},
      {'archetype':'timeline','milestones':[{'date':'2024','label':'启动95','body':'内容96'},{'date':'未来','label':'计划'},{'date':'未来','label':'交付'}]},
      {'archetype':'statement','statement':'结论97','support':'支持98'},
    ]
    for variant in variants:
        page=adapter.validate_python({'title':'标题99','kicker':'章节100','lead':'引言101','takeaway':'总结102',**variant})
        issues=check_content_numbers(page,'')
        assert {i['path'] for i in issues}>={'title','kicker','lead','takeaway'}
        cleaned,_=sanitize_content_numbers(page,issues)
        assert not check_content_numbers(cleaned,'')


def test_numeric_chart_removal_preserves_valid_neighbor():
    page=ChartContent(title='用途',chart_title='指标',categories=['会议纪要/PPT','回答问题'],values=[30,80.9],unit_format='0.0"%"',insights=[{'text':'用途多样'}])
    text='会议纪要/PPT 29.7%\n回答问题 80.9%'
    clean,_=sanitize_content_numbers(page,check_content_numbers(page,text))
    assert not check_content_numbers(clean,text)
    assert '30' not in clean.model_dump_json()


def test_pdf_category_value_on_adjacent_lines_is_supported():
    text='作为生活助手\n30.0%\n生成会议纪要、PPT\n29.7%\n回答问题\n80.9%'
    assert check_content_numbers(metric('29.7%'),text)==[]
    assert check_content_numbers(metric('30.0%'),text)[0]['reason']=='metric_mismatch'


def test_thousand_separator_equivalence_keeps_units():
    page=StatementContent(title='规模',statement='1,234万')
    assert not check_content_numbers(page,'总量1234万')
    assert check_content_numbers(page,'总量1234亿')


def test_source_page_range_is_scoped():
    from ppt_agent.v2.evidence import EvidencePacket
    page=StatementContent(title='结论',statement='47.1%',source='报告.pdf 第 18-19 页')
    packet=EvidencePacket(text='[报告.pdf | PDF p19]\n比例47.1%\n[报告.pdf | PDF p20]\n比例18.8%',references=['报告.pdf'])
    assert not check_content_numbers(page,packet)
    page.statement='18.8%'
    assert check_content_numbers(page,packet)


def test_amount_and_event_rate_are_different_metric_keywords():
    from ppt_agent.v2.visual.content import MetricsContent
    c=MetricsContent(title='融资结构',metrics=[{'value':'38.7%','label':'机器人融资金额'}, {'value':'35.5%','label':'机器人融资金额'}])
    issues=check_content_numbers(c,'机器人融资事件38.7%\n机器人融资金额占比35.5%')
    assert any(i['path']=='metrics.0.value' and i['reason']=='metric_mismatch' for i in issues)
    assert not any(i['path']=='metrics.1.value' for i in issues)


def _claim(value, label):
    return MetricsContent(title='指标', metrics=[{'value': value, 'label': label}, {'value': '定性', 'label': '说明'}])


def test_paraphrased_labels_from_unseen_report_are_supported():
    # Real CAICT 2024 evidence: PDF line breaks split words and labels paraphrase.
    defect = '如TCL通过视觉技术实现液晶面板缺陷检 测，准确率超 90%、生产周期缩短了 60%。'
    assert check_content_numbers(_claim('90%', '缺陷检测准确率'), defect) == []
    assert check_content_numbers(_claim('90%', '质检准确率'), defect) == []
    assert check_content_numbers(_claim('60%', '准确率'), defect)[0]['reason'] == 'metric_mismatch'
    assert check_content_numbers(_claim('70%', '研发成本降幅'), '使先导药 的研发周期从数年缩短至数月，研发成本降低约 70%。') == []
    assert check_content_numbers(_claim('66%', '协调对象增加'), '实现制 造产线优化，协调对象的数量增加 66%，运动次数减少 11%。') == []
    assert check_content_numbers(_claim('66%', '调度优化'), '实现制 造产线优化，协调对象的数量增加 66%，运动次数减少 11%。')
    scale = '据IDC 预测，2024年全球人 工智能产业规模将达到 6233亿美元，同比增长 21.5%。'
    assert check_content_numbers(_claim('6233亿', '2024年产业规模'), scale) == []


def test_labels_with_their_own_digits_bind_to_the_table_value():
    text = '表 1 语言大模型演进迭代情况 公司 模型 上下文长度 Meta AI Llama 2 8k Llama 3.1 128k OpenAI GPT-4 32k GPT-4 Turbo 128k'
    chart = ChartContent(title='上下文', chart_title='上下文长度', categories=['Llama 2', 'Llama 3.1', 'GPT-4'],
                         values=[8, 128, 32], insights=[{'text': '持续扩展'}])
    assert check_content_numbers(chart, text) == []
    wrong = chart.model_copy(update={'values': [8, 32, 128]})
    assert {i['path'] for i in check_content_numbers(wrong, text)} == {'values.1', 'values.2'}


def test_number_removal_never_leaves_instructional_placeholder():
    page = MetricsContent(title='产业规模', lead='全球产业保持增长', metrics=[
        {'value': '99%', 'label': '编造指标'}, {'value': '98%', 'label': '编造指标二'}])
    clean, _ = sanitize_content_numbers(page, check_content_numbers(page, ''))
    text = clean.model_dump_json()
    assert '审阅' not in text and '请核实' not in text
    assert clean.statement == '全球产业保持增长'


def test_value_first_prose_declines_and_footnotes():
    assert check_content_numbers(_claim('552', '二氧化碳排放'), 'GPT-3模型能耗相当于 1287兆瓦时的电力，还产生了552吨二氧化碳排放15。') == []
    assert check_content_numbers(_claim('10亿', '被劫持算力'), '数千家网络服务器遭受攻击，超过 10亿美元算力遭到“劫持”12。') == []
    assert check_content_numbers(_claim('-11%', '运动次数'), '协调对象的数量增加 66%，运动次数减少 11%。') == []
    assert check_content_numbers(_claim('-66%', '协调对象数量'), '协调对象的数量增加 66%，运动次数减少 11%。')
    # Clause text that runs into the next value is that value's label.
    assert check_content_numbers(_claim('30%', '使用B'), '30%的用户使用A，29.7%的用户使用B。')


def test_retry_feedback_quotes_the_source_wording():
    from ppt_agent.v2.evidence_check import number_contexts
    lines = number_contexts([{'value': '80%', 'label': '企业采用率'}],
                            '据Gartner预测，到2026年，超过 80%的企业将使用生成式人工智能 API。')
    assert lines and '80%的企业将使用生成式人工智能' in lines[0]


def test_time_series_and_shared_subject_values():
    text = ('受益于大模型发展，人工智能领\n域融资占全行业融资比例持续上升，从 2022年的 4.5%上升至 2024\n'
            '年上半年的 12.1%。2023年，生成式人工智能投融资规模达 252 亿美元。')
    chart = ChartContent(title='融资占比', chart_title='AI融资占比', categories=['2022年', '2024年上半年'],
                         values=[4.5, 12.1], unit_format='0.0"%"', insights=[{'text': '占比上升'}])
    assert check_content_numbers(chart, text) == []
    swapped = chart.model_copy(update={'values': [12.1, 4.5]})
    assert {i['path'] for i in check_content_numbers(swapped, text)} == {'values.0', 'values.1'}
    assert check_content_numbers(_claim('12.1%', '人工智能融资占比'), text) == []
    assert check_content_numbers(_claim('12.1%', '投融资规模'), text)
    # A list is not a shared subject.
    assert check_content_numbers(_claim('30.0%', '回答问题'), '回答问题 80.9%，作为生活助手 30.0%。')


def test_chart_fallback_support_reads_as_phrases():
    page = ChartContent(title='融资', chart_title='融资占比', categories=['2022年', '2024年上半年', '2025年'],
                        values=[4.5, 12.1, 99], unit_format='0.0"%"', insights=[{'text': '占比持续上升'}])
    clean, _ = sanitize_content_numbers(page, [{'path': 'values.1', 'value': '12.1', 'label': ''},
                                               {'path': 'values.2', 'value': '99', 'label': ''}])
    assert clean.archetype == 'statement'
    assert clean.support == '2022年 4.5%；占比持续上升'


def test_measure_words_bind_the_counted_noun():
    text = '截至8月，国内已有近1919个深度合成算法、190个生成式人工智能服务在国家网信办完成备案。'
    assert check_content_numbers(_claim('1919', '深度合成算法'), text) == []
    assert check_content_numbers(_claim('190', '生成式AI服务'), text) == []
    assert check_content_numbers(_claim('1919', '生成式AI服务'), text)
    assert check_content_numbers(_claim('1.1万', '打击目标'), '如以色列军队利用人工智能技术打击了加沙地带1.1万多个目标，一天内发现并摧毁了150个隧道14。') == []
    assert check_content_numbers(_claim('1287', '兆瓦时能耗'), '1750亿个参数的GPT-3模型能耗相当于1287兆瓦时的电力，还产生了552吨二氧化碳15。') == []
    assert check_content_numbers(_claim('4.5%', '全行业融资占比'), '人工智能领域融资占全行业融资比例持续上升，从2022年的4.5%上升至2024年上半年的12.1%。') == []
