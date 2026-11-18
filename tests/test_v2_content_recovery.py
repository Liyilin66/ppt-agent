from ppt_agent.v2 import content_recovery
from ppt_agent.v2.evidence import EvidenceStore


def store(*pages):
    return EvidenceStore([{'doc_id': 'doc1', 'name': 'r.pdf', 'page_kind': 'PDF',
                           'pages': [{'page': i + 1, 'text': t} for i, t in enumerate(pages)]}])


def record(values, evidence=''):
    return {'evidence': {'text': evidence},
            'numeric_check': {'failures': [{'attempt': 3, 'issues': [{'value': v} for v in values]}]}}


def test_value_printed_elsewhere_is_a_citation_mismatch():
    diag = content_recovery.diagnose(record(['3450万']), store('无关', '新增用户3450万人'))
    assert diag['values'] == {'3450万': 'CITATION_MISMATCH'} and diag['recoverable']
    assert content_recovery.pages_to_add(diag) == {'r.pdf': {2}}


def test_bare_chart_labels_are_not_text_facts():
    chart = store('图 142.5\n231.2\n337.7\n425.1\n483.8 单位（万个）', '比例 1142.57')
    diag = content_recovery.diagnose(record(['142.5万', '231.2万', '425.1万']), chart)
    assert set(diag['values'].values()) == {'CHART_LABEL_ONLY'}
    assert content_recovery.pages_to_add(diag) == {}  # nothing to add: the number is no quotable fact
    assert '裸数值' in content_recovery.instruction(diag)
    # one decimal inside another number on a page with none of the others is chance
    alone = content_recovery.diagnose(record(['142.5万', '999.9万']), store('比例 1142.57'))
    assert set(alone['values'].values()) == {'UNSUPPORTED'}


def test_sum_of_two_figures_is_computed():
    diag = content_recovery.diagnose(record(['1183'], evidence='企业 748 家，机构 435 家'), store('企业748家'))
    assert diag['values'] == {'1183': 'COMPUTED'} and content_recovery.pages_to_add(diag) == {}
    assert '自己计算' in content_recovery.instruction(diag)


def test_page_holding_most_figures_wins():
    diag = content_recovery.diagnose(record(['17.5%', '9.7%']), store('9.7%', '17.5% 与 9.7%'))
    assert content_recovery.pages_to_add(diag) == {'r.pdf': {2}}


def test_placeholder_title_takes_planned_claim():
    assert content_recovery.title_for({'claim': '应用竞争转向具体场景'}, '小节') == '应用竞争转向具体场景'
    assert content_recovery.title_for({'claim': '规模达11亿'}, '小节') == '小节'
    assert content_recovery.title_for(None, '小节') == '小节'


def test_summary_groups_by_status():
    out = content_recovery.summary({1: {}, 2: {'content_status': 'CONTENT_RECOVERED', 'content_failure': {'x': 1}}})
    assert out['by_status']['CONTENT_VALID'] == [1] and out['by_status']['CONTENT_RECOVERED'] == [2]


def test_a_moved_page_does_not_bring_its_old_topic_back():
    diag = content_recovery.diagnose(record(['3748万']), store('外卖增长3748万人'), moved=True)
    assert diag['values'] == {'3748万': 'OFF_TOPIC'} and content_recovery.pages_to_add(diag) == {}
    assert '不要再讲原来的主题' in content_recovery.instruction(diag)


def test_renumber_changes_only_the_footer_number():
    from ppt_agent.v2.htmldeck.deck import renumber
    from ppt_agent.v2.ir import PageDesign
    page = PageDesign.model_validate({'page_number': 18, 'role': 'content', 'elements': [
        {'type': 'rich_text', 'id': 'a', 'frame': {'x': 76, 'y': 300, 'w': 300, 'h': 40}, 'runs': [{'text': '18'}]},
        {'type': 'rich_text', 'id': 'b', 'frame': {'x': 1191, 'y': 679, 'w': 80, 'h': 17}, 'runs': [{'text': '18'}]}]})
    out = renumber(page, 17)
    assert [e.runs[0].text for e in out.elements] == ['18', '17']
