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
