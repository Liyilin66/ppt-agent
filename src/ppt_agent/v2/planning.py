"""Hierarchical content planning for long decks.

A 100-page deck is planned top-down: brief -> section outline -> per-page
briefs. Every model output passes through a deterministic reconciler so page
counts always add up exactly, no matter what the model proposed.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, ValidationError

from ppt_agent.models import StrictModel


MIN_PAGES = 4
MAX_PAGES = 100


class ContentBrief(StrictModel):
    """Normalized understanding of what the user wants."""

    deck_type: Literal["consulting", "launch", "training", "corporate"] = "corporate"
    deck_type_reason: str = ""
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
    speaker_notes: str = Field(
        default="", description="Spoken script for this page; written into the PPTX notes."
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
    planning_events: list[dict[str, Any]] = Field(default_factory=list)

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


def _structure_budget(
    total_pages: int, section_count: int, include_section_dividers: bool | None = None
) -> tuple[int, bool]:
    """Reserve fixed pages and use dividers only within the 25% allowance.

    The fixed pages take precedence for short decks, where they alone can
    exceed the allowance. TOCs keep their existing eight-section capacity.
    """

    toc_pages = math.ceil(section_count / 8)
    include_dividers = (
        include_section_dividers is not False
        and (2 + toc_pages + section_count) * 4 <= total_pages
    )
    return toc_pages, include_dividers


def reconcile_outline(
    outline: DeckOutline, total_pages: int, *, include_section_dividers: bool | None = None
) -> tuple[DeckOutline, int]:
    """Fit the model's outline to the exact page budget.

    Returns the adjusted outline plus the TOC page count. Section content-page
    counts are rescaled proportionally so cover + TOC + dividers + content +
    closing == total_pages, with every section keeping at least one page.
    """

    if not MIN_PAGES <= total_pages <= MAX_PAGES:
        raise ValueError(f"total_pages must be within [{MIN_PAGES}, {MAX_PAGES}]")

    sections = list(outline.sections)
    while sections:
        toc_pages, include_dividers = _structure_budget(
            total_pages, len(sections), include_section_dividers
        )
        overhead = 2 + toc_pages + (len(sections) if include_dividers else 0)
        content_budget = total_pages - overhead
        fixed_fits = 2 + toc_pages <= max(3, total_pages // 4)
        if content_budget >= len(sections) and fixed_fits:
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
    outline: DeckOutline, *, total_pages: int, language: str,
    include_section_dividers: bool | None = None,
) -> DeckSkeleton:
    """Lay out the exact page-by-page structure of the deck."""

    fitted, toc_pages = reconcile_outline(
        outline, total_pages, include_section_dividers=include_section_dividers
    )
    _, include_dividers = _structure_budget(
        total_pages, len(fitted.sections), include_section_dividers
    )
    slots: list[PageSlot] = [PageSlot(page_number=1, kind="cover")]
    for index in range(toc_pages):
        slots.append(PageSlot(page_number=len(slots) + 1, kind="toc"))
    for section_index, section in enumerate(fitted.sections, start=1):
        if include_dividers:
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
    """TOC targets: each section's divider, or its first content page."""

    starts: list[tuple[str, int]] = []
    seen: set[int] = set()
    for slot in skeleton.slots:
        if slot.section_index is not None and slot.section_index not in seen:
            starts.append((slot.section_title or "", slot.page_number))
            seen.add(slot.section_index)
    return starts


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


def normalize_layout_hint(hint: Any) -> str:
    """Treat layout suggestions as hints, without discarding page content."""
    supported = PageBrief.model_fields["layout_hint"].annotation.__args__
    if isinstance(hint, str):
        hint = hint.strip().lower()
        if hint in supported:
            return hint
        if hint == "columns" or hint.endswith(("_column", "_columns")):
            return "cards"
    return "auto"


