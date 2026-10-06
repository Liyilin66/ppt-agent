"""Content-only generation contract and deterministic ordered typesetting."""
import asyncio
import json

from ppt_agent.v2.orchestrator import _Checkpoints, BuildRequest, plan_deck_async
from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.planning import ContentBrief, PageBrief, PageSlot
from ppt_agent.v2.visual.profiles import PROFILES
from ppt_agent.v2.typeset_pipeline import generate_content, build_typeset_deck


def valid():
    return {'archetype': 'points', 'title': '核心结论', 'items': [
        {'heading': '判断', 'body': '先验证需求'}, {'heading': '行动', 'body': '再扩大试点'}]}


class Replies(MockLLMClient):
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.requests = []

    async def complete_json(self, **kwargs):
        self.requests.append(kwargs)
        return self.replies.pop(0)


def slot():
    return PageSlot(page_number=4, kind='content', section_title='方案',
                    brief=PageBrief(title='验证需求', points=['访谈用户', '验证痛点']))


def test_validation_feedback_retries_once(tmp_path):
    broken = valid() | {'title': '长' * 41}
    client = Replies([broken, valid()])
    content, record = asyncio.run(generate_content(client, _Checkpoints(tmp_path, resume=False),
                                slot(), ContentBrief(topic='方案', deck_title='方案'), PROFILES['consulting']))
    assert content.title == '核心结论'
    assert record['attempts'] == 2 and not record['fallback']
    assert 'title' in client.requests[1]['user'] and '40' in client.requests[1]['user']
    assert 'frame' not in client.requests[0]['system']


def test_invalid_twice_falls_back_to_source_points(tmp_path):
    client = Replies([{}, {}])
    content, record = asyncio.run(generate_content(client, _Checkpoints(tmp_path, resume=False),
                                slot(), ContentBrief(topic='方案', deck_title='方案'), PROFILES['consulting']))
    assert record['fallback'] and record['attempts'] == 2
    assert content.archetype == 'points'
    assert [i.body for i in content.items] == ['访谈用户', '验证痛点']
    assert len(record['validation_errors']) == 2


def test_chart_value_length_mismatch_retries(tmp_path):
    chart = {'archetype': 'chart', 'title': '真实数据', 'chart_title': '资料统计',
             'categories': ['A', 'B'], 'values': [1, 2, 3], 'source': None,
             'insights': [{'text': '比较两项'}]}
    client = Replies([chart, valid()])
    _, record = asyncio.run(generate_content(client, _Checkpoints(tmp_path, resume=False),
                              slot(), ContentBrief(topic='方案', deck_title='方案'), PROFILES['consulting']))
    assert record['attempts'] == 2
    assert 'categories' in client.requests[1]['user']


def test_ordered_content_finishes_before_ordered_layout(tmp_path, monkeypatch):
    import ppt_agent.v2.typeset_pipeline as pipeline
    from ppt_agent.v2.visual.archetypes import typeset_page as original
    class Delayed(MockLLMClient):
        def __init__(self):
            super().__init__()
            self.completed = []
        async def complete_json(self, *, task, **kwargs):
            if task == 'page_content':
                n = kwargs['context']['page_number']
                await asyncio.sleep((20 - n) * .001)
                self.completed.append(n)
            return await super().complete_json(task=task, **kwargs)
    client = Delayed()
    request = BuildRequest(prompt='企业方案', page_count=20, output_dir=str(tmp_path), deck_name='ordered')
    planned = asyncio.run(plan_deck_async(request, client, progress=lambda _: None))
    calls = []
    events = []
    total = len(planned.skeleton.content_slots())
    def spy(content, profile, **kwargs):
        assert len(set(client.completed)) == total
        calls.append((kwargs['page_number'], list(kwargs['history'])))
        return original(content, profile, **kwargs)
    monkeypatch.setattr(pipeline, 'typeset_page', spy)
    result = asyncio.run(build_typeset_deck(request, client, _Checkpoints(tmp_path / 'checkpoints', resume=False),
                      planned.brief, planned.skeleton, progress=events.append))
    assert [event for event in events if event.startswith('[typeset]')] == [
        f'[typeset] page {n} done' for n in range(1, 21)]
    assert [n for n, _ in calls] == sorted(n for n, _ in calls)
    assert [len(h) for _, h in calls] == list(range(total))
    assert result.page_count == 20 and result.pptx_path
    report = json.loads((tmp_path / 'ordered_run_report.json').read_text())
    assert len(report['typeset_pages']) == total


