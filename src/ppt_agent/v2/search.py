"""Pluggable web research for deck grounding.

Search is optional: without a key the pipeline simply skips enrichment. The
default implementation is Tavily (one small POST per query), chosen because
it returns LLM-ready snippets without HTML scraping.
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx
from pydantic import Field

from ppt_agent.models import StrictModel


class SearchResult(StrictModel):
    title: str = ""
    url: str = ""
    snippet: str = ""


class SearchProvider(Protocol):
    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        ...  # pragma: no cover


class TavilySearchProvider:
    """Tavily search API adapter (TAVILY_API_KEY)."""

    def __init__(self, api_key: str | None = None, *, timeout_seconds: float = 30) -> None:
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise ValueError("TavilySearchProvider requires TAVILY_API_KEY")

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", ""))[:600],
            )
            for item in payload.get("results", [])
        ]


def default_search_provider() -> SearchProvider | None:
    """Tavily when a key is configured; otherwise no enrichment."""

    if os.environ.get("TAVILY_API_KEY"):
        return TavilySearchProvider()
    return None


def format_search_digest(results: list[SearchResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"- {result.title} ({result.url}): {result.snippet}")
    return "\n".join(lines)
