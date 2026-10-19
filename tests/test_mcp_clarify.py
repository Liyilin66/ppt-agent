"""Requirement clarification over MCP: prepare_deck settles the brief, create_deck enforces it."""
import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from ppt_agent import deck_jobs, mcp_server
from ppt_agent.v2.mock import MockLLMClient


def _value(result):
    return result.structuredContent or json.loads(result.content[0].text)


def user(value):
    return {'value': value, 'origin': 'user'}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PPT_AGENT_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('PPT_AGENT_API_KEY', 'offline-test-only')
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    monkeypatch.setattr(deck_jobs, 'create_v2_model_client', lambda: MockLLMClient())
    contexts = []
    actual = mcp_server.MCPContext

    def capture(*args, **kwargs):
        context = actual(*args, **kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(mcp_server, 'MCPContext', capture)
    return contexts


@pytest.fixture
def report(tmp_path):
    path = tmp_path / '季度经营简报.md'
    path.write_text('# 收入\n三季度收入 1.2 亿元，同比增长 18%。\n\n# 成本\n毛利率 41%，较上季度提升 2 个百分点。\n',
                    encoding='utf-8')
    return path


DETAILED = {'topic': user('三季度经营复盘'), 'purpose': user('向管理层汇报三季度经营情况并争取四季度预算'),
            'audience': user('公司管理层'), 'slide_count': user(6), 'content_focus': user(['收入', '成本'])}


def run(runtime, scenario):
    async def main():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            async def call(tool, **arguments):
                return _value(await client.call_tool(tool, arguments))

            async def wait():
                await asyncio.to_thread(runtime[0].wait_for_idle, 10)
            await scenario(call, wait)
    asyncio.run(main())


def test_vague_request_is_clarified_turn_by_turn_without_creating_jobs(runtime):
    async def scenario(call, wait):
        first = await call('prepare_deck', user_message='帮我做个 AI 培训 PPT',
                           updates={'topic': user('AI 培训')})
        assert first['status'] == 'clarifying'
        assert first['next_question']['field'] == 'purpose'
        session = first['session_id']
        answers = [('让新员工会用公司内部的 AI 工具', 'purpose'), ('入职一个月内的新员工', 'audience'),
                   (12, 'slide_count'), ('none', 'source_policy')]
        state = first
        for value, field in answers:
            assert state['next_question']['field'] == field
            state = await call('prepare_deck', user_message=str(value), session_id=session,
                               updates={field: user(value)})
            assert runtime[0].store.get_latest_job() is None
        assert state['status'] == 'ready' and state['missing'] == [] and state['next_question'] is None
        assert state['fields']['audience'] == {'value': '入职一个月内的新员工', 'origin': 'user'}
        assert '入职一个月内的新员工' in state['summary']
        stored = runtime[0].store.get_mcp_brief_session(session)
        assert len(json.loads(stored.turns_json)) == 5 and stored.status == 'ready'
    run(runtime, scenario)


def test_detailed_request_with_small_md_is_ready_at_first_call(runtime, report):
    async def scenario(call, wait):
        state = await call('prepare_deck', user_message='按附件做 6 页三季度经营复盘，给管理层看',
                           updates=DETAILED, source_paths=[str(report)])
        assert state['status'] == 'ready', state
        assert state['fields']['source_policy'] == {'value': 'materials_only', 'origin': 'user'}
        assert state['source_summary'][0]['pages'] == 2
        assert state['source_summary'][0]['chapters'] == ['收入', '成本']
        assert runtime[0].store.get_latest_job() is None
    run(runtime, scenario)


def test_thin_material_conflict_clears_when_web_supplements_it(runtime, report):
    async def scenario(call, wait):
        state = await call('prepare_deck', user_message='用这份资料做 40 页，只用资料',
                           updates={**DETAILED, 'slide_count': user(40), 'source_policy': user('materials_only')},
                           source_paths=[str(report)])
        assert state['status'] == 'clarifying'
        assert [c['field'] for c in state['conflicts']] == ['slide_count']
        assert state['next_question']['kind'] == 'conflict'
        state = await call('prepare_deck', user_message='那就联网补充吧', session_id=state['session_id'],
                           updates={'source_policy': user('materials_and_web')})
        assert state['status'] == 'ready' and state['conflicts'] == []
    run(runtime, scenario)


def test_delegated_purpose_is_settled_and_named_in_the_generation_request(runtime, tmp_path):
    async def scenario(call, wait):
        state = await call('prepare_deck', user_message='目的你来定吧，讲 AI 工具入门',
                           updates={'topic': user('AI 工具入门'), 'audience': user('新员工'),
                                    'slide_count': user(4), 'source_policy': user('none'),
                                    'purpose': {'value': '让新员工一周内能独立使用 AI 工具', 'origin': 'delegated'}})
        assert 'purpose' not in state['missing'] and state['status'] == 'ready'
        assert state['fields']['purpose']['origin'] == 'delegated'
        created = await call('create_deck', session_id=state['session_id'])
        assert created['job_id'] and created['existing'] is False, created
        await wait()
        request = json.loads((tmp_path / 'data' / 'jobs' / created['job_id'] / 'long_deck_request.json').read_text())
        assert '以下各项由 Agent 代为决定：目的=让新员工一周内能独立使用 AI 工具。' in request['user_requirements']
        assert request['audience'] == '新员工'
    run(runtime, scenario)


def test_repeat_create_returns_the_same_job_and_changed_files_need_prepare_again(runtime, report):
    async def scenario(call, wait):
        state = await call('prepare_deck', user_message='按附件做 6 页', updates=DETAILED,
                           source_paths=[str(report)])
        session = state['session_id']
        first = await call('create_deck', session_id=session)
        again = await call('create_deck', session_id=session)
        assert again['job_id'] == first['job_id'] and again['existing'] is True
        await wait()
        assert runtime[0].store.get_mcp_brief_session(session).status == 'generated'
        report.write_text('# 收入\n三季度收入修订为 1.3 亿元。\n\n# 成本\n毛利率 39%。\n', encoding='utf-8')
        refused = await call('create_deck', session_id=session)
        assert refused['error'] and 'prepare_deck' in refused['error'] and 'job_id' not in refused
        refreshed = await call('prepare_deck', user_message='资料更新了', session_id=session)
        assert refreshed['status'] == 'ready' and refreshed['brief_hash'] != state['brief_hash']
        second = await call('create_deck', session_id=session)
        assert second['existing'] is False and second['job_id'] != first['job_id']
        await wait()
    run(runtime, scenario)


def test_direct_create_needs_a_settled_brief(runtime):
    async def scenario(call, wait):
        refused = await call('create_deck', topic='AI 工具培训', requirements='一页一个观点',
                             purpose='让新员工会用 AI 工具', pages=4)
        assert 'prepare_deck' in refused['error']
        assert refused['missing'] == ['audience']
        unspecified = await call('create_deck', topic='AI 工具培训', purpose='让新员工会用 AI 工具',
                                 audience='未指定', pages=4)
        assert unspecified['missing'] == ['audience']
        assert runtime[0].store.get_latest_job() is None
        created = await call('create_deck', topic='AI 工具培训', requirements='一页一个观点',
                             purpose='让新员工会用 AI 工具', audience='新员工', pages=4)
        assert created['job_id'] and created['existing'] is False, created
        await wait()
        status = await call('get_deck_status', job_id=created['job_id'])
        assert status['status'] == 'succeeded', status
    run(runtime, scenario)


def test_malformed_update_leaves_the_session_unchanged(runtime):
    async def scenario(call, wait):
        state = await call('prepare_deck', user_message='做 AI 培训', updates={'topic': user('AI 培训')})
        before = runtime[0].store.get_mcp_brief_session(state['session_id'])
        bad = await call('prepare_deck', user_message='受众从资料里看', session_id=state['session_id'],
                         updates={'audience': {'value': '管理层', 'origin': 'source'}})
        assert bad['error']
        assert runtime[0].store.get_mcp_brief_session(state['session_id']) == before
        assert (await call('prepare_deck', user_message='x', session_id='missing'))['error']
    run(runtime, scenario)


def test_planning_rules_are_server_instructions_and_a_prompt(runtime):
    async def main():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            prompts = await client.list_prompts()
            assert [p.name for p in prompts.prompts] == ['plan_deck']
            prompt = await client.get_prompt('plan_deck')
            assert prompt.messages[0].content.text == mcp_server.PLANNING_RULES
    asyncio.run(main())
    assert mcp_server.create_server().instructions == mcp_server.PLANNING_RULES
    for rule in ('每轮只问一个问题', 'delegated', '不要替用户编造', 'create_deck(session_id=...)'):
        assert rule in mcp_server.PLANNING_RULES


def test_only_the_latest_sixty_turns_are_kept(runtime):
    async def scenario(call, wait):
        state = await call('prepare_deck', user_message='第 1 轮')
        for turn in range(2, 63):
            await call('prepare_deck', user_message=f'第 {turn} 轮', session_id=state['session_id'])
        turns = json.loads(runtime[0].store.get_mcp_brief_session(state['session_id']).turns_json)
        assert len(turns) == 60 and turns[0] == '第 3 轮' and turns[-1] == '第 62 轮'
    run(runtime, scenario)