def test_budget_stops_without_second_call(tmp_path):
    from ppt_agent.v2.providers import BudgetExceededError
    class BudgetClient(MockLLMClient):
        calls = 0
        async def complete_json(self, **kwargs):
            self.calls += 1
            raise BudgetExceededError('budget reached')
    client = BudgetClient()
    content, record = asyncio.run(generate_content(client, _Checkpoints(tmp_path, resume=False),
                                slot(), ContentBrief(topic='方案', deck_title='方案'), PROFILES['consulting']))
    assert client.calls == 1 and record['fallback']
    assert content.items[0].body == '访谈用户'


def test_force_regenerate_and_cache(tmp_path):
    store = _Checkpoints(tmp_path, resume=True)
    client = Replies([valid(), valid() | {'title': '修改后的结论'}])
    brief = ContentBrief(topic='方案', deck_title='方案')
    asyncio.run(generate_content(client, store, slot(), brief, PROFILES['consulting']))
    cached, _ = asyncio.run(generate_content(client, store, slot(), brief, PROFILES['consulting']))
    assert cached.title == '核心结论' and len(client.requests) == 1
    changed, _ = asyncio.run(generate_content(client, store, slot(), brief, PROFILES['consulting'],
                                              revision_instruction='改变标题', force_regenerate=True))
    assert changed.title == '修改后的结论' and len(client.requests) == 2
    assert '改变标题' in client.requests[1]['user']


def test_content_qa_omits_geometry_and_warns_missing_source():
    from ppt_agent.v2.typeset_pipeline import content_qa, content_adapter
    payload = valid() | {'items': [{'heading': '规模', 'body': '用户提供了800人'},
                                  {'heading': '行动', 'body': '开展试点'}]}
    content = content_adapter().validate_python(payload)
    seen = set()
    assert [issue.code for issue in content_qa(content, 1, seen).issues] == ['number_without_source']
    assert [issue.code for issue in content_qa(content, 2, seen).issues] == ['duplicate_title', 'number_without_source']


def test_typesetter_failure_is_recorded_and_local_fallback(tmp_path, monkeypatch):
    import ppt_agent.v2.typeset_pipeline as pipeline
    from ppt_agent.v2.visual.archetypes import typeset_page as original
    client = MockLLMClient()
    request = BuildRequest(prompt='企业方案', page_count=10, output_dir=str(tmp_path), deck_name='local')
    planned = asyncio.run(plan_deck_async(request, client, progress=lambda _: None))
    failed_page = planned.skeleton.content_slots()[0].page_number
    already_failed = False
    def spy(content, profile, **kwargs):
        nonlocal already_failed
        if kwargs['page_number'] == failed_page and not already_failed:
            already_failed = True
            raise ValueError('test dense content does not fit')
        return original(content, profile, **kwargs)
    monkeypatch.setattr(pipeline, 'typeset_page', spy)
    result = asyncio.run(build_typeset_deck(request, client, _Checkpoints(tmp_path / 'checkpoints', resume=False),
                        planned.brief, planned.skeleton, progress=lambda _: None))
    assert result.fallback_pages == 1 and result.pptx_path
    report = json.loads((tmp_path / 'local_run_report.json').read_text())
    assert report['typeset_pages'][0]['layout_errors'] == ['test dense content does not fit']
    assert sum(record['fallback'] for record in report['typeset_pages']) == 1


def test_revision_actual_prompt_contains_current_content(tmp_path):
    client = Replies([valid()])
    current = valid() | {'title': '上次修改后的标题', 'items': [
        {'heading': '已有结论', 'body': '上次修改后的正文'},
        {'heading': '保留', 'body': '不相关的要点应保留'}]}
    asyncio.run(generate_content(client, _Checkpoints(tmp_path, resume=False), slot(),
        ContentBrief(topic='方案', deck_title='方案'), PROFILES['consulting'],
        revision_instruction='仅压缩正文', force_regenerate=True, current_content=current))
    prompt = json.loads(client.requests[0]['user'])
    assert prompt['current_content'] == current
    assert prompt['page']['revision_instruction'] == '仅压缩正文'


def test_mock_typeset_revision_plans_profile_pages_and_rejection():
    client = MockLLMClient()
    async def plan(message, pages=None):
        return await client.complete_json(task='typeset_revision_plan', system='', user='',
            context={'message': message, 'selected_pages': pages or [], 'profile': 'corporate'})
    changed = asyncio.run(plan('换成咨询风格'))
    assert changed['profile'] == 'consulting' and changed['pages'] == []
    rewrite = asyncio.run(plan('改写正文', [4]))
    assert rewrite['pages'] == [{'page_number': 4, 'instruction': '改写正文'}]
    rejected = asyncio.run(plan('把标题向右移动'))
    assert rejected['unsupported_reason'] and not rejected['pages']
