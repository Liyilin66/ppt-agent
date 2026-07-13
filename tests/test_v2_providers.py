"""Tests for the BYOK provider layer: parsing, retries, budget."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ppt_agent.v2.providers import (
    AnthropicClient,
    BudgetExceededError,
    OpenAICompatClient,
    ProviderConfig,
    ProviderError,
    UsageMeter,
    ensure_pricing,
    extract_json_payload,
    lookup_default_pricing,
    provider_config_from_env,
)


class TestExtractJsonPayload:
    def test_plain_json(self) -> None:
        assert extract_json_payload('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        assert extract_json_payload('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_prose_around_it(self) -> None:
        assert extract_json_payload('Sure! Here it is {"a": [1, 2]} hope it helps') == {
            "a": [1, 2]
        }

    def test_garbage_raises(self) -> None:
        with pytest.raises(ProviderError):
            extract_json_payload("not json at all")


def _openai_config(**overrides) -> ProviderConfig:
    defaults = dict(
        protocol="openai",
        model="test-model",
        api_key="sk-test",
        base_url="https://example.test/v1",
        max_retries=1,
    )
    defaults.update(overrides)
    return ProviderConfig(**defaults)


def _openai_response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def _client_with(handler, config=None) -> OpenAICompatClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return OpenAICompatClient(config or _openai_config(), client=http)


class TestOpenAICompatClient:
    def test_success_records_usage(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "test-model"
            assert request.headers["authorization"] == "Bearer sk-test"
            return httpx.Response(200, json=_openai_response('{"ok": true}'))

        client = _client_with(handler)
        result = asyncio.run(
            client.complete_json(task="t", system="s", user="u")
        )
        assert result == {"ok": True}
        assert client.usage.input_tokens == 10
        assert client.usage.output_tokens == 20

    def test_retries_on_500_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json=_openai_response('{"ok": 1}'))

        client = _client_with(handler)
        result = asyncio.run(client.complete_json(task="t", system="s", user="u"))
        assert result == {"ok": 1}
        assert calls["n"] == 2

    def test_gives_up_after_retries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(524, text="origin timeout")

        client = _client_with(handler)
        with pytest.raises(ProviderError, match="after 2 attempts"):
            asyncio.run(client.complete_json(task="t", system="s", user="u"))

    def test_budget_guard_blocks_next_call(self) -> None:
        config = _openai_config(
            input_cost_per_mtok_usd=1_000_000.0, output_cost_per_mtok_usd=0.0
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_openai_response('{"ok": 1}'))

        client = OpenAICompatClient(
            config,
            usage=UsageMeter(budget_usd=5.0),
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        asyncio.run(client.complete_json(task="t", system="s", user="u"))
        assert client.usage.estimated_cost_usd == pytest.approx(10.0)
        with pytest.raises(BudgetExceededError):
            asyncio.run(client.complete_json(task="t", system="s", user="u"))


class TestAnthropicClient:
    def test_parses_messages_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == "sk-ant"
            assert request.url.path == "/v1/messages"
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": '{"a": 2}'}],
                    "usage": {"input_tokens": 5, "output_tokens": 7},
                },
            )

        config = ProviderConfig(
            protocol="anthropic",
            model="claude-sonnet-5",
            api_key="sk-ant",
            base_url="https://anthropic.test",
        )
        client = AnthropicClient(
            config, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        result = asyncio.run(client.complete_json(task="t", system="s", user="u"))
        assert result == {"a": 2}


class TestProviderConfigFromEnv:
    def test_defaults_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PPT_AGENT_PROVIDER", raising=False)
        monkeypatch.delenv("PPT_AGENT_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        config = provider_config_from_env()
        assert config.protocol == "openai"
        assert config.model == "gpt-5.5"

    def test_rejects_unknown_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PPT_AGENT_PROVIDER", "gemini")
        with pytest.raises(ProviderError):
            provider_config_from_env()

    def test_missing_key_raises_with_guidance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PPT_AGENT_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = ProviderConfig(protocol="openai", model="m")
        with pytest.raises(ProviderError, match="PPT_AGENT_API_KEY"):
            config.resolved_api_key()


class TestPricingDefaults:
    def test_unknown_model_uses_non_zero_estimates(self) -> None:
        input_cost, output_cost = lookup_default_pricing("custom-private-model")
        assert input_cost > 0
        assert output_cost > 0

    def test_missing_rates_are_filled_without_overwriting_user_rate(self) -> None:
        config = ProviderConfig(
            protocol="openai",
            model="gpt-4o-mini",
            input_cost_per_mtok_usd=0.25,
        )
        priced, used_defaults = ensure_pricing(config)
        assert used_defaults is True
        assert priced.input_cost_per_mtok_usd == 0.25
        assert priced.output_cost_per_mtok_usd is not None
        assert priced.output_cost_per_mtok_usd > 0

    def test_complete_user_rates_are_preserved(self) -> None:
        config = ProviderConfig(
            protocol="openai",
            model="custom",
            input_cost_per_mtok_usd=1.25,
            output_cost_per_mtok_usd=4.5,
        )
        priced, used_defaults = ensure_pricing(config)
        assert used_defaults is False
        assert priced == config
