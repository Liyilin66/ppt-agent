"""BYOK provider layer: one JSON-task interface over OpenAI-compatible and Anthropic APIs.

The whole v2 pipeline talks to models through ``LLMClient.complete_json``. Users
bring their own key, model name, and base URL; nothing in the pipeline depends
on a specific vendor. Requests are kept small (one page per call) so proxies
with strict read timeouts still work; resilience comes from retries plus the
orchestrator's per-page fallback, not from long-lived streams.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field

from ppt_agent.models import StrictModel


DEFAULT_TIMEOUT_SECONDS = 100.0
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504, 524}

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ProviderError(RuntimeError):
    """Raised when a provider call fails after all retries."""


class BudgetExceededError(RuntimeError):
    """Raised when the run would exceed the configured cost budget."""


class ProviderConfig(StrictModel):
    """User-supplied model endpoint configuration (BYOK)."""

    protocol: Literal["openai", "anthropic"] = "openai"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    max_retries: int = Field(default=3, ge=0)
    temperature: float | None = Field(default=0.4, ge=0, le=2)
    max_output_tokens: int = Field(default=8192, gt=0)
    input_cost_per_mtok_usd: float | None = Field(default=None, ge=0)
    output_cost_per_mtok_usd: float | None = Field(default=None, ge=0)

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_names = (
            ["OPENAI_API_KEY"] if self.protocol == "openai" else ["ANTHROPIC_API_KEY"]
        )
        env_names.insert(0, "PPT_AGENT_API_KEY")
        for name in env_names:
            value = os.environ.get(name, "").strip()
            if value:
                return value
        raise ProviderError(
            f"No API key configured for protocol '{self.protocol}'. "
            f"Set PPT_AGENT_API_KEY or {env_names[-1]}, or pass api_key explicitly."
        )

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.protocol == "openai":
            return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        return os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")


# Approximate USD prices per million tokens, matched by substring against the
# model name. These fallback estimates keep --budget-usd from silently metering
# $0.00 when a provider does not expose pricing. They are not billing rates;
# users should pass explicit prices when accuracy matters.
DEFAULT_MODEL_PRICES_USD_PER_MTOK: tuple[tuple[str, float, float], ...] = (
    ("claude-fable", 10.0, 50.0),
    ("claude-mythos", 10.0, 50.0),
    ("claude-opus", 5.0, 25.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-haiku", 1.0, 5.0),
    ("gpt-4o-mini", 0.6, 2.4),
    ("gpt-4o", 3.0, 12.0),
    ("gpt-4.1-mini", 0.8, 3.2),
    ("gpt-4.1", 3.0, 12.0),
    ("o3", 5.0, 20.0),
    ("o1", 15.0, 60.0),
    ("deepseek", 0.6, 2.5),
    ("qwen", 0.8, 3.0),
    ("glm", 0.8, 3.0),
    ("kimi", 1.0, 4.0),
)
GENERIC_DEFAULT_PRICE_USD_PER_MTOK: tuple[float, float] = (5.0, 20.0)


def lookup_default_pricing(model: str) -> tuple[float, float]:
    """Guardrail price estimate for a model name (input, output per MTok)."""

    lowered = model.lower()
    for pattern, input_cost, output_cost in DEFAULT_MODEL_PRICES_USD_PER_MTOK:
        if pattern in lowered:
            return input_cost, output_cost
    return GENERIC_DEFAULT_PRICE_USD_PER_MTOK


def ensure_pricing(config: ProviderConfig) -> tuple[ProviderConfig, bool]:
    """Fill in any missing per-token prices with non-zero estimates.

    Returns the (possibly updated) config and whether defaults were applied.
    Without this, a budget guardrail would silently meter $0.00 forever.
    """

    if (
        config.input_cost_per_mtok_usd is not None
        and config.output_cost_per_mtok_usd is not None
    ):
        return config, False
    default_input_cost, default_output_cost = lookup_default_pricing(config.model)
    return (
        config.model_copy(
            update={
                "input_cost_per_mtok_usd": (
                    config.input_cost_per_mtok_usd
                    if config.input_cost_per_mtok_usd is not None
                    else default_input_cost
                ),
                "output_cost_per_mtok_usd": (
                    config.output_cost_per_mtok_usd
                    if config.output_cost_per_mtok_usd is not None
                    else default_output_cost
                ),
            }
        ),
        True,
    )


def provider_config_from_env() -> ProviderConfig:
    """Build a ProviderConfig from PPT_AGENT_* / OPENAI_* / ANTHROPIC_* env vars."""

    protocol = os.environ.get("PPT_AGENT_PROVIDER", "openai").strip().lower()
    if protocol not in ("openai", "anthropic"):
        raise ProviderError(
            f"Unsupported PPT_AGENT_PROVIDER '{protocol}'; use 'openai' or 'anthropic'."
        )
    default_model = "gpt-5.5" if protocol == "openai" else "claude-sonnet-5"
    model = (
        os.environ.get("PPT_AGENT_MODEL")
        or os.environ.get("OPENAI_MODEL", default_model if protocol == "openai" else "")
        or default_model
    )
    return ProviderConfig(
        protocol=protocol,  # type: ignore[arg-type]
        model=model.strip(),
        base_url=os.environ.get("PPT_AGENT_BASE_URL") or None,
    )


class UsageMeter:
    """Accumulates token usage and enforces an optional cost budget."""

    def __init__(self, budget_usd: float | None = None) -> None:
        self.budget_usd = budget_usd
        self.calls = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.estimated_cost_usd = 0.0
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        config: ProviderConfig,
        failed: bool = False,
    ) -> None:
        async with self._lock:
            self.calls += 1
            if failed:
                self.failures += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            if config.input_cost_per_mtok_usd is not None:
                self.estimated_cost_usd += (
                    input_tokens / 1_000_000 * config.input_cost_per_mtok_usd
                )
            if config.output_cost_per_mtok_usd is not None:
                self.estimated_cost_usd += (
                    output_tokens / 1_000_000 * config.output_cost_per_mtok_usd
                )

    def check_budget(self) -> None:
        if self.budget_usd is not None and self.estimated_cost_usd > self.budget_usd:
            raise BudgetExceededError(
                f"Estimated cost ${self.estimated_cost_usd:.2f} exceeds the "
                f"${self.budget_usd:.2f} budget. Resume later with --resume, raise "
                "--budget-usd, or lower --pages."
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "budget_usd": self.budget_usd,
        }


def extract_json_payload(text: str) -> Any:
    """Parse a JSON object/array out of a model reply, tolerating fences and prose."""

    candidate = text.strip()
    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ProviderError(
        f"Model reply is not valid JSON (first 200 chars): {candidate[:200]!r}"
    )


class LLMClient(Protocol):
    """Minimal JSON-task interface every provider (and the mock) implements."""

    usage: UsageMeter

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        context: Any = None,
        images: list[tuple[str, str]] | None = None,
    ) -> Any:
        """Run one JSON task and return the parsed payload.

        ``context`` carries structured task inputs; HTTP clients ignore it,
        the deterministic mock uses it instead of parsing prompt text.
        ``images`` contains ``(media_type, base64_data)`` vision inputs.
        """
        ...  # pragma: no cover


class HttpLLMClient:
    """Shared retry/backoff/budget logic for real HTTP providers."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        usage: UsageMeter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.usage = usage or UsageMeter()
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _build_request(
        self,
        system: str,
        user: str,
        max_output_tokens: int,
        *,
        images: list[tuple[str, str]] | None = None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """images: (media_type, base64_data) pairs attached to the user turn."""

        raise NotImplementedError

    def _parse_response(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        raise NotImplementedError

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        context: Any = None,
        images: list[tuple[str, str]] | None = None,
    ) -> Any:
        del context  # structured context is for the mock client only
        self.usage.check_budget()
        url, headers, body = self._build_request(
            system,
            user,
            max_output_tokens or self.config.max_output_tokens,
            images=images,
        )
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                delay = min(2**attempt, 20) + random.uniform(0, 1)
                await asyncio.sleep(delay)
            try:
                client = await self._http()
                response = await client.post(url, headers=headers, json=body)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = ProviderError(
                        f"[{task}] provider returned HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                    continue
                response.raise_for_status()
                text, input_tokens, output_tokens = self._parse_response(response.json())
                await self.usage.record(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    config=self.config,
                )
                return extract_json_payload(text)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                continue
            except httpx.HTTPStatusError as exc:
                raise ProviderError(
                    f"[{task}] provider request failed with HTTP "
                    f"{exc.response.status_code}: {exc.response.text[:300]}"
                ) from exc
        await self.usage.record(input_tokens=0, output_tokens=0, config=self.config, failed=True)
        raise ProviderError(
            f"[{task}] provider call failed after {self.config.max_retries + 1} attempts: "
            f"{last_error}"
        )


class OpenAICompatClient(HttpLLMClient):
    """Chat-completions client for OpenAI and any OpenAI-compatible endpoint."""

    def _build_request(
        self,
        system: str,
        user: str,
        max_output_tokens: int,
        *,
        images: list[tuple[str, str]] | None = None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        user_content: Any = user
        if images:
            user_content = [
                *(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    }
                    for media_type, data in images
                ),
                {"type": "text", "text": user},
            ]
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        headers = {
            "Authorization": f"Bearer {self.config.resolved_api_key()}",
            "Content-Type": "application/json",
        }
        return f"{self.config.resolved_base_url()}/chat/completions", headers, body

    def _parse_response(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected chat-completions payload: {exc}") from exc
        usage = payload.get("usage") or {}
        return (
            text,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )


class AnthropicClient(HttpLLMClient):
    """Messages-API client for Anthropic models."""

    def _build_request(
        self,
        system: str,
        user: str,
        max_output_tokens: int,
        *,
        images: list[tuple[str, str]] | None = None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        user_content: Any = user
        if images:
            user_content = [
                *(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    }
                    for media_type, data in images
                ),
                {"type": "text", "text": user},
            ]
        body: dict[str, Any] = {
            "model": self.config.model,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": max_output_tokens,
        }
        if self.config.temperature is not None:
            body["temperature"] = min(self.config.temperature, 1.0)
        headers = {
            "x-api-key": self.config.resolved_api_key(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        return f"{self.config.resolved_base_url()}/v1/messages", headers, body

    def _parse_response(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        try:
            text = "".join(
                block.get("text", "")
                for block in payload["content"]
                if block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"Unexpected messages payload: {exc}") from exc
        usage = payload.get("usage") or {}
        return (
            text,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )


def build_client(
    config: ProviderConfig, *, usage: UsageMeter | None = None
) -> HttpLLMClient:
    if config.protocol == "anthropic":
        return AnthropicClient(config, usage=usage)
    return OpenAICompatClient(config, usage=usage)


VISION_MAX_EDGE_PX = 1568
VISION_JPEG_QUALITY = 85


def encode_image_for_vision(
    path: "str | os.PathLike[str]",
    *,
    max_edge: int = VISION_MAX_EDGE_PX,
    jpeg_quality: int = VISION_JPEG_QUALITY,
) -> tuple[str, str]:
    """(media_type, base64) for a vision call, downscaled and recompressed.

    Phone photos arrive at 4000px+/many MB; sending them raw blows past
    provider read timeouts and wastes vision tokens. Anything larger than
    ``max_edge`` on its long side is resized and re-encoded as JPEG. Falls
    back to the original bytes if Pillow cannot process the file.
    """

    import base64 as _base64
    import io
    from pathlib import Path as _Path

    file_path = _Path(path)
    suffix = file_path.suffix.lower()
    fallback_media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
    raw = file_path.read_bytes()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            needs_resize = max(width, height) > max_edge
            needs_recompress = len(raw) > 600_000
            if not needs_resize and not needs_recompress:
                return fallback_media, _base64.b64encode(raw).decode("ascii")
            if needs_resize:
                scale = max_edge / max(width, height)
                image = image.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale)))
                )
            if image.mode in ("RGBA", "LA", "P"):
                from PIL import Image as _Image

                background = _Image.new("RGB", image.size, (255, 255, 255))
                converted = image.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
            return "image/jpeg", _base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - fall back to the untouched bytes
        return fallback_media, _base64.b64encode(raw).decode("ascii")
