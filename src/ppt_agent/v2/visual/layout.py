"""Typesetting primitives shared by every composition.

Measuring (with a safety margin the renderer will honour), widow control for
Chinese text, the element builder, and the page furniture: header, footer and
takeaway band. Compositions only decide *where* blocks go; these helpers
decide how a block of text is sized and drawn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ppt_agent.v2.design import TYPE_SCALE
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    Frame,
    LineItem,
    PageElement,
    ShapeItem,
    TextItem,
)
from ppt_agent.v2.metrics import text_height_units, text_width_units
from ppt_agent.v2.visual.content import PageCopy
from ppt_agent.v2.visual.profiles import StyleProfile

MIN_BODY_PT = 12.0  # nothing a reader is meant to read goes below this
FOOTER_H = 22.0


_WIDOW_ROLES = frozenset({"title", "subtitle", "h3", "body", "body_small"})


def _height(text: str, role: str, size: float, width: float) -> float:
    """Estimated block height plus a small safety margin, in canvas units.

    Measured at the width the builder will actually draw: reading roles are
    narrowed to avoid one-character last lines, which can add a line.
    """

    if role in _WIDOW_ROLES and "\n" not in text:
        width = _widow_safe_width(text, size, width)
    spacing = TYPE_SCALE[role].line_spacing
    estimate = text_height_units(text, size, spacing, width)
    # The overflow metric breaks Latin words anywhere; PowerPoint moves the
    # whole word down. Take whichever wrap needs more lines.
    lines = sum(len(_cjk_line_chars(part, size, width)) if part else 1
                for part in text.split("\n"))
    by_tokens = lines * size * spacing * 96 / 72
    return max(estimate, by_tokens) * 1.04 + 2.0


def _cjk_line_chars(text: str, size: float, width: float, latin_scale: float = 1.0) -> list[int]:
    """Characters per rendered line, assuming exact 1 em CJK advance.

    YaHei and DengXian set CJK glyphs at exactly one em, so this is closer to
    PowerPoint's real wrap than the deliberately conservative overflow metric.
    """

    em_px = size * 96 / 72
    counts, current, used = [], 0, 0.0
    for token in _TOKEN.findall(text):
        advance = sum(
            em_px * (em if em == 1.0 else em * latin_scale)
            for em in (_advance_em(char) for char in token)
        )
        if used + advance > width and current:
            if token[0] in _NO_LINE_START and current > 1:
                # Kinsoku: punctuation may not start a line, so PowerPoint
                # carries the previous character down with it.
                counts.append(current - 1)
                current, used = 1, em_px
            else:
                counts.append(current)
                current, used = 0, 0.0
        current += len(token)
        used += advance
    counts.append(current)
    return counts


# Latin words and numbers never break mid-token in PowerPoint; CJK breaks anywhere.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,%+\-/]*|.", re.S)


def _advance_em(char: str) -> float:
    """Approximate YaHei / DengXian advance widths (CJK is exactly 1 em)."""

    if ord(char) > 0x2E80:
        return 1.0
    if char == " ":
        return 0.3
    if char in "Iijl|.,:;!'":
        return 0.28
    if char.isupper():
        return 0.62
    if char.isdigit():
        return 0.55
    return 0.5


_NO_LINE_START = set("，。、；：？！）》」』”’,.;:?!)%")


def _widow_safe_width(text: str, size: float, width: float) -> float:
    """Narrow a frame by up to three characters so no line ends with 1–2 chars."""

    em_px = size * 96 / 72

    def safe(candidate: float) -> bool:
        # Latin widths differ between YaHei and DengXian; require no widow
        # under both a wide and a narrow estimate.
        for scale in (1.0, 0.85):
            lines = _cjk_line_chars(text, size, candidate, scale)
            if len(lines) > 1 and lines[-1] < 3:
                return False
        return True

    for shave in range(0, 5):
        candidate = width - shave * em_px
        if safe(candidate):
            return candidate
    return width


@dataclass
class _Sizes:
    heading: float
    body: float
    small: float
    marker: float
    stat: float

    def bumped(self, step: float, floor: float) -> "_Sizes":
        return _Sizes(
            heading=max(floor + 1, self.heading + step),
            body=max(floor, self.body + step),
            small=max(floor, self.small + step),
            marker=max(floor, self.marker + step * 1.5),
            stat=max(floor + 8, self.stat + step * 2),
        )


@dataclass
class _Builder:
    profile: StyleProfile
    elements: list[PageElement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _count: int = 0

    def _id(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}_{self._count}"

    def text(
        self, text: str, *, x: float, y: float, w: float, h: float, role: str, size: float,
        color: str = "text", bold: bool | None = None, align: str = "left",
        valign: str = "top", bullet: str = "none",
    ) -> None:
        if role in _WIDOW_ROLES and align == "left" and "\n" not in text:
            w = _widow_safe_width(text, size, w)
        self.elements.append(
            TextItem(
                id=self._id("t"), frame=Frame(x=x, y=y, w=w, h=h), text=text, role=role,
                size_pt=size, color=color, bold=bold, align=align, valign=valign,
                bullet=bullet,
            )
        )

    def shape(
        self, *, x: float, y: float, w: float, h: float, shape: str = "rectangle",
        fill: str | None = "surface", stroke: str | None = None, stroke_width: float = 1.0,
    ) -> None:
        self.elements.append(
            ShapeItem(
                id=self._id("s"), frame=Frame(x=x, y=y, w=w, h=h), shape=shape, fill=fill,
                stroke=stroke, stroke_width=stroke_width,
            )
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: str, width: float) -> None:
        self.elements.append(
            LineItem(id=self._id("l"), x1=x1, y1=y1, x2=x2, y2=y2, color=color, width=width)
        )

    def card(self, x: float, y: float, w: float, h: float) -> None:
        style = self.profile.card_style
        if style == "none":
            return
        shape = "rounded_rectangle" if self.profile.card_radius > 0 else "rectangle"
        if style == "outlined":
            self.shape(x=x, y=y, w=w, h=h, shape=shape, fill="surface", stroke="surface_alt")
        else:
            self.shape(x=x, y=y, w=w, h=h, shape=shape, fill="surface")


@dataclass
class _Zone:
    x: float
    y: float
    w: float
    bottom: float

    @property
    def h(self) -> float:
        return self.bottom - self.y


def _header(b: _Builder, copy: PageCopy) -> float:
    """Draw kicker, title and lead; return the y where content may start."""

    p = b.profile
    s = p.sizes
    x = p.margin_x
    width = CANVAS_WIDTH - 2 * p.margin_x
    y = p.title_top
    if copy.kicker:
        if p.kicker_as_pill:
            text_w = text_width_units(copy.kicker, s.kicker) + 4
            pill_h = s.kicker * 1.333 * 1.25 + 12
            b.shape(x=x, y=y, w=text_w + 32, h=pill_h, shape="pill", fill="primary_soft")
            b.text(copy.kicker, x=x + 16, y=y, w=text_w, h=pill_h, role="kicker",
                   size=s.kicker, color="primary", valign="middle")
            y += pill_h + 14
        else:
            kicker_color = "secondary" if p.dark else ("muted" if p.name == "consulting" else "primary")
            h = _height(copy.kicker, "kicker", s.kicker, width)
            b.text(copy.kicker, x=x, y=y, w=width, h=h, role="kicker", size=s.kicker,
                   color=kicker_color)
            y += h + 8
    title_x = x + (20 if p.title_decor == "bar_left" else 0)
    title_w = width - (title_x - x)
    title_h = _height(copy.title, "title", s.title, title_w)
    if p.title_decor == "bar_left":
        line_h = s.title * 1.333 * 1.15
        b.shape(x=x, y=y + line_h * 0.18, w=6, h=line_h * 0.64, shape="rectangle", fill="primary")
    b.text(copy.title, x=title_x, y=y, w=title_w, h=title_h, role="title", size=s.title,
           color="text")
    y += title_h
    if copy.lead:
        y += 10
        lead_h = _height(copy.lead, "subtitle", s.lead, title_w)
        b.text(copy.lead, x=title_x, y=y, w=title_w, h=lead_h, role="subtitle", size=s.lead,
               color="muted")
        y += lead_h
    if p.title_decor == "rule_below":
        y += 16
        b.line(x, y, x + width, y, color="surface_alt", width=0.75)
    return y + p.content_gap


def _footer(
    b: _Builder, copy: PageCopy, *, page_number: int, deck_title: str,
    x: float | None = None, width: float | None = None,
) -> float:
    """Draw source line and page furniture; return the y content must stay above."""

    p = b.profile
    s = p.sizes
    x = p.margin_x if x is None else x
    width = CANVAS_WIDTH - p.margin_x - x if width is None else width
    y = CANVAS_HEIGHT - 30 - FOOTER_H
    number = f"{page_number:02d}"
    b.text(number, x=x + width - 60, y=y, w=60, h=FOOTER_H, role="caption", size=s.caption,
           color="muted", align="right", valign="middle")
    left_parts = []
    if copy.source:
        left_parts.append(copy.source)
    elif p.show_footer_title:
        left_parts.append(deck_title)
    if left_parts:
        b.text("　".join(left_parts), x=x, y=y, w=width - 80, h=FOOTER_H, role="caption",
               size=s.caption, color="muted", valign="middle")
    return y - 18


def _takeaway(
    b: _Builder, text: str, zone: _Zone, *, y: float | None = None, measure_only: bool = False
) -> float:
    """Conclusion band; drawn at ``y`` (default: bottom of the zone). Returns its height."""

    p = b.profile
    s = p.sizes
    size = s.lead
    pad_x = 0 if p.card_style == "none" else 22
    pad_y = 0 if p.card_style == "none" else 14
    label = "要点" if p.name == "training" else ""
    label_w = text_width_units(label, size) + 14 if label else 0
    inner_w = zone.w - 18 - 2 * pad_x - label_w
    text_h = _height(text, "h3", size, inner_w)
    h = text_h + 2 * pad_y
    if measure_only:
        return h
    if y is None:
        y = zone.bottom - h
    if p.card_style != "none":
        fill = "surface_alt" if p.name == "training" else "primary_soft"
        shape = "rounded_rectangle" if p.card_radius > 0 else "rectangle"
        b.shape(x=zone.x, y=y, w=zone.w, h=h, shape=shape, fill=fill)
    bar_color = "accent" if p.name in ("consulting", "training", "launch") else "primary"
    b.shape(x=zone.x, y=y + (pad_y or 2), w=4, h=h - 2 * (pad_y or 2), fill=bar_color)
    text_x = zone.x + 18 + pad_x
    if label:
        b.text(label, x=text_x, y=y + pad_y, w=label_w, h=text_h, role="h3", size=size,
               color="accent")
        text_x += label_w
    color = "primary" if p.name == "corporate" else "text"
    b.text(text, x=text_x, y=y + pad_y, w=inner_w, h=text_h, role="h3", size=size, color=color)
    return h


def _marker_height(p: StyleProfile, sz: _Sizes) -> float:
    if p.marker == "big_number":
        return _height("00", "stat", sz.marker, 400)
    if p.marker == "circle_number":
        return 44.0
    if p.marker == "badge_number":
        return 0.0  # badge sits beside the heading
    return 2 + 10 + _height("00", "h3", sz.marker, 400)  # rule + gap + number


def _draw_marker(b: _Builder, index: int, x: float, y: float, w: float, sz: _Sizes) -> None:
    p = b.profile
    label = f"{index + 1:02d}"
    if p.marker == "rule_number":
        b.shape(x=x, y=y, w=w, h=2, fill="primary")
        b.text(label, x=x, y=y + 12, w=w, h=_height(label, "h3", sz.marker, w), role="h3",
               size=sz.marker, color="accent")
    elif p.marker == "big_number":
        b.text(label, x=x, y=y, w=w, h=_height(label, "stat", sz.marker, w), role="stat",
               size=sz.marker, color="primary")
    elif p.marker == "circle_number":
        b.shape(x=x, y=y, w=44, h=44, shape="ellipse", fill="primary")
        b.text(f"{index + 1}", x=x, y=y, w=44, h=44, role="h3", size=min(sz.marker, 24.0),
               color="on_primary", align="center", valign="middle")




PANEL_W = 408.0


def _panel_header(b: _Builder, copy: PageCopy) -> float:
    """Title column: a full-height colour panel on the left carries the header.

    Returns the x where the content column starts.
    """

    p = b.profile
    s = p.sizes
    b.shape(x=0, y=0, w=PANEL_W, h=CANVAS_HEIGHT, fill="surface_alt" if p.dark else "primary")
    on = "text" if p.dark else "on_primary"
    soft = "secondary" if p.dark else "primary_soft"
    x = 56.0
    w = PANEL_W - x - 48
    y = p.title_top + 40
    if copy.kicker:
        h = _height(copy.kicker, "kicker", s.kicker, w)
        b.text(copy.kicker, x=x, y=y, w=w, h=h, role="kicker", size=s.kicker, color=soft)
        y += h + 14
    title_size = s.title * 1.08
    h = _height(copy.title, "title", title_size, w)
    b.text(copy.title, x=x, y=y, w=w, h=h, role="title", size=title_size, color=on)
    y += h + 22
    b.shape(x=x, y=y, w=44, h=4, fill="accent")
    y += 26
    if copy.lead:
        h = _height(copy.lead, "subtitle", s.lead, w)
        b.text(copy.lead, x=x, y=y, w=w, h=h, role="subtitle", size=s.lead, color=soft)
    return PANEL_W + 64.0