def parse_page_briefs(
    payload: Any, *, fallback_briefs: list[PageBrief] | None = None,
    page_numbers: list[int] | None = None, events: list[dict[str, Any]] | None = None,
) -> list[PageBrief]:
    """Normalize suggestions and recover invalid pages independently.

    Without fallback content, invalid non-hint fields still raise. Callers that
    know the target slots can supply one fallback per page, including omissions.
    """
    if isinstance(payload, dict):
        for key in ("pages", "briefs", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("Page brief reply is not a list")
    count = len(fallback_briefs) if fallback_briefs is not None else len(payload)
    result: list[PageBrief] = []
    for index in range(count):
        number = page_numbers[index] if page_numbers is not None else index + 1
        item = payload[index] if index < len(payload) else None
        try:
            if isinstance(item, dict):
                item = dict(item)
                original = item.get("layout_hint", "auto")
                hint = normalize_layout_hint(original)
                item["layout_hint"] = hint
                if original != hint and events is not None:
                    events.append({"page_number": number, "action": "normalized",
                                   "original_hint": original, "normalized_hint": hint})
            result.append(PageBrief.model_validate(item))
        except (ValidationError, ValueError) as exc:
            if fallback_briefs is None:
                raise
            result.append(fallback_briefs[index])
            if events is not None:
                events.append({"page_number": number, "action": "fallback",
                               "reason": "missing_page" if item is None else str(exc)})
    return result


class EditablePage(StrictModel):
    """One content page as shown in the pre-generation review UI."""

    title: str = Field(..., min_length=1)
    summary: str = ""
    points: list[str] = Field(default_factory=list)
    layout_hint: str = "auto"
    data_idea: str | None = None
    speaker_notes: str = ""

    def to_brief(self) -> PageBrief:
        hint = normalize_layout_hint(self.layout_hint)
        return PageBrief(
            title=self.title,
            summary=self.summary,
            points=[point for point in (item.strip() for item in self.points) if point],
            layout_hint=hint,
            data_idea=self.data_idea or None,
            speaker_notes=self.speaker_notes.strip(),
        )


class EditableSection(StrictModel):
    title: str = Field(..., min_length=1)
    goal: str = ""
    pages: list[EditablePage] = Field(..., min_length=1, max_length=40)


class EditableDeckPlan(StrictModel):
    """User-facing, editable form of brief + skeleton_with_briefs."""

    deck_title: str = Field(..., min_length=1)
    subtitle: str | None = None
    language: str = "zh-CN"
    include_section_dividers: bool | None = None
    deck_type: Literal["consulting", "launch", "training", "corporate"] = "corporate"
    deck_type_reason: str = ""
    sections: list[EditableSection] = Field(..., min_length=1, max_length=16)

    def structural_pages(self) -> int:
        content = sum(len(section.pages) for section in self.sections)
        section_count = len(self.sections)
        fixed = 2 + math.ceil(section_count / 8)
        total_with_dividers = content + fixed + section_count
        _, include_dividers = _structure_budget(
            total_with_dividers, section_count, self.include_section_dividers
        )
        return fixed + (section_count if include_dividers else 0)

    def total_pages(self) -> int:
        return self.structural_pages() + sum(len(section.pages) for section in self.sections)


def editable_plan_from_skeleton(skeleton: DeckSkeleton) -> EditableDeckPlan:
    """Project an enriched skeleton into the user-editable plan shape."""

    sections: list[EditableSection] = []
    for index, section in enumerate(skeleton.outline.sections, start=1):
        pages = [
            EditablePage(
                title=slot.brief.title if slot.brief else f"{section.title} · {position}",
                summary=slot.brief.summary if slot.brief else "",
                points=list(slot.brief.points) if slot.brief else [],
                layout_hint=slot.brief.layout_hint if slot.brief else "auto",
                data_idea=slot.brief.data_idea if slot.brief else None,
                speaker_notes=slot.brief.speaker_notes if slot.brief else "",
            )
            for position, slot in enumerate(
                (slot for slot in skeleton.slots if slot.kind == "content" and slot.section_index == index),
                start=1,
            )
        ]
        sections.append(EditableSection(title=section.title, goal=section.goal, pages=pages))
    return EditableDeckPlan(
        deck_title=skeleton.deck_title,
        subtitle=skeleton.subtitle,
        language=skeleton.language,
        include_section_dividers=any(slot.kind == "section_divider" for slot in skeleton.slots),
        sections=sections,
    )


def skeleton_from_editable_plan(plan: EditableDeckPlan) -> DeckSkeleton:
    """Rebuild an exact skeleton (with briefs) from a user-edited plan.

    Raises ValueError when the edited plan falls outside the supported
    [MIN_PAGES, MAX_PAGES] deck size.
    """

    total = plan.total_pages()
    if not MIN_PAGES <= total <= MAX_PAGES:
        raise ValueError(
            f"Edited plan needs {total} pages; supported range is {MIN_PAGES}-{MAX_PAGES}."
        )
    outline = DeckOutline(
        deck_title=plan.deck_title,
        subtitle=plan.subtitle,
        sections=[
            SectionOutline(
                title=section.title,
                goal=section.goal,
                content_pages=len(section.pages),
                talking_points=[page.title for page in section.pages],
            )
            for section in plan.sections
        ],
    )
    skeleton = build_skeleton(
        outline, total_pages=total, language=plan.language,
        include_section_dividers=plan.include_section_dividers,
    )
    if skeleton.outline.sections != outline.sections:
        raise ValueError(
            "Edited plan exceeds the structural page allowance; "
            "merge sections or add content pages to preserve every edited page."
        )
    briefs_by_section = {
        index: [page.to_brief() for page in section.pages]
        for index, section in enumerate(plan.sections, start=1)
    }
    cursor: dict[int, int] = {}
    slots: list[PageSlot] = []
    for slot in skeleton.slots:
        if slot.kind == "content" and slot.section_index is not None:
            position = cursor.get(slot.section_index, 0)
            cursor[slot.section_index] = position + 1
            slot = slot.model_copy(update={"brief": briefs_by_section[slot.section_index][position]})
        slots.append(slot)
    return skeleton.model_copy(update={"slots": slots})
