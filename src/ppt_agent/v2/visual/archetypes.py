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

from collections.abc import Sequence

from ppt_agent.v2.ir import CANVAS_WIDTH, DeckDesign, Gradient, PageDesign
from ppt_agent.v2.visual.compositions import BY_KEY, Composition, candidates
from ppt_agent.v2.visual.content import (  # noqa: F401 - re-exported API
    AnyContent,
    ArchetypeContent,
    ChartContent,
    Insight,
    MetricItem,
    MetricsContent,
    PointItem,
    PointsContent,
    ProcessContent,
    StatementContent,
    StepItem,
)
from ppt_agent.v2.visual.layout import (  # noqa: F401 - re-exported helpers
    MIN_BODY_PT,
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
}


def choose_composition(
    content: AnyContent, profile: StyleProfile, history: Sequence[str] = ()
) -> Composition:
    """Profile preference first, steered away from arrangements used on the
    last few pages so a deck never reads as one template repeated."""

    valid = candidates(content)
    if not valid:
        raise ValueError(f"No composition can lay out this {content.archetype} content")
    preferred = profile.compositions.get(content.archetype, [])
    # Stay inside the profile's own vocabulary; it is what makes a style a style.
    ordered = [c for name in preferred for c in valid if c.name == name] or valid
    # Pass 1: avoid anything used in the last three pages; pass 2: only avoid
    # the previous page; otherwise accept the top preference.
    for window in (3, 1):
        recent = set(history[-window:])
        for composition in ordered:
            if composition.key not in recent:
                return composition
    return ordered[0]


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
) -> tuple[PageDesign, list[str]]:
    """Typeset one content page. Returns the page and notes (composition, size changes)."""

    comp = BY_KEY[composition] if composition else choose_composition(content, profile, history)
    if not comp.valid(content):
        raise ValueError(f"Composition {comp.key} cannot lay out this content")
    b = _Builder(profile)
    margin = profile.margin_x
    if comp.header == "panel":
        x = _panel_header(b, content)
        content_top = profile.title_top + 40
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title,
                                 x=x, width=CANVAS_WIDTH - x - margin)
        zone = _Zone(x=x, y=content_top, w=CANVAS_WIDTH - x - margin, bottom=content_bottom)
        bias = 0.35
    elif comp.header == "none":
        content_bottom = _footer(b, content, page_number=page_number, deck_title=deck_title)
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
    if content.takeaway:
        band = _takeaway(b, content.takeaway, zone, measure_only=True)
        zone = _Zone(x=zone.x, y=zone.y, w=zone.w, bottom=zone.bottom - band - band_gap)

    s = profile.sizes
    base = _Sizes(heading=s.heading, body=s.body, small=s.small, marker=s.marker, stat=s.stat)
    floor = max(MIN_BODY_PT, s.body - 4)
    probe = _Builder(profile)

    chosen = base
    if comp.fn(probe, content, zone, base, draw=False, top=zone.y) <= zone.h:
        for step in (1, 2, 3, 4):
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
    group = block + (band_gap + band if content.takeaway else 0.0)
    top = full.y + max(0.0, full.h - group) * bias
    comp.fn(b, content, zone, chosen, draw=True, top=top)
    if content.takeaway:
        _takeaway(b, content.takeaway, full, y=top + block + band_gap)
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
    pages, notes, history = [], [], []
    for index, content in enumerate(contents, start=1):
        page, page_notes = typeset_page(content, profile, page_number=index,
                                        deck_title=deck_title, history=history)
        history.append(page_notes[0])
        pages.append(page)
        notes.append(page_notes)
    deck = DeckDesign(deck_title=deck_title, theme=profile.theme(), pages=pages)
    return deck, notes
