"""The consulting deck's structure and the layouts added to carry more data."""
import json
from pathlib import Path

import pytest

from ppt_agent.v2.ir import ShapeItem, TableItem, TextItem
from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, build_deck
from ppt_agent.v2.planning import PageBrief, PageSlot
from ppt_agent.v2.evidence import EvidencePacket
from ppt_agent.v2.typeset_pipeline import generate_content, plan_archetypes, repair_citation
from ppt_agent.v2.visual.archetypes import typeset_page
from ppt_agent.v2.visual.content import PointItem, PointsContent, RoadmapContent, TableContent
from ppt_agent.v2.visual.profiles import PROFILES

CONSULTING = PROFILES['consulting']


def test_consulting_opens_on_a_summary_and_closes_on_a_roadmap(tmp_path):
    result = build_deck(BuildRequest(prompt='管理层汇报', page_count=20, output_dir=str(tmp_path),
                                     style_profile='consulting'), MockLLMClient(), progress=lambda _: None)
    design = json.loads(Path(result.deck_design_path).read_text())
    texts = [[e.get('text') for e in page['elements'] if e['type'] == 'text'] for page in design['pages']]
    assert len(design['pages']) == 20
    assert '执行摘要' in texts[1]
    assert '分三步推进' in texts[-1] and '谢谢' not in texts[-1]
    assert not any('目录' in page for page in texts)


@pytest.mark.parametrize('profile', ['training', 'launch', 'corporate'])
def test_other_styles_keep_toc_and_closing(tmp_path, profile):
    result = build_deck(BuildRequest(prompt='汇报', page_count=12, output_dir=str(tmp_path),
                                     style_profile=profile), MockLLMClient(), progress=lambda _: None)
    roles = [page['role'] for page in json.loads(Path(result.deck_design_path).read_text())['pages']]
    assert roles[1] == 'toc' and roles[-1] == 'closing'


def test_failed_frame_call_keeps_a_summary_and_the_closing(tmp_path):
    class NoFrame(MockLLMClient):
        async def complete_json(self, **kw):
            if kw['task'] == 'deck_frame':
                raise RuntimeError('gateway timeout')
            return await super().complete_json(**kw)
    result = build_deck(BuildRequest(prompt='汇报', page_count=20, output_dir=str(tmp_path),
                                     style_profile='consulting'), NoFrame(), progress=lambda _: None)
    pages = json.loads(Path(result.deck_design_path).read_text())['pages']
    assert any(e.get('text') == '执行摘要' for e in pages[1]['elements'])
    assert pages[-1]['role'] == 'closing'


def test_frame_cannot_add_a_figure_the_pages_lack(tmp_path):
    class Inventing(MockLLMClient):
        async def complete_json(self, **kw):
            payload = await super().complete_json(**kw)
            if kw['task'] == 'deck_frame':
                payload['summary']['rows'][1]['detail'] = '预计收益提升 87.5%'
            return payload
    result = build_deck(BuildRequest(prompt='汇报', page_count=20, output_dir=str(tmp_path),
                                     style_profile='consulting'), Inventing(), progress=lambda _: None)
    assert '87.5' not in Path(result.deck_design_path).read_text()


def test_stretched_points_reach_the_takeaway_band():
    content = PointsContent(title='三个结论', takeaway='先做试点', items=[
        PointItem(heading='甲', body='内容'), PointItem(heading='乙', body='内容'), PointItem(heading='丙', body='内容')])
    page, _ = typeset_page(content, CONSULTING, page_number=3, deck_title='t', composition='points:columns')
    band = next(e for e in page.elements if isinstance(e, TextItem) and e.text == '先做试点')
    cards = [e for e in page.elements if isinstance(e, ShapeItem) and e.fill == 'surface']
    assert band.frame.y > 480, 'the conclusion sits at the foot of the page'
    assert max(c.frame.bottom for c in cards) > 400, 'cards grow into the space above it'


def test_table_uses_a_native_table_with_columns_sized_to_content():
    content = TableContent(title='案例', headers=['企业', '成效'],
                           rows=[['TCL', '准确率超 90%，生产周期缩短 60%'], ['京东', '现货率超 95%']])
    page, _ = typeset_page(content, CONSULTING, page_number=3, deck_title='t')
    table = next(e for e in page.elements if isinstance(e, TableItem))
    assert table.column_widths[1] > table.column_widths[0]
    assert sum(table.column_widths) == pytest.approx(table.frame.w)


def test_roadmap_rejects_a_bar_outside_its_phases():
    with pytest.raises(ValueError):
        RoadmapContent(title='路线', phases=['一', '二'], rows=[
            {'label': '甲', 'body': '内容', 'start': 1, 'end': 3},
            {'label': '乙', 'body': '内容', 'start': 1, 'end': 1}])


