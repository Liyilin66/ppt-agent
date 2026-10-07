from ppt_agent.v2.source_names import document_display_names, name_from_cover, short_name
from ppt_agent.v2.typeset_pipeline import _FOOTER_EMS, _ems, display_copy, display_ref, display_source
from ppt_agent.v2.visual.content import PointItem, PointsContent

CAICT = '中国信息通信研究院《人工智能发展报告（2024年）》'
NAMES = {'caict_ai_2024.pdf': CAICT}


def test_cover_name_joins_spaced_issuer_and_split_title():
    # pypdf text of the CAICT and CNNIC covers, line for line.
    assert name_from_cover(['No.202 409', '中 国 信 息 通 信 研 究 院', '2024年12月',
                            '人工智能发展报告', '(2024 年)']) == CAICT
    assert name_from_cover(['工信洞察系列之', ' ', '生成式人工智能应用发展', '报告（2025）',
                            ' ', '中国互联网络信息中心', '2025 年 10 月']) == \
        '中国互联网络信息中心《生成式人工智能应用发展报告（2025）》'


def test_cover_without_a_title_is_not_guessed():
    assert name_from_cover(['本报告由某机构编写，仅供参考。', '2024年']) is None


def test_names_fall_back_to_a_clean_filename(tmp_path):
    missing = tmp_path / 'q3_board-review.pdf'
    note = tmp_path / 'brief.md'
    note.write_text('# 季度经营回顾\n正文', encoding='utf-8')
    assert document_display_names([missing, note]) == {
        'q3_board-review.pdf': 'q3 board review', 'brief.md': '《季度经营回顾》'}


def test_footer_names_the_report_with_a_page_range():
    assert display_source('caict_ai_2024.pdf 第7-13页', {}, NAMES) == f'资料来源：{CAICT}第 7–13 页'


def test_footer_mentions_each_report_once():
    assert display_source('caict_ai_2024.pdf 第8页；caict_ai_2024.pdf 第10页；caict_ai_2024.pdf 第62-63页', {}, NAMES) \
        == f'资料来源：{CAICT}第 8、10、62–63 页'
    assert display_source('a.pdf 第 3 页；a.pdf 第 5 页', {}, {'a.pdf': 'a'}) == '资料来源：a 第 3、5 页'


def test_footer_shortens_names_before_it_overflows():
    titles = {f'https://www.example{i}.com/a': '一个非常非常长的网页标题，用来检查页脚会不会溢出到第二行'
              for i in range(2)}
    text = display_source('caict_ai_2024.pdf 第7-13页；' + '；'.join(titles), titles, NAMES)
    assert text == '资料来源：《人工智能发展报告（2024年）》第 7–13 页；example0.com；example1.com'
    assert _ems(text) <= _FOOTER_EMS
    one_site = display_source('caict_ai_2024.pdf 第7-13页；https://www.example0.com/a', titles, NAMES)
    # Dropping the issuer comes before dropping the web title.
    assert one_site.startswith('资料来源：《人工智能发展报告（2024年）》第 7–13 页；example0.com · ')


def test_many_web_sources_fall_back_to_site_names():
    urls = [f'https://www.site{i}.com/a' for i in range(3)] + ['https://zhuanlan.zhihu.com/p/1']
    titles = {url: '一篇标题很长很长很长很长的网页文章：深度解析与实践' for url in urls}
    assert display_source('；'.join(urls), titles) == \
        '资料来源：site0.com；site1.com；site2.com；zhuanlan.zhihu.com'


def test_card_ref_keeps_only_the_page_when_the_footer_names_the_report():
    assert display_ref('caict_ai_2024.pdf 第12页', NAMES, {'caict_ai_2024.pdf'}) == '第 12 页'
    assert display_ref('caict_ai_2024.pdf 第12页', NAMES, {'caict_ai_2024.pdf', 'b.pdf'}) == \
        '《人工智能发展报告（2024年）》第 12 页'
    assert display_ref('https://www.example.com/x/y', NAMES, set()) == 'example.com'
    assert display_ref('caict_ai_2024.pdf 第9页、第12页', NAMES, {'caict_ai_2024.pdf'}) == '第 9、12 页'


def _points(*refs):
    return PointsContent(title='全球产业', source='caict_ai_2024.pdf 第7-13页', items=[
        PointItem(heading=f'要点{i}', body='正文', ref=ref) for i, ref in enumerate(refs)])


def test_identical_card_refs_are_dropped_and_distinct_ones_kept():
    same = display_copy(_points('caict_ai_2024.pdf 第12页', 'caict_ai_2024.pdf 第12页'), {}, NAMES)
    assert [item.ref for item in same.items] == [None, None]
    assert same.source == f'资料来源：{CAICT}第 7–13 页'
    distinct = display_copy(_points('caict_ai_2024.pdf 第8页', 'caict_ai_2024.pdf 第12页'), {}, NAMES)
    assert [item.ref for item in distinct.items] == ['第 8 页', '第 12 页']


def test_short_name_drops_the_issuer():
    assert short_name(CAICT) == '《人工智能发展报告（2024年）》'
    assert short_name('q3 board review') == 'q3 board review'
