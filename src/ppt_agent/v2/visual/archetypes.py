"""Typesetting engine: content in, finished PageDesign out.

For each page the engine
1. picks a composition (``choose_composition``): the profile's preference for
   this archetype, filtered by what the content can support, and steered away
   from repeating the previous page's arrangement;
2. builds the page frame the composition asks for (standard header, a
   left title panel, or none for statement pages);
3. sizes the type: sparse pages grow in 1 pt steps up to the profile's
   ``fill_target``; dense pages shrink to the floor (profile body − 4 pt,
   never below ``MIN_BODY_PT``) and then drop trailing items rather than overflow;
4. draws once, placing the content group at the profile's vertical bias.

The model never emits coordinates, so the failure modes of free layout —
9 pt body text, empty half-cards, overlapping frames — cannot be produced.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH, DeckDesign, Gradient, PageDesign
from ppt_agent.v2.visual.compositions import BY_KEY, Composition, candidates
from ppt_agent.v2.visual.content import (  # noqa: F401 - re-exported API
    AnyContent,
    ArchetypeContent,
    ChartContent,
    CompareContent,
    CompareSide,
    Insight,
    Milestone,
    MetricItem,
    MetricsContent,
    PointItem,
    PointsContent,
    ProcessContent,
    StatementContent,
    RoadmapContent,
    RoadmapRow,
    StepItem,
    SummaryContent,
    SummaryRow,
    TableContent,
    TimelineContent,
)
from ppt_agent.v2.visual.layout import (  # noqa: F401 - re-exported helpers
    MIN_BODY_PT,
    _banner_header,
    _Builder,
    _cjk_line_chars,
    _footer,
    _header,
    _panel_header,
    _Sizes,
    _takeaway,
    _widow_safe_width,
    _Zone,
)
from ppt_agent.v2.visual.profiles import StyleProfile

_ITEM_FIELD = {
    "points": ("items", 2),
    "process": ("steps", 3),
    "chart": ("insights", 1),
    "metrics": ("metrics", 2),
    "timeline": ("milestones", 3),
    "table": ("rows", 2),
    "summary": ("rows", 2),
    "roadmap": ("rows", 2),
}


# Compositions that all read as a row of equal cards, whatever their markers.
CARD_ROWS = frozenset({"points:columns", "timeline:cards", "timeline:axis", "process:chevron",
                       "process:horizontal", "metrics:cards", "metrics:columns"})
FRAMES = ("standard", "banner", "lead_band", "panel")
# What still reads well in the narrower column beside a title panel.
_PANEL_READY = frozenset({"points:rows", "points:grid", "points:list2", "points:feature", "process:vertical",
                          "chart:stacked", "compare:split", "metrics:cards", "metrics:columns"})


def choose_composition(
    content: AnyContent, profile: StyleProfile, history: Sequence[str] = ()
) -> Composition:
    """The least used arrangement in the profile's vocabulary, so a long deck does
    not lean on its favourites; never one used in the last few pages, and never a
    third row of equal cards in a row."""

    valid = candidates(content)
    if not valid:
        raise ValueError(f"No composition can lay out this {content.archetype} content")
    preferred = profile.compositions.get(content.archetype, [])
    # Stay inside the profile's own vocabulary; it is what makes a style a style.
    ordered = [c for name in preferred for c in valid if c.name == name] or valid
    used = Counter(history)
    rank = lambda c: (used[c.key], ordered.index(c))  # noqa: E731
    row_streak = len(history) >= 2 and all(key in CARD_ROWS for key in history[-2:])
    for window in (3, 1):
        recent = set(history[-window:])
        options = [c for c in ordered
                   if c.key not in recent and not (row_streak and c.key in CARD_ROWS)]
        if options:
            return min(options, key=rank)
    return min(ordered, key=rank)


def choose_frame(comp: Composition, content: AnyContent, profile: StyleProfile,
                 frames: Sequence[str] = ()) -> str:
    """The least used page skeleton this composition can sit in, never three alike in a row."""

    if not profile.pin_takeaway or comp.header != "standard" or content.archetype in ("summary", "roadmap"):
        return "standard"
    options = ["standard", "banner"]
    if content.takeaway:
        options.append("lead_band")
    if comp.key in _PANEL_READY or (content.archetype == "table" and len(content.headers) <= 3):
        options.append("panel")
    if len(frames) >= 2 and frames[-1] == frames[-2] and frames[-1] in options and len(options) > 1:
        options.remove(frames[-1])
    used = Counter(frames)
    return min(options, key=lambda frame: (used[frame], FRAMES.index(frame)))


def _drop_until_fits(content, comp: Composition, probe, zone, sizes):
    """Remove trailing items until the block fits, never past what ``comp`` accepts."""

    if content.archetype not in _ITEM_FIELD:
        return content, 0
    field_name, minimum = _ITEM_FIELD[content.archetype]
    dropped = 0
    while len(getattr(content, field_name)) > minimum:
        if comp.fn(probe, content, zone, sizes, draw=False, top=zone.y) <= zone.h:
            break
        shorter = content.model_copy(update={field_name: getattr(content, field_name)[:-1]})
        if not comp.valid(shorter):
            break
        content = shorter
        dropped += 1
    return content, dropped


def _adds_a_figure(lead: str, title: str) -> bool:
    """A lead stays only if it carries a number the title does not."""

    return any(number not in title for number in re.findall(r"\d+(?:\.\d+)?", lead))


def _drop_until_fits(content, comp: Composition, probe, zone, sizes):
    """Remove trailing items until the block fits, never past what ``comp`` accepts."""

    if content.archetype not in _ITEM_FIELD:
        return content, 0
    field_name, minimum = _ITEM_FIELD[content.archetype]
    dropped = 0
    while len(getattr(content, field_name)) > minimum:
        if comp.fn(probe, content, zone, sizes, draw=False, top=zone.y) <= zone.h:
            break
        shorter = content.model_copy(update={field_name: getattr(content, field_name)[:-1]})
        if not comp.valid(shorter):
            break
        content = shorter
        dropped += 1
    return content, dropped


def typeset_page(
    content: AnyContent,
    profile: StyleProfile,
    *,
    page_number: int,
    deck_title: str,
    composition: str | None = None,
    history: Sequence[str] = (),
    frame: str = "standard",
    frames: Sequence[str] | None = None,
) -> tuple[PageDesign, list[str]]:
    """Typeset one content page. Returns the page and notes (composition, frame, size changes).

    Pass ``frames`` (the skeletons used so far) to let the engine pick one; a page
    that will not fit its skeleton is retried on the standard one before any cut.
    """

    if profile.pin_takeaway and getattr(content, "lead", None) and not _adds_a_figure(content.lead, content.title):
        content = content.model_copy(update={"lead": None})  # a lead must earn its line
    comp = BY_KEY[composition] if composition else choose_composition(content, profile, history)
    if not comp.valid(content):
        raise ValueError(f"Composition {comp.key} cannot lay out this content")
    if frames is not None:
        frame = choose_frame(comp, content, profile, frames)
    if frame != "standard":
        try:
            page, notes = _typeset_framed(content, profile, comp, page_number, deck_title, history, frame)
            if not any(note.startswith(("dropped ", "content exceeds")) or "switched" in note for note in notes):
                return page, notes
        except ValueError:
            pass
        frame = "standard"
    return _typeset_framed(content, profile, comp, page_number, deck_title, history, frame)


def _typeset_framed(content, profile, comp, page_number, deck_title, history, frame):
    b = _Builder(profile)
    margin = profile.margin_x
    pinned = profile.pin_takeaway and comp.header == "standard"
    takeaway = content.takeaway if frame in ("standard",) else None
    if frame == "panel":
        footer_top = _footer(b, content, page_number=page_number, deck_title=deck_title)
        x = _panel_header(b, content, height=footer_top + 6, takeaway=content.takeaway)
        zone = _Zone(x=x, y=profile.title_top, w=CANVAS_WIDTH - x - margin, bottom=footer_top)
        bias = 0.3
    elif frame == "banner":
        content_top = _banner_header(b, content)
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title)
        zone = _Zone(x=margin, y=content_top, w=CANVAS_WIDTH - 2 * margin, bottom=content_bottom)
        bias = profile.vertical_bias
        takeaway = content.takeaway
    elif frame == "lead_band":
        content_top = _header(b, content) - profile.content_gap + 16
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title)
        full_width = _Zone(x=margin, y=content_top, w=CANVAS_WIDTH - 2 * margin, bottom=content_bottom)
        band_h = _takeaway(b, content.takeaway, full_width, y=content_top)
        zone = _Zone(x=margin, y=content_top + band_h + 20, w=CANVAS_WIDTH - 2 * margin, bottom=content_bottom)
        bias = profile.vertical_bias
    elif comp.header == "panel":
        x = _panel_header(b, content)
        content_top = profile.title_top + 40
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title,
                                 x=x, width=CANVAS_WIDTH - x - margin)
        zone = _Zone(x=x, y=content_top, w=CANVAS_WIDTH - x - margin, bottom=content_bottom)
        bias = 0.35
    elif comp.header == "none":
        if comp.name == "dark":
            b.shape(x=0, y=0, w=CANVAS_WIDTH, h=CANVAS_HEIGHT, fill="primary")
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title,
                                 color="primary_soft" if comp.name == "dark" else "muted")
        zone = _Zone(x=margin, y=profile.title_top, w=CANVAS_WIDTH - 2 * margin,
                     bottom=content_bottom)
        bias = 0.42
    else:
        content_top = _header(b, content)
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title)
        zone = _Zone(x=margin, y=content_top, w=CANVAS_WIDTH - 2 * margin, bottom=content_bottom)
        bias = profile.vertical_bias

    full = zone
    band = 0.0
    band_gap = profile.item_gap * 1.5
    if takeaway:
        band = _takeaway(b, takeaway, zone, measure_only=True)
        zone = _Zone(x=zone.x, y=zone.y, w=zone.w, bottom=zone.bottom - band - band_gap)

    s = profile.sizes
    base = _Sizes(heading=s.heading, body=s.body, small=s.small, marker=s.marker, stat=s.stat)
    floor = max(MIN_BODY_PT, s.body - 4)
    probe = _Builder(profile)

    chosen = base
    if comp.fn(probe, content, zone, base, draw=False, top=zone.y) <= zone.h:
        for step in (1, 2, 3, 4, 5, 6) if profile.pin_takeaway else (1, 2, 3, 4):
            candidate = base.bumped(step, floor)
            if comp.fn(probe, content, zone, candidate, draw=False,
                       top=zone.y) > zone.h * profile.fill_target:
                break
            chosen = candidate
    else:
        for step in (-1, -2, -3, -4, -5, -6):
            chosen = base.bumped(step, floor)
            if comp.fn(probe, content, zone, chosen, draw=False, top=zone.y) <= zone.h:
                break
        else:
            # Too dense for this arrangement: try the profile's other compositions
            # (same frame type) at the floor before cutting any content.
            alternatives = [
                c for c in candidates(content)
                if c is not comp and c.header == comp.header
                and c.name in profile.compositions.get(content.archetype, [])
            ]
            # Keep the rhythm rule when switching: last page's arrangement goes last.
            alternatives.sort(key=lambda c: bool(history) and history[-1] == c.key)
            switched = next(
                (c for c in alternatives
                 if c.fn(probe, content, zone, chosen, draw=False, top=zone.y) <= zone.h),
                None,
            )
            if switched is None:
                # Last resort before cutting content: any arrangement of this
                # archetype that fits, even outside the profile's vocabulary.
                switched = next(
                    (c for c in candidates(content)
                     if c is not comp and c.header == comp.header
                     and c.fn(probe, content, zone, chosen, draw=False, top=zone.y) <= zone.h),
                    None,
                )
            if switched is not None:
                b.notes.append(f"{comp.key} too dense; switched")
                comp = switched
            else:
                content, dropped = _drop_until_fits(content, comp, probe, zone, chosen)
                b.notes.append(
                    f"dropped {dropped} trailing item(s) that did not fit" if dropped
                    else "content exceeds the zone at minimum sizes"
                )

    block = comp.fn(probe, content, zone, chosen, draw=False, top=zone.y)
    if block > zone.h + 0.5:
        raise ValueError(
            f"{comp.key}: content does not fit even at minimum sizes "
            f"({block:.0f} > {zone.h:.0f}); shorten it or split the page"
        )
    if pinned:
        # Grow the content toward the band, but not so far that cards turn hollow.
        zone = replace(zone, target=min(zone.h, block * 1.35))
        block = comp.fn(probe, content, zone, chosen, draw=False, top=zone.y)
        top = zone.y + max(0.0, zone.h - block) * bias
        comp.fn(b, content, zone, chosen, draw=True, top=top)
        if takeaway:
            _takeaway(b, takeaway, full)
    else:
        group = block + (band_gap + band if takeaway else 0.0)
        top = full.y + max(0.0, full.h - group) * bias
        comp.fn(b, content, zone, chosen, draw=True, top=top)
        if takeaway:
            _takeaway(b, takeaway, full, y=top + block + band_gap)
    b.notes.insert(0, f"frame:{frame}")
    b.notes.insert(0, comp.key)
    if chosen != base:
        b.notes.append(f"body size {base.body:g} -> {chosen.body:g} pt")

    page = PageDesign(
        page_number=page_number,
        role="content",
        title=content.title,
        background="background",
        background_gradient=(
            Gradient(start="background", end="surface_alt", angle_deg=300)
            if profile.background_gradient
            else None
        ),
        show_chrome=False,
        elements=b.elements,
        speaker_notes=content.speaker_notes,
    )
    return page, b.notes


def typeset_deck(
    contents: Sequence[AnyContent],
    profile: StyleProfile,
    *,
    deck_title: str,
) -> tuple[DeckDesign, list[list[str]]]:
    pages, notes, history, frames = [], [], [], []
    for index, content in enumerate(contents, start=1):
        page, page_notes = typeset_page(content, profile, page_number=index,
                                        deck_title=deck_title, history=history, frames=frames)
        history.append(page_notes[0])
        frames.append(page_notes[1].removeprefix("frame:"))
        pages.append(page)
        notes.append(page_notes)
    deck = DeckDesign(deck_title=deck_title, theme=profile.theme(), pages=pages)
    return deck, notes


def assemble_deck(
    contents: Sequence[AnyContent],
    profile: StyleProfile,
    *,
    deck_title: str,
    subtitle: str | None = None,
) -> tuple[DeckDesign, list[list[str]]]:
    """Cover + TOC + content pages + closing, with sections taken from kickers.

    Used for sample decks; the generation pipeline builds sections from its
    skeleton and calls ``typeset_structural`` directly.
    """

    from ppt_agent.v2.visual.structural import typeset_structural

    first_content = 3
    sections: list[tuple[str, int]] = []
    for offset, content in enumerate(contents):
        label = content.kicker or content.title
        if not sections or sections[-1][0] != label:
            sections.append((label, first_content + offset))
    pages = [
        typeset_structural("cover", profile, page_number=1, deck_title=deck_title,
                           subtitle=subtitle, sections=sections),
        typeset_structural("toc", profile, page_number=2, deck_title=deck_title,
                           sections=sections),
    ]
    notes: list[list[str]] = [["structural:cover"], ["structural:toc"]]
    history: list[str] = []
    frames: list[str] = []
    for offset, content in enumerate(contents):
        page, page_notes = typeset_page(content, profile, page_number=first_content + offset,
                                        deck_title=deck_title, history=history, frames=frames)
        history.append(page_notes[0])
        frames.append(page_notes[1].removeprefix("frame:"))
        pages.append(page)
        notes.append(page_notes)
    pages.append(typeset_structural("closing", profile, page_number=len(pages) + 1,
                                    deck_title=deck_title))
    notes.append(["structural:closing"])
    return DeckDesign(deck_title=deck_title, subtitle=subtitle, theme=profile.theme(),
                      pages=pages), notes
