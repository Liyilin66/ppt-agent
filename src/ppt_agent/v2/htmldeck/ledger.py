"""Count how often each fact is used across a deck.

A fact is a figure together with its unit ("95%", "6233亿"). Leading a page
means sitting in its title or being its headline figure; anything else is
support. The rule a long deck must keep: a fact leads at most once and
supports at most twice.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ppt_agent.v2.evidence_check import _NUMBER, _YEAR, _key

MAX_LEAD, MAX_SUPPORT = 1, 2
_SKIP_FIELDS = {"source", "speaker_notes", "ref", "archetype", "unit_format", "chart", "phases",
                "start", "end", "kicker"}


def fact_keys(text: str) -> list[str]:
    """Figures in ``text`` as comparable keys; years and bare counts are not facts."""

    keys = []
    for match in _NUMBER.finditer(text or ""):
        token = match.group().strip()
        if _YEAR.fullmatch(token) or re.fullmatch(r"\d{1,2}", token):
            continue
        try:
            value, unit = _key(token)
        except (TypeError, ArithmeticError):
            continue
        keys.append(f"{value.copy_abs().normalize():f}{unit}")
    return keys


def _visible(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [pair for key, child in value.items() if key not in _SKIP_FIELDS
                for pair in _visible(child, f"{path}.{key}".strip("."))]
    if isinstance(value, list):
        return [pair for child in value for pair in _visible(child, path)]
    if isinstance(value, bool) or value is None:
        return []
    return [(path, f"{value:g}" if isinstance(value, float) else str(value))]


def page_facts(content: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(leading facts, supporting facts) of one page of content."""

    lead: set[str] = set()
    everything: set[str] = set()
    for path, text in _visible(content):
        keys = fact_keys(text)
        everything.update(keys)
        if path in ("title", "statement", "highlight.value"):
            lead.update(keys)
    metrics = content.get("metrics") or []
    if metrics and isinstance(metrics[0], dict):
        lead.update(fact_keys(str(metrics[0].get("value", ""))))
    return lead, everything - lead


@dataclass
class FactLedger:
    leads: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    supports: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))

    def register(self, page_number: int, content: dict[str, Any]) -> list[str]:
        """Record a page; return the facts it leads with that already led a page."""

        lead, support = page_facts(content)
        clashes = sorted(fact for fact in lead if self.leads.get(fact))
        for fact in lead:
            self.leads[fact].append(page_number)
        for fact in support:
            self.supports[fact].append(page_number)
        return clashes

    def used(self) -> list[str]:
        return sorted(set(self.leads) | set(self.supports))

    def audit(self) -> dict[str, Any]:
        over_lead = {fact: pages for fact, pages in self.leads.items() if len(pages) > MAX_LEAD}
        over_support = {fact: pages for fact, pages in self.supports.items() if len(pages) > MAX_SUPPORT}
        pages_per_fact = {
            fact: sorted(set(self.leads.get(fact, [])) | set(self.supports.get(fact, [])))
            for fact in self.used()
        }
        most = max(pages_per_fact.items(), key=lambda item: len(item[1]), default=("", []))
        return {
            "facts": len(pages_per_fact),
            "led_more_than_once": {fact: pages for fact, pages in sorted(over_lead.items())},
            "supported_more_than_twice": {fact: pages for fact, pages in sorted(over_support.items())},
            "most_used": {"fact": most[0], "pages": most[1]},
        }