def test_numeric_pages_get_data_layouts_before_generic_ones():
    slots = [PageSlot(page_number=n, kind='content', section_index=1, brief=PageBrief(title=str(n)))
             for n in range(1, 11)]
    plan = plan_archetypes(slots, {n: True for n in range(1, 11)})
    kinds = list(plan.values())
    assert sum(kind in ('metrics', 'chart', 'table') for kind in kinds) >= 7
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    assert kinds.count('table') <= 2


def test_markers_stay_level_when_card_bodies_differ():
    content = PointsContent(title='三个结论', takeaway='先做试点', items=[
        PointItem(heading='甲', body='很短'), PointItem(heading='乙', body='这一条内容明显更长一些，会折成好几行来显示，比其他卡片高。'),
        PointItem(heading='丙', body='中等长度的内容')])
    page, _ = typeset_page(content, CONSULTING, page_number=3, deck_title='t', composition='points:columns')
    markers = [e.frame.y for e in page.elements
               if isinstance(e, TextItem) and e.text in ('01', '02', '03') and e.frame.y < 600]
    assert len(set(markers)) == 1


def test_qualitative_metric_values_are_sent_back(tmp_path):
    import asyncio
    from ppt_agent.v2.orchestrator import _Checkpoints
    from ppt_agent.v2.planning import ContentBrief

    class Words(MockLLMClient):
        calls = 0
        async def complete_json(self, **kw):
            Words.calls += 1
            if Words.calls == 1:
                return {'archetype': 'metrics', 'title': '框架', 'metrics': [
                    {'value': '五维', 'label': '框架'}, {'value': '清晰', 'label': '边界'}]}
            return {'archetype': 'points', 'title': '框架', 'items': [
                {'heading': '价值', 'body': '先看价值'}, {'heading': '数据', 'body': '再看数据'}]}
    slot = PageSlot(page_number=3, kind='content', brief=PageBrief(title='框架'))
    content, record = asyncio.run(generate_content(Words(), _Checkpoints(tmp_path, resume=False), slot,
                                                   ContentBrief(topic='t', deck_title='t'), CONSULTING))
    assert content.archetype == 'points' and record['attempts'] == 2


def test_a_mis_cited_figure_gets_its_real_page_instead_of_being_deleted():
    from ppt_agent.v2.visual.content import StatementContent
    packet = EvidencePacket(text='[r.pdf | PDF p3]\n背景说明\n[r.pdf | PDF p7]\n规模达到 6233亿美元',
                            references=['r.pdf'], allowed_pages={'r.pdf': [3, 7]})
    page = StatementContent(title='规模', statement='规模达6233亿', source='r.pdf 第3页')
    fixed, added = repair_citation(page, packet)
    assert added == ['r.pdf 第7页'] and fixed.source == 'r.pdf 第3页；r.pdf 第7页'
    invented = StatementContent(title='规模', statement='规模达9999亿', source='r.pdf 第3页')
    assert repair_citation(invented, packet) == (invented, [])


def test_fallback_points_get_headings_from_their_own_text():
    from ppt_agent.v2.typeset_pipeline import fallback_content
    slot = PageSlot(page_number=3, kind='content', brief=PageBrief(title='营销试点', points=['商品特性辅助生成内容，提升效率', '发布前须人工审核']))
    assert [item.heading for item in fallback_content(slot).items] == ['商品特性辅助生成内容', '发布前须人工审核']


def test_pages_prefer_chunks_earlier_pages_did_not_use():
    from collections import Counter
    from ppt_agent.v2.evidence import EvidenceStore
    pages = [{'page': n, 'text': f'质检准确率案例{n}，企业应用场景。' * 60} for n in range(1, 11)]
    store = EvidenceStore([{'doc_id': 'd', 'name': 'r.pdf', 'path': 'r.pdf', 'sha256': '', 'page_kind': 'PDF', 'pages': pages}])
    used = Counter()
    first = store.select('质检准确率', used=used).chunk_ids
    second = store.select('质检准确率', used=used).chunk_ids
    third = store.select('质检准确率', used=used).chunk_ids
    assert set(first) == set(second), 'relevance decides until a chunk is used twice'
    assert set(third) != set(first)
    assert store.select('质检准确率').chunk_ids[:1] == first[:1], 'without tracking the ranking is unchanged'


