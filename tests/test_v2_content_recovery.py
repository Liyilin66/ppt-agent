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


def test_glued_chart_labels_count_only_together():
    glued = store('图 142.5231.2337.7425.1483.8', '比例 1142.57')
    diag = content_recovery.diagnose(record(['142.5万', '231.2万', '425.1万']), glued)
    assert diag['recoverable'] and content_recovery.pages_to_add(diag) == {'r.pdf': {1}}
    # one decimal inside another number on a page with none of the others is chance
    alone = content_recovery.diagnose(record(['142.5万', '999.9万']), store('比例 1142.57'))
    assert not alone['recoverable']


def test_sum_of_two_figures_is_computed_and_not_recovered():
    diag = content_recovery.diagnose(record(['1183'], evidence='企业 748 家，机构 435 家'), store('企业748家'))
    assert diag['values'] == {'1183': 'COMPUTED'} and not diag['recoverable']


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
