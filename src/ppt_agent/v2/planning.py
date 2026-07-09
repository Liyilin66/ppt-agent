"""Hierarchical content planning for long decks.

A 100-page deck is planned top-down: brief -> section outline -> per-page
briefs. Every model output passes through a deterministic reconciler so page
counts always add up exactly, no matter what the model proposed.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field

from ppt_agent.models import StrictModel


MIN_PAGES = 4
MAX_PAGES = 200


class ContentBrief(StrictModel):
    """Normalized understanding of what the user wants."""

    topic: str = Field(..., min_length=1)
    deck_title: str = Field(..., min_length=1)
    subtitle: str | None = None
    audience: str = "general professional audience"
    purpose: str = "inform"
    tone: str = "professional, confident"
    language: str = "zh-CN"
    key_points: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    source_digest: str | None = Field(
        default=None, description="Condensed facts from uploaded documents / web search."
    )


class SectionOutline(StrictModel):
    title: str = Field(..., min_length=1)
    goal: str = Field(default="", description="What this section must convince or explain.")
    content_pages: int = Field(..., ge=1, le=40)
    talking_points: list[str] = Field(default_factory=list)


class DeckOutline(StrictModel):
    deck_title: str = Field(..., min_length=1)
    subtitle: str | None = None
    sections: list[SectionOutline] = Field(..., min_length=1, max_length=16)


class PageBrief(StrictModel):
    """Content contract for one model-designed page."""

    title: str = Field(..., min_length=1)
    summary: str = Field(default="")
    points: list[str] = Field(default_factory=list)
    layout_hint: Literal[
        "auto",
        "cards",
        "two_column",
        "stats",
        "timeline",
        "comparison",
        "quote",
        "chart",
        "table",
        "list",
    ] = "auto"
    data_idea: str | None = Field(
        default=None, description="Optional concrete chart/table suggestion with numbers."
    )


SlotKind = Literal["cover", "toc", "section_divider", "content", "closing"]


class PageSlot(StrictModel):
    """One position in the final deck skeleton."""

    page_number: int = Field(..., ge=1)
    kind: SlotKind
    section_index: int | None = None
    section_title: str | None = None
    brief: PageBrief | None = None


class DeckSkeleton(StrictModel):
    deck_title: str
    subtitle: str | None = None
    language: str
    total_pages: int
    outline: DeckOutline
    slots: list[PageSlot]

    def content_slots(self) -> list[PageSlot]:
        return [slot for slot in self.slots if slot.kind == "content"]


def _distribute(total: int, weights: list[float]) -> list[int]:
    """Split ``total`` into len(weights) positive ints proportional to weights."""

    count = len(weights)
    if total < count:
        raise ValueError(f"Cannot give {count} sections at least one page from {total}")
    weight_sum = sum(weights) or float(count)
    raw = [max(weight, 0.01) / weight_sum * total for weight in weights]
    floors = [max(1, math.floor(value)) for value in raw]
    while sum(floors) > total:
        index = max(range(count), key=lambda i: floors[i])
        floors[index] -= 1
    remainders = sorted(
        range(count), key=lambda i: raw[i] - floors[i], reverse=True
    )
    cursor = 0
    while sum(floors) < total:
        floors[remainders[cursor % count]] += 1
        cursor += 1
    return floors


def reconcile_outline(outline: DeckOutline, total_pages: int) -> tuple[DeckOutline, int]:
    """Fit the model's outline to the exact page budget.

    Returns the adjusted outline plus the TOC page count. Section content-page
    counts are rescaled proportionally so cover + TOC + dividers + content +
    closing == total_pages, with every section keeping at least one page.
    """

    if not MIN_PAGES <= total_pages <= MAX_PAGES:
        raise ValueError(f"total_pages must be within [{MIN_PAGES}, {MAX_PAGES}]")

    sections = list(outline.sections)
    include_toc = total_pages >= 10

    while sections:
        toc_pages = math.ceil(len(sections) / 8) if include_toc else 0
        overhead = 1 + toc_pages + len(sections) + 1
        content_budget = total_pages - overhead
        if content_budget >= len(sections):
            break
        # Too many sections for the budget: merge the smallest into its neighbor.
        if len(sections) == 1:
            raise ValueError(
                f"total_pages={total_pages} is too small for a structured deck"
            )
        smallest = min(range(len(sections)), key=lambda i: sections[i].content_pages)
        neighbor = smallest - 1 if smallest > 0 else 1
        merged = sections[neighbor].model_copy(
            update={
                "content_pages": sections[neighbor].content_pages
                + sections[smallest].content_pages,
                "talking_points": sections[neighbor].talking_points
                + sections[smallest].talking_points,
            }
        )
        sections = [
            section
            for index, section in enumerate(sections)
            if index not in (smallest, neighbor)
        ]
        sections.insert(min(neighbor, smallest), merged)

    weights = [float(section.content_pages) for section in sections]
    counts = _distribute(content_budget, weights)
    adjusted = [
        section.model_copy(update={"content_pages": count})
        for section, count in zip(sections, counts)
    ]
    return outline.model_copy(update={"sections": adjusted}), toc_pages


def build_skeleton(
    outline: DeckOutline, *, total_pages: int, language: str
) -> DeckSkeleton:
    """Lay out the exact page-by-page structure of the deck."""

    fitted, toc_pages = reconcile_outline(outline, total_pages)
    slots: list[PageSlot] = [PageSlot(page_number=1, kind="cover")]
    for index in range(toc_pages):
        slots.append(PageSlot(page_number=len(slots) + 1, kind="toc"))
    for section_index, section in enumerate(fitted.sections, start=1):
        slots.append(
            PageSlot(
                page_number=len(slots) + 1,
                kind="section_divider",
                section_index=section_index,
                section_title=section.title,
            )
        )
        for _ in range(section.content_pages):
            slots.append(
                PageSlot(
                    page_number=len(slots) + 1,
                    kind="content",
                    section_index=section_index,
                    section_title=section.title,
                )
            )
    slots.append(PageSlot(page_number=len(slots) + 1, kind="closing"))
    assert len(slots) == total_pages, f"skeleton built {len(slots)} != {total_pages}"
    return DeckSkeleton(
        deck_title=fitted.deck_title,
        subtitle=fitted.subtitle,
        language=language,
        total_pages=total_pages,
        outline=fitted,
        slots=slots,
    )


def section_start_pages(skeleton: DeckSkeleton) -> list[tuple[str, int]]:
    """(section title, divider page number) pairs for the TOC."""

    return [
        (slot.section_title or "", slot.page_number)
        for slot in skeleton.slots
        if slot.kind == "section_divider"
    ]


def reconcile_page_briefs(
    briefs: list[PageBrief], expected: int, *, section_title: str
) -> list[PageBrief]:
    """Force the per-section brief list to the expected length."""

    result = list(briefs[:expected])
    index = len(result)
    while len(result) < expected:
        index += 1
        result.append(
            PageBrief(
                title=f"{section_title} · {index}",
                summary=f"Continue the {section_title} narrative.",
                layout_hint="auto",
            )
        )
    return result


def parse_page_briefs(payload: Any) -> list[PageBrief]:
    """Parse a model reply that should be a list of PageBrief objects."""

    if isinstance(payload, dict):
        for key in ("pages", "briefs", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("Page brief reply is not a list")
    return [PageBrief.model_validate(item) for item in payload]