def test_two_value_chart_is_sent_back_for_a_different_layout(tmp_path):
    import asyncio
    from ppt_agent.v2.orchestrator import _Checkpoints
    from ppt_agent.v2.planning import ContentBrief

    class TwoBars(MockLLMClient):
        calls = 0
        async def complete_json(self, **kw):
            TwoBars.calls += 1
            if TwoBars.calls == 1:
                return {'archetype': 'chart', 'title': '成效', 'chart_title': '成效', 'categories': ['准确率', '周期'],
                        'values': [90, 60], 'insights': [{'text': '提升'}]}
            return {'archetype': 'points', 'title': '成效', 'items': [
                {'heading': '质检', 'body': '提升'}, {'heading': '周期', 'body': '缩短'}]}
    slot = PageSlot(page_number=3, kind='content', brief=PageBrief(title='成效'))
    content, record = asyncio.run(generate_content(TwoBars(), _Checkpoints(tmp_path, resume=False), slot,
                                                   ContentBrief(topic='t', deck_title='t'), CONSULTING))
    assert content.archetype == 'points' and record['attempts'] == 2


def test_statement_that_loses_a_figure_is_replaced_not_left_broken(tmp_path):
    import asyncio
    from ppt_agent.v2.orchestrator import _Checkpoints
    from ppt_agent.v2.planning import ContentBrief

    class Unsupported(MockLLMClient):
        async def complete_json(self, **kw):
            return {'archetype': 'statement', 'title': '融资验证热度', 'statement': '2024年上半年316亿美元融资验证热度'}
    slot = PageSlot(page_number=3, kind='content', brief=PageBrief(title='融资验证热度'))
    content, _ = asyncio.run(generate_content(Unsupported(), _Checkpoints(tmp_path, resume=False), slot,
                                              ContentBrief(topic='t', deck_title='t'), CONSULTING))
    assert content.statement == '融资验证热度'


def test_chart_reduced_to_a_statement_does_not_inherit_a_broken_title(tmp_path):
    import asyncio
    from ppt_agent.v2.orchestrator import _Checkpoints
    from ppt_agent.v2.planning import ContentBrief

    class Unsupported(MockLLMClient):
        async def complete_json(self, **kw):
            return {'archetype': 'chart', 'title': '2024年上半年316亿美元融资验证热度', 'chart_title': '融资',
                    'categories': ['甲', '乙', '丙'], 'values': [316, 252, 135], 'insights': [{'text': '融资上升'}]}
    slot = PageSlot(page_number=3, kind='content', section_title='投入窗口已开启', brief=PageBrief(title='融资验证热度'))
    content, _ = asyncio.run(generate_content(Unsupported(), _Checkpoints(tmp_path, resume=False), slot,
                                              ContentBrief(topic='t', deck_title='t'), CONSULTING))
    assert content.archetype == 'statement'
    assert content.statement == '融资验证热度' == content.title


def test_statement_page_goes_dark_without_repeating_itself():
    from ppt_agent.v2.visual.content import StatementContent
    page, notes = typeset_page(StatementContent(title='窗口已开启', statement='窗口已开启'), CONSULTING,
                               page_number=5, deck_title='t')
    assert notes[0] == 'statement:dark'
    assert page.elements[0].fill == 'primary' and page.elements[0].frame.w == 1280
    assert [e.text for e in page.elements if isinstance(e, TextItem)].count('窗口已开启') == 1


def test_chart_highlights_the_bar_its_headline_names():
    from ppt_agent.v2.ir import ChartItem
    from ppt_agent.v2.visual.content import ChartContent, Insight
    content = ChartContent(title='融资', chart_title='占比', categories=['甲', '乙', '丙'], values=[90, 12.1, 40],
                           insights=[Insight(value='12.1%', text='乙最受关注')])
    page, _ = typeset_page(content, CONSULTING, page_number=5, deck_title='t', composition='chart:side')
    assert next(e for e in page.elements if isinstance(e, ChartItem)).highlight == 1


def test_lead_stays_only_when_it_adds_a_figure():
    plain = PointsContent(title='六成企业已试点', lead='企业正在加速试点', items=[
        PointItem(heading='甲', body='内容'), PointItem(heading='乙', body='内容')])
    page, _ = typeset_page(plain, CONSULTING, page_number=3, deck_title='t')
    assert not any(isinstance(e, TextItem) and e.text == '企业正在加速试点' for e in page.elements)
    numbered = plain.model_copy(update={'lead': '同比增长 21.5%'})
    page, _ = typeset_page(numbered, CONSULTING, page_number=3, deck_title='t')
    assert any(isinstance(e, TextItem) and e.text == '同比增长 21.5%' for e in page.elements)


def test_table_cells_centre_their_text_where_powerpoint_reads_it(tmp_path):
    from pptx import Presentation
    from pptx.enum.text import MSO_ANCHOR
    from ppt_agent.v2.ir import DeckDesign
    from ppt_agent.v2.render import render_deck
    content = TableContent(title='案例', headers=['企业', '成效'], rows=[['TCL', '准确率超 90%'], ['京东', '现货率超 95%']])
    page, _ = typeset_page(content, CONSULTING, page_number=1, deck_title='t')
    path = render_deck(DeckDesign(deck_title='t', theme=CONSULTING.theme(), pages=[page]), tmp_path / 'a.pptx')
    table = next(s for s in Presentation(path).slides[0].shapes if s.has_table).table
    assert table.cell(1, 1).vertical_anchor == MSO_ANCHOR.MIDDLE


