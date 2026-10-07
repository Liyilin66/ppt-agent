"""Core MCP tools exercise the real offline pipeline and bounded background work."""
import asyncio
import json
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from ppt_agent import deck_jobs, mcp_server
from ppt_agent.v2.mock import MockLLMClient


def _value(result):
    return result.structuredContent or json.loads(result.content[0].text)


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


async def _wait(runtime):
    await asyncio.to_thread(runtime[0].wait_for_idle, 10)


def test_mcp_complete_create_status_revision_and_web_history(tmp_path, runtime):
    source = tmp_path / 'material.md'
    source.write_text('# 培训目标\n理解工具，验证输出，再交付结果。\n')
    examples = []

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            assert {t.name for t in (await client.list_tools()).tools} == {
                'create_deck', 'get_deck_status', 'revise_deck'}
            arguments = {'topic': '企业工具培训', 'requirements': '一页一观点，清晰易懂',
                         'pages': 4, 'source_paths': [str(source)], 'style': 'training'}
            created = _value(await client.call_tool('create_deck', arguments))
            examples.append({'tool': 'create_deck', 'input': arguments, 'output': created})
            assert created['job_id']
            assert created['status'] in {'queued', 'pending', 'running'}
            assert 'get_deck_status' in created['message']
            initial = _value(await client.call_tool('get_deck_status', {'job_id': created['job_id']}))
            assert initial['total_pages'] == 4
            await _wait(runtime)
            status = _value(await client.call_tool('get_deck_status', {'job_id': created['job_id']}))
            examples.append({'tool': 'get_deck_status', 'input': {'job_id': created['job_id']}, 'output': status})
            assert status['status'] == 'succeeded', status
            assert status['completed_pages'] == status['total_pages'] == 4
            assert Path(status['pptx_path']).is_absolute()
            assert Path(status['pptx_path']).is_file()
            assert status['stage']
            assert status['statistics']
            assert Path(status['data_dir']) == tmp_path / 'data'
            assert status['version']
            revision_input = {'job_id': created['job_id'], 'instruction': '第3页改成对比', 'page_numbers': [3]}
            revision = _value(await client.call_tool('revise_deck', revision_input))
            examples.append({'tool': 'revise_deck', 'input': revision_input, 'output': revision})
            assert revision['revision_id']
            await _wait(runtime)
            revised = _value(await client.call_tool('get_deck_status', {'job_id': created['job_id']}))
            assert revised['latest_revision']['revision_id'] == revision['revision_id']
            assert revised['latest_revision']['status'] == 'succeeded', revised
            assert revised['latest_revision']['revised_pages'] == [3]
            assert revised['latest_revision']['reply']
            examples.append({'tool': 'get_deck_status', 'input': {'job_id': created['job_id']}, 'output': revised})
            from ppt_agent.api import create_app
            with TestClient(create_app(data_dir=tmp_path / 'data')) as web:
                history = web.get('/api/presentations').json()['items']
                assert any(item['job_id'] == created['job_id'] for item in history)
    asyncio.run(check())
    (tmp_path / 'tool-examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2))


def test_mcp_invalid_paths_parameters_and_unknown_job_leave_store_empty(tmp_path, runtime):
    unsupported = tmp_path / 'source.exe'
    unsupported.write_text('not a supported file')
    valid = tmp_path / 'material.md'
    valid.write_text('offline')
    cases = [
        {'source_paths': [str(tmp_path / 'missing.pdf')]},
        {'source_paths': ['relative.pdf']},
        {'source_paths': [str(unsupported)]},
        {'pages': 3}, {'pages': 101}, {'search': True},
        {'source_paths': [str(valid)] * 21},
        {'style': 'unknown'}, {'topic': ''}, {'requirements': ''},
    ]

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            for case in cases:
                result = await client.call_tool('create_deck', {'topic': '测试', 'requirements': '测试', **case})
                if not result.isError:
                    assert _value(result).get('error'), (case, result)
            missing = _value(await client.call_tool('get_deck_status', {'job_id': 'missing'}))
            assert missing['error']
            missing_revision = _value(await client.call_tool('revise_deck', {'job_id': 'missing', 'instruction': '改一下'}))
            assert missing_revision['error']
            assert runtime[0].store.get_latest_job() is None
    asyncio.run(check())


def test_mcp_missing_model_configuration_creates_no_job(tmp_path, runtime, monkeypatch):
    def missing():
        raise ValueError('PPT_AGENT_API_KEY or OPENAI_API_KEY is not set')
    monkeypatch.setattr(deck_jobs, 'create_v2_model_client', missing)
    monkeypatch.delenv('PPT_AGENT_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            value = _value(await client.call_tool('create_deck', {'topic': '测试', 'requirements': '测试'}))
            assert value['error']
            assert 'KEY' in value['error'] or '模型' in value['error']
            assert runtime[0].store.get_latest_job() is None
    asyncio.run(check())


def test_mcp_busy_generation_and_early_revision_are_rejected(tmp_path, runtime, monkeypatch):
    entered, release = Event(), Event()
    actual = deck_jobs.build_v2_deck

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'test did not release the offline generation'
        return actual(*args, **kwargs)

    monkeypatch.setattr(deck_jobs, 'build_v2_deck', blocking)

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            try:
                first = _value(await client.call_tool('create_deck', {'topic': '测试', 'requirements': '测试', 'pages': 4}))
                assert await asyncio.to_thread(entered.wait, 10)
                second = _value(await client.call_tool('create_deck', {'topic': '再次创建', 'requirements': '测试'}))
                assert second['error']
                assert second['job_id'] == first['job_id']
                early = _value(await client.call_tool('revise_deck', {'job_id': first['job_id'], 'instruction': '第3页改成对比'}))
                assert early['error']
            finally:
                release.set()
                await _wait(runtime)
            status = _value(await client.call_tool('get_deck_status', {'job_id': first['job_id']}))
            assert status['status'] == 'succeeded'
    asyncio.run(check())


def test_mcp_revision_requires_checkpoints(tmp_path, runtime):
    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            job = runtime[0].store.create_job(job_type='long_deck_v2')
            runtime[0].store.update_job(job.job_id, status='succeeded')
            value = _value(await client.call_tool('revise_deck', {'job_id': job.job_id, 'instruction': '改一下'}))
            assert value['error']
            assert 'checkpoint' in value['error'].lower() or '检查点' in value['error']
    asyncio.run(check())


def test_mcp_same_job_cannot_have_two_running_revisions(tmp_path, runtime, monkeypatch):
    entered, release = Event(), Event()
    actual = deck_jobs.revise_v2_deck

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'test did not release the offline revision'
        return actual(*args, **kwargs)

    monkeypatch.setattr(deck_jobs, 'revise_v2_deck', blocking)

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            created = _value(await client.call_tool('create_deck', {'topic': '测试', 'requirements': '测试', 'pages': 4}))
            await _wait(runtime)
            arguments = {'job_id': created['job_id'], 'instruction': '第3页改成对比', 'page_numbers': [3]}
            try:
                first = _value(await client.call_tool('revise_deck', arguments))
                assert first['revision_id']
                assert await asyncio.to_thread(entered.wait, 10)
                second = _value(await client.call_tool('revise_deck', arguments))
                assert second['error']
            finally:
                release.set()
                await _wait(runtime)
            status = _value(await client.call_tool('get_deck_status', {'job_id': created['job_id']}))
            assert status['latest_revision']['status'] == 'succeeded'
    asyncio.run(check())


def test_mcp_local_sources_are_staged_for_web_resume(tmp_path, runtime, monkeypatch):
    from PIL import Image

    document = tmp_path / 'original.md'
    document.write_text('# 原始资料\n必须保留这段原始材料。\n')
    image = tmp_path / 'original.png'
    Image.new('RGB', (12, 8), (50, 80, 100)).save(image)
    original_document = document.read_bytes()
    original_image = image.read_bytes()
    calls = []
    actual = deck_jobs.build_v2_deck

    def capture(request, *args, **kwargs):
        calls.append(request)
        return actual(request, *args, **kwargs)

    monkeypatch.setattr(deck_jobs, 'build_v2_deck', capture)

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            created = _value(await client.call_tool('create_deck', {
                'topic': '资料持久化', 'requirements': '保留原始材料', 'pages': 4,
                'source_paths': [str(document), str(image)],
            }))
            await _wait(runtime)
            status = _value(await client.call_tool('get_deck_status', {'job_id': created['job_id']}))
            assert status['status'] == 'succeeded', status
            metadata = json.loads((tmp_path / 'data' / 'jobs' / created['job_id'] / 'long_deck_request.json').read_text())
            assert len(metadata['attachment_ids']) == 2
            staged_document = Path(calls[0].source_paths[0])
            staged_image = Path(calls[0].image_paths[0])
            assert staged_document.is_absolute() and staged_image.is_absolute()
            assert staged_document.is_relative_to(tmp_path / 'data' / 'uploads')
            assert staged_image.is_relative_to(tmp_path / 'data' / 'uploads')
            assert staged_document.read_bytes() == original_document
            assert staged_image.read_bytes() == original_image
            assert {staged_document.parent.name, staged_image.parent.name} == set(metadata['attachment_ids'])
            document.write_text('原文件后来改了，不应改变已提交资料。')
            image.unlink()
            assert staged_document.read_bytes() == original_document
            assert staged_image.read_bytes() == original_image
            from ppt_agent.api import create_app
            with TestClient(create_app(data_dir=tmp_path / 'data')) as web:
                resumed = web.post(f"/api/long-deck-jobs/{created['job_id']}/resume")
                assert resumed.status_code == 202, resumed.text
                result = web.get(f"/api/jobs/{resumed.json()['job_id']}").json()
                assert result['status'] == 'succeeded', result
            assert len(calls) == 2
            assert calls[1].source_paths == [str(staged_document)]
            assert calls[1].image_paths == [str(staged_image)]
    asyncio.run(check())


def test_mcp_status_reads_existing_report_fields_and_preserves_missing_values(tmp_path, runtime):
    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            job = runtime[0].store.create_job(job_type='long_deck_v2')
            runtime[0].store.update_job(job.job_id, status='succeeded')
            output = tmp_path / 'data' / 'jobs' / job.job_id
            output.mkdir(parents=True)
            report_path = output / 'generated_long_deck_v2_run_report.json'
            report_path.write_text(json.dumps({
                'outcomes': [{'page_number': 1, 'status': 'ok'},
                             {'page_number': 2, 'status': 'fallback'},
                             {'page_number': 3, 'status': 'fallback'}],
                'content_statistics': {'source_empty_pages': 3, 'source_invalid_pages': 2,
                                       'source_invalid_attempts': 4},
                'usage': {'estimated_cost_usd': 0.125},
            }))
            before = report_path.read_bytes()
            result = _value(await client.call_tool('get_deck_status', {'job_id': job.job_id}))
            assert result['statistics'] == {
                'fallback_pages': 2, 'source_empty_pages': 3, 'source_invalid_pages': 2,
                'source_invalid_attempts': 4, 'estimated_cost_usd': 0.125,
            }
            assert report_path.read_bytes() == before
            report_path.write_text('{}')
            result = _value(await client.call_tool('get_deck_status', {'job_id': job.job_id}))
            assert result['statistics'] == dict.fromkeys([
                'fallback_pages', 'source_empty_pages', 'source_invalid_pages',
                'source_invalid_attempts', 'estimated_cost_usd',
            ])
            report_path.unlink()
            result = _value(await client.call_tool('get_deck_status', {'job_id': job.job_id}))
            assert result['statistics'] is None
            report_path.write_text('{incomplete')
            result = _value(await client.call_tool('get_deck_status', {'job_id': job.job_id}))
            assert result['statistics'] is None
            assert result['statistics_error']
    asyncio.run(check())


def test_mcp_jpeg_source_is_accepted_and_forwarded_as_image(tmp_path, runtime, monkeypatch):
    from PIL import Image

    source = tmp_path / 'diagram.jpeg'
    Image.new('RGB', (16, 16), 'white').save(source, format='JPEG')
    captured = {}
    actual = deck_jobs.build_v2_deck

    def capture(request, client, **kwargs):
        captured['request'] = request
        return actual(request, client, **kwargs)

    monkeypatch.setattr(deck_jobs, 'build_v2_deck', capture)

    async def check():
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            result = _value(await client.call_tool('create_deck', {
                'topic': '图片培训', 'requirements': '清晰说明图片信息',
                'pages': 4, 'source_paths': [str(source)],
            }))
            assert 'error' not in result, result
            await _wait(runtime)
            request = captured['request']
            assert request.source_paths == []
            assert len(request.image_paths) == 1
            staged = Path(request.image_paths[0])
            assert staged.suffix == '.jpeg'
            assert staged.read_bytes() == source.read_bytes()
            status = _value(await client.call_tool('get_deck_status', {'job_id': result['job_id']}))
            assert status['status'] == 'succeeded', status

    asyncio.run(check())
