"""Regressions from the frozen long-input run's rejected column hints."""
import json
from pathlib import Path

import pytest

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, build_deck, plan_deck
from ppt_agent.v2.planning import parse_page_briefs


@pytest.mark.parametrize('hint', ['three_column', 'four_column', 'columns', 'unknown_style'])
def test_unknown_hint_keeps_actual_section_content(hint):
    payload = json.loads((Path(__file__).parent / 'fixtures/section_four_column.json').read_text())
    payload['pages'][0]['layout_hint'] = hint
    originals = json.loads(json.dumps(payload))
    briefs = parse_page_briefs(payload)
    assert len(briefs) == 2
    assert briefs[0].points == payload['pages'][0]['points']
    assert briefs[0].speaker_notes == payload['pages'][0]['speaker_notes']
    assert briefs[0].layout_hint == ('auto' if hint == 'unknown_style' else 'cards')
    assert briefs[1].points == payload['pages'][1]['points']
    assert payload == originals


class MixedBriefClient(MockLLMClient):
    async def complete_json(self, *, task, **kwargs):
        payload = await super().complete_json(task=task, **kwargs)
        if task == 'section_pages':
            pages = payload['pages']
            for index, page in enumerate(pages):
                page['title'] = f'保留页面{index}'
                page['points'] = [f'保留正文{index}']
            pages[0]['layout_hint'] = 'three_column'
            if len(pages) > 1:
                pages[1]['title'] = ''
        return payload


def test_invalid_page_does_not_discard_valid_neighbors(tmp_path):
    result = plan_deck(BuildRequest(prompt='验证页级容错', page_count=30,
                                   output_dir=str(tmp_path)), MixedBriefClient(), progress=lambda _: None)
    for index in range(1, len(result.skeleton.outline.sections) + 1):
        slots = [s for s in result.skeleton.content_slots() if s.section_index == index]
        assert len(slots) >= 3
        assert slots[0].brief.points == ['保留正文0']
        assert slots[0].brief.layout_hint == 'cards'
        assert slots[1].brief.points  # fallback must retain available talking points
        assert slots[2].brief.points == ['保留正文2']
    events = result.skeleton.planning_events
    assert {e['page_number'] for e in events if e['action'] == 'normalized'} == {
        s.page_number for s in result.skeleton.content_slots() if s.brief.title == '保留页面0'}
    fallback_pages = {e['page_number'] for e in events if e['action'] == 'fallback'}
    assert fallback_pages == {slots.page_number for slots in result.skeleton.content_slots()
                              if slots.brief.title not in ('保留页面0',) and not slots.brief.title.startswith('保留页面')}
    resumed = plan_deck(BuildRequest(prompt='验证页级容错', page_count=30,
                                    output_dir=str(tmp_path), resume=True), MockLLMClient(), progress=lambda _: None)
    assert resumed.skeleton.planning_events == events


def test_run_report_records_brief_normalization_and_fallback(tmp_path):
    result = build_deck(BuildRequest(prompt='验证容错报告', page_count=20,
                                    output_dir=str(tmp_path)), MixedBriefClient(), progress=lambda _: None)
    report = json.loads(Path(result.run_report_path).read_text())
    events = report['planning_events']
    assert any(e['action'] == 'normalized' and e['original_hint'] == 'three_column' for e in events)
    assert any(e['action'] == 'fallback' for e in events)
    assert all(e['page_number'] >= 1 for e in events)


@pytest.mark.parametrize('hint', ['three_column', 'stats', 'timeline'])
def test_single_point_column_hint_can_render_fallback(hint):
    from ppt_agent.v2.fallback import design_fallback_page
    brief = parse_page_briefs([{'title': '单个要点', 'points': ['唯一正文'],
                                'layout_hint': hint}])[0]
    page = design_fallback_page(brief, page_number=3, section_title='章节',
                               language='zh-CN')
    assert any(getattr(element, 'text', None) == '唯一正文' for element in page.elements)
