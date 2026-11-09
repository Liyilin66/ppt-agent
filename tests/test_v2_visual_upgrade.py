"""The consulting deck's structure and the layouts added to carry more data."""
import json
from pathlib import Path

import pytest

from ppt_agent.v2.ir import ShapeItem, TableItem, TextItem
from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, build_deck
from ppt_agent.v2.planning import PageBrief, PageSlot
from ppt_agent.v2.typeset_pipeline import generate_content, plan_archetypes
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