def _hundred_page_deck():
    from ppt_agent.v2.visual.archetypes import typeset_deck
    from ppt_agent.v2.visual.samples import CNNIC_DECK, SAMPLES
    pool = [c for c in SAMPLES + CNNIC_DECK if c.archetype not in ('summary', 'roadmap')]
    contents = [pool[i % len(pool)] for i in range(87)]
    return typeset_deck(contents, CONSULTING, deck_title='长文档')


def test_a_hundred_pages_spread_across_layouts_and_skeletons():
    from collections import Counter
    from ppt_agent.v2.visual.archetypes import CARD_ROWS
    _, notes = _hundred_page_deck()
    keys = [n[0] for n in notes]
    frames = [n[1] for n in notes]
    assert max(Counter(keys).values()) / len(keys) <= 0.12
    assert len(set(frames)) == 4
    assert not any(a == b == c for a, b, c in zip(frames, frames[1:], frames[2:]))
    assert not any(all(k in CARD_ROWS for k in keys[i:i + 3]) for i in range(len(keys) - 2))


@pytest.mark.parametrize('frame', ['banner', 'lead_band', 'panel'])
def test_every_skeleton_keeps_text_apart_and_on_the_canvas(frame):
    import itertools
    from ppt_agent.v2.visual.archetypes import _typeset_framed, candidates
    from ppt_agent.v2.visual.samples import CNNIC_DECK, SAMPLES
    from ppt_agent.v2.ir import LineItem
    for content in SAMPLES + CNNIC_DECK:
        content = content.model_copy(update={'takeaway': content.takeaway or '据此决定首批试点'})
        for comp in candidates(content):
            if comp.header != 'standard':
                continue
            try:
                page, notes = _typeset_framed(content, CONSULTING, comp, 3, 't', [], frame)
            except ValueError:
                continue  # the engine retries on the standard skeleton
            if any(n.startswith(('dropped', 'content exceeds')) or 'switched' in n for n in notes):
                continue
            texts = [e for e in page.elements if isinstance(e, TextItem)]
            for e in page.elements:
                if not isinstance(e, LineItem):
                    assert e.frame.bottom <= 720.5 and e.frame.right <= 1280.5, (comp.key, e.id)
            for a, b in itertools.combinations(texts, 2):
                ix = min(a.frame.right, b.frame.right) - max(a.frame.x, b.frame.x)
                iy = min(a.frame.bottom, b.frame.bottom) - max(a.frame.y, b.frame.y)
                assert ix <= 1 or iy <= 1, (comp.key, frame, a.text, b.text)


def test_page_too_dense_for_its_skeleton_falls_back_without_cutting():
    from ppt_agent.v2.visual.archetypes import typeset_page as engine
    items = [PointItem(heading=f'要点{i}', body='这一条内容相当长，需要折成好几行才能放下完整的事实和解释说明，' + '并补充细节。' * 3) for i in range(5)]
    content = PointsContent(title='五个要点', takeaway='先做试点', items=items)
    page, notes = engine(content, CONSULTING, page_number=3, deck_title='t', composition='points:rows', frames=['banner', 'banner', 'standard', 'standard'])
    assert sum(isinstance(e, TextItem) and e.text.startswith('这一条') for e in page.elements) == 5


def test_long_runs_get_a_statement_page_to_breathe():
    slots = [PageSlot(page_number=n, kind='content', section_index=1, brief=PageBrief(title=str(n))) for n in range(1, 41)]
    kinds = list(plan_archetypes(slots, {n: True for n in range(1, 41)}).values())
    gaps = ''.join('s' if k == 'statement' else '.' for k in kinds)
    assert '.' * 9 not in gaps


def test_long_deck_shows_its_summary_once(tmp_path):
    result = build_deck(BuildRequest(prompt='长汇报', page_count=100, output_dir=str(tmp_path),
                                     style_profile='consulting'), MockLLMClient(), progress=lambda _: None)
    pages = json.loads(Path(result.deck_design_path).read_text())['pages']
    assert sum(any(e.get('text') == '执行摘要' for e in page['elements']) for page in pages) == 1
    assert len(pages) == 100


def test_terra_budget_estimate_uses_its_real_rates():
    from ppt_agent.v2.providers import lookup_default_pricing
    assert lookup_default_pricing('gpt-5.6-terra') == (2.0, 12.0)
