import pytest
import asyncio
import httpx
import json

from long_input_experiment import OfficialBudget, RoutedClient, official_cost
from ppt_agent.v2.providers import ProviderConfig


def test_cached_tokens_are_not_billed_twice():
    usage = {'prompt_tokens': 1000, 'completion_tokens': 100,
             'prompt_tokens_details': {'cached_tokens': 600}}
    assert official_cost(usage) == pytest.approx(0.00212)


def test_missing_usage_cannot_be_reported_as_zero():
    with pytest.raises(ValueError):
        official_cost({})


def test_budget_refuses_call_before_spending():
    budget = OfficialBudget(0.01)
    with pytest.raises(RuntimeError, match='budget'):
        budget.reserve('request', 10000, 4096)
    assert budget.spent == 0
    assert not budget.reservations


def test_unknown_failed_call_keeps_its_reservation():
    budget = OfficialBudget(1)
    reserved = budget.reserve('request', 1000, 100)
    budget.settle('request', None)
    assert budget.spent == pytest.approx(reserved)
    assert not budget.reservations


def test_settlement_frees_unused_budget():
    budget = OfficialBudget(1)
    reserved = budget.reserve('request', 1000, 100)
    budget.settle('request', 0.001)
    assert budget.spent == pytest.approx(0.001)
    assert reserved > budget.spent


def test_model_mapping_mismatch_stops_before_more_requests(tmp_path):
    docs = [{'doc_id': 'doc1', 'pages': [
        {'pdf_page': n, 'text': f'第{n}页'} for n in range(1, 64)
    ]}]
    identity = {}
    client = RoutedClient(ProviderConfig(model='gpt-5.6-terra'), 'A', docs,
                          OfficialBudget(3), tmp_path, identity)
    with pytest.raises(RuntimeError, match='model mismatch'):
        client._parse_response({'model': 'gpt-5.6-luna'})
    assert client.halted
    assert identity == {}


def test_stream_reassembles_json_and_keeps_final_usage(tmp_path):
    docs = [{'doc_id': 'doc1', 'pages': [
        {'pdf_page': n, 'text': f'第{n}页'} for n in range(1, 64)
    ]}]
    client = RoutedClient(ProviderConfig(model='gpt-5.6-terra', api_key='test-key'),
                          'A', docs, OfficialBudget(3), tmp_path, {}, streaming=True)
    frames = [
        {'model': 'gpt-5.6-terra', 'choices': [{'delta': {'content': '{"pages":'}}]},
        {'model': 'gpt-5.6-terra', 'choices': [{'delta': {'content': '[]}'}}]},
        {'model': 'gpt-5.6-terra', 'choices': [],
         'usage': {'prompt_tokens': 10, 'completion_tokens': 5}},
    ]
    data = '\n\n'.join('data: '+json.dumps(x) for x in frames)+'\n\ndata: [DONE]\n\n'
    def respond(request):
        body = json.loads(request.content)
        assert body['stream'] is True
        assert body['stream_options'] == {'include_usage': True}
        assert body['reasoning_effort'] == 'none'
        return httpx.Response(200, text=data, headers={'content-type': 'text/event-stream'})
    async def verify():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client._client = http
            result = await client._stream_json('system', 'user', 4096)
        assert result == {'pages': []}
        assert client.usage.input_tokens == 10
        assert client.pending_payload['usage']['completion_tokens'] == 5
    asyncio.run(verify())
