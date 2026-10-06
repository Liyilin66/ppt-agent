import asyncio
import json

import httpx
import pytest

from evidence_acceptance import AcceptanceClient, PersistentBudget, page_statistics, run_policy
from long_input_experiment import save
from ppt_agent.v2.providers import ProviderConfig


def test_budget_persists_settled_and_crashed_reservations(tmp_path):
    path = tmp_path / 'budget.json'
    first = PersistentBudget(path)
    first.reserve('one', 1000, 32)
    first.settle('one', .01)
    reservation = first.reserve('crashed', 2000, 4096)
    reopened = PersistentBudget(path)
    assert reopened.spent == pytest.approx(.01 + reservation)
    reopened.reserve('next', 1000, 32)
    reopened.settle('next', .02)
    assert PersistentBudget(path).spent == pytest.approx(.03 + reservation)


def test_budget_refuses_before_reserve_write_when_cap_exceeded(tmp_path):
    budget = PersistentBudget(tmp_path / 'budget.json')
    budget.spent = 1.99
    with pytest.raises(RuntimeError, match='before HTTP'):
        budget.reserve('too_much', 20_000, 4096)
    assert not budget.path.exists()


def test_run_policy_requires_explicit_resume_and_never_reruns_completed(tmp_path):
    folder = tmp_path / 'T3'
    run_policy(folder, False)
    folder.mkdir()
    with pytest.raises(RuntimeError, match='explicit --resume'):
        run_policy(folder, False)
    with pytest.raises(RuntimeError, match='No frozen'):
        run_policy(folder, True)
    save(folder / 'manifest.json', {})
    run_policy(folder, True)
    save(folder / 'result.json', {})
    with pytest.raises(RuntimeError, match='immutable'):
        run_policy(folder, True)


def test_source_statistics_are_syntactic_not_claim_truth(tmp_path):
    save(tmp_path / 'checkpoints/typeset/content_004.json', {
        'content': {'archetype': 'metrics', 'title': '用户数 5.15 亿', 'source': 'report.pdf 第 12 页'},
        'record': {'page_number': 4, 'evidence_selection': {'strategy': 'chapter', 'allowed_pages': {'report.pdf': [12]}}}})
    save(tmp_path / 'checkpoints/typeset/content_005.json', {
        'content': {'archetype': 'points', 'title': '增长情况', 'speaker_notes': '99', 'source': None},
        'record': {'page_number': 5}})
    result = page_statistics(tmp_path, ['report.pdf'])
    assert result['numeric_pages'] == 1
    assert result['numeric_source_format_rate'] == 1
    assert result['numeric_source_selection_rate'] == 1
    assert result['archetype_ratios'] == {'metrics': .5, 'points': .5}
    assert result['semantic_fabrication_audit'].startswith('pending')
    assert result['pages'][0]['evidence_selection']['strategy'] == 'chapter'


def test_preflight_preserves_raw_usage_and_request_limits(tmp_path):
    async def check():
        seen = []
        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            return httpx.Response(200, json={'model': 'gpt-5.6-terra', 'id': 'mock-id',
                'choices': [{'message': {'content': '{"ok":true}'}}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 4}})
        cfg = ProviderConfig(model='gpt-5.6-terra', api_key='mock', max_output_tokens=32, max_retries=0)
        budget = PersistentBudget(tmp_path / 'budget.json')
        docs = [{'doc_id': 'doc1', 'pages': [{'pdf_page': number, 'text': '资料'} for number in range(9, 64)]}]
        client = AcceptanceClient(cfg, 'A', docs, budget, tmp_path / 'preflight', {'model': cfg.model})
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await client.complete_json(task='preflight', system='JSON', user='ok', max_output_tokens=32)
        assert seen[0]['reasoning_effort'] == 'none'
        assert seen[0]['max_tokens'] == 32
        raw = json.loads((tmp_path / 'preflight/calls/01-raw-response.json').read_text())
        assert raw['usage'] == {'prompt_tokens': 11, 'completion_tokens': 4}
        assert client.records[0]['input_character_limit'] == 40_000
        with pytest.raises(RuntimeError, match='before HTTP'):
            await client.complete_json(task='page_content', system='JSON', user='x' * 40001)
        assert len(seen) == 1
        await client.aclose()
    asyncio.run(check())


def test_numeric_citation_cannot_reference_unselected_page(tmp_path):
    save(tmp_path / 'checkpoints/typeset/content_004.json', {
        'content': {'archetype': 'metrics', 'title': '用户数 5.15 亿', 'source': 'report.pdf 第 13 页'},
        'record': {'page_number': 4, 'evidence': {'allowed_pages': {'report.pdf': [12]}}}})
    result = page_statistics(tmp_path, ['report.pdf'])
    assert result['numeric_source_format_rate'] == 1
    assert result['numeric_source_selection_rate'] == 0


def test_gateway_failure_keeps_unknown_reserve_and_does_not_retry(tmp_path):
    async def check():
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(524, text='Gateway timed out')
        cfg = ProviderConfig(model='gpt-5.6-terra', api_key='mock', max_retries=0)
        path = tmp_path / 'budget.json'
        budget = PersistentBudget(path)
        docs = [{'doc_id': 'doc1', 'pages': [{'pdf_page': number, 'text': '资料'} for number in range(9, 64)]}]
        client = AcceptanceClient(cfg, 'A', docs, budget, tmp_path / 'Large', {'model': cfg.model})
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(Exception):
            await client.complete_json(task='brief', system='JSON', user='ok')
        assert count == 1
        assert budget.spent > 0
        assert PersistentBudget(path).spent == budget.spent
        assert client.records[0]['official_cost_usd'] is None
        await client.aclose()
    asyncio.run(check())
