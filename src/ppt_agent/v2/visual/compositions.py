"""Compositions: several ways to lay out each archetype.

An archetype says *what* a page is (parallel points, a process, a chart with
conclusions); a composition says how it is arranged. Each composition is a
function ``fn(builder, content, zone, sizes, *, draw, top) -> block_height``:
with ``draw=False`` it only measures, so the engine can search sizes before
drawing once.

Every composition is held to the same invariants as the rest of the visual
system (inside the canvas, no overlapping text, reading text >= 12 pt).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ppt_agent.v2.ir import ChartItem, ChartSeries, Frame
from ppt_agent.v2.metrics import text_width_units
from ppt_agent.v2.visual.content import (
    AnyContent,
    ChartContent,
    CompareContent,
    TimelineContent,
    MetricsContent,
    PointItem,
    PointsContent,
    ProcessContent,
    StatementContent,
)
from ppt_agent.v2.visual.layout import (
    MIN_BODY_PT,
    _text_width,
    _Builder,
    _draw_marker,
    _height,
    _marker_height,
    _Sizes,
    _widow_safe_width,
    _Zone,
)

HeaderMode = Literal["standard", "panel", "none"]


@dataclass(frozen=True)
class Composition:
    archetype: str
    name: str
    fn: Callable[..., float]
    valid: Callable[[AnyContent], bool]
    header: HeaderMode = "standard"
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.archetype}:{self.name}"


# --------------------------------------------------------------------------
# Shared drawing pieces
# --------------------------------------------------------------------------


def _ref_size(sz: _Sizes) -> float:
    # A citation is a footnote: it stays small when sparse pages enlarge the body.
    return MIN_BODY_PT


def _point_cell_height(b: _Builder, item: PointItem, w: float, sz: _Sizes,
                       *, heading_size: float | None = None) -> float:
    """Height of one vertical point cell (marker, heading, body, ref) at inner width w."""

    p = b.profile
    badge = 32.0 if p.marker == "badge_number" else 0.0
    head_size = heading_size or sz.heading
    marker_h = _marker_height(p, sz)
    h = marker_h + (16 if marker_h else 0)
    h += max(badge, _height(item.heading, "h3", head_size, w - (badge + 12 if badge else 0))) + 10
    h += _height(item.body, "body", sz.body, w)
    if item.ref:
        h += 12 + _height(item.ref, "caption", _ref_size(sz), w)
    return h


def _draw_point_cell(b: _Builder, item: PointItem, index: int, x: float, y: float, w: float,
                     block: float, sz: _Sizes, *, pad: float,
                     heading_size: float | None = None) -> None:
    """Draw a vertical point cell inside a (possibly carded) box of height block."""

    p = b.profile
    b.card(x, y, w, block)
    inner_w = w - 2 * pad
    ix, iy = x + pad, y + pad
    head_size = heading_size or sz.heading
    marker_h = _marker_height(p, sz)
    if marker_h:
        _draw_marker(b, index, ix, iy, inner_w, sz)
        iy += marker_h + 16
    badge = 32.0 if p.marker == "badge_number" else 0.0
    head_x, head_w = ix, inner_w
    head_h = _height(item.heading, "h3", head_size, inner_w - (badge + 12 if badge else 0))
    if badge:
        b.shape(x=ix, y=iy, w=badge, h=badge, shape="rounded_rectangle", fill="primary_soft")
        b.text(f"{index + 1}", x=ix, y=iy, w=badge, h=badge, role="h3", size=min(sz.marker, 17.0),
               color="primary", align="center", valign="middle")
        head_x, head_w = ix + badge + 12, inner_w - badge - 12
        head_h = max(badge, head_h)
    b.text(item.heading, x=head_x, y=iy, w=head_w, h=head_h, role="h3", size=head_size,
           color="text", valign="middle" if badge else "top")
    iy += head_h + 10
    body_h = _height(item.body, "body", sz.body, inner_w)
    b.text(item.body, x=ix, y=iy, w=_widow_safe_width(item.body, sz.body, inner_w), h=body_h,
           role="body", size=sz.body, color="muted" if p.dark else "text")
    if item.ref:
        ref_h = _height(item.ref, "caption", _ref_size(sz), inner_w)
        b.text(item.ref, x=ix, y=y + block - pad - ref_h, w=inner_w, h=ref_h, role="caption",
               size=_ref_size(sz), color="muted")


_BOLD_WIDTH = 1.0  # _advance_em is already calibrated on YaHei Bold


def _one_line(text: str, size: float, width: float, floor: float = 14.0) -> float:
    """Largest size <= ``size`` at which a short bold stat stays on one line.

    Widths come from the bold-calibrated table in ``layout``; real runs
    wrapped "+18.8pp" and ">60%—80%" before it existed.
    """

    while size > floor and _text_width(text, size) * _BOLD_WIDTH > width * 0.92:
        size -= 1.0
    return size


def _pad(b: _Builder) -> float:
    return b.profile.card_pad if b.profile.card_style != "none" else 0.0


# --------------------------------------------------------------------------
# points
# --------------------------------------------------------------------------


def points_columns(b, content: PointsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    items = content.items
    n = len(items)
    gap = b.profile.item_gap
    col_w = (zone.w - gap * (n - 1)) / n
    pad = _pad(b)
    block = max(_point_cell_height(b, item, col_w - 2 * pad, sz) for item in items) + 2 * pad
    if draw:
        for index, item in enumerate(items):
            _draw_point_cell(b, item, index, zone.x + index * (col_w + gap), top, col_w, block,
                             sz, pad=pad)
    return block


def points_grid(b, content: PointsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Four points as a 2 x 2 grid: reads as a balanced dashboard."""

    items = content.items
    gap = b.profile.item_gap
    col_w = (zone.w - gap) / 2
    pad = _pad(b) or 0.0
    rows = [items[0:2], items[2:4]]
    heights = [max(_point_cell_height(b, it, col_w - 2 * pad, sz) for it in row) + 2 * pad
               for row in rows]
    block = sum(heights) + gap
    if draw:
        y = top
        for r, row in enumerate(rows):
            for c, item in enumerate(row):
                _draw_point_cell(b, item, r * 2 + c, zone.x + c * (col_w + gap), y, col_w,
                                 heights[r], sz, pad=pad)
            y += heights[r] + gap
    return block


def points_feature(b, content: PointsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Lead point large on the left, the other two stacked on the right."""

    p = b.profile
    items = content.items
    gap = p.item_gap * 1.4
    left_w = zone.w * 0.46
    right_w = zone.w - left_w - gap
    pad = _pad(b) or 0.0
    lead_size = sz.heading + 6
    left_h = _point_cell_height(b, items[0], left_w - 2 * pad, sz, heading_size=lead_size) + 2 * pad
    right_hs = [_point_cell_height(b, it, right_w - 2 * pad, sz) + 2 * pad for it in items[1:]]
    small_gap = p.item_gap
    block = max(left_h, sum(right_hs) + small_gap * (len(right_hs) - 1))
    if draw:
        if p.card_style == "none":
            b.shape(x=zone.x, y=top, w=left_w, h=block, fill="primary_soft")
            pad_left = 28.0
        else:
            pad_left = pad
        _draw_point_cell(b, items[0], 0, zone.x, top, left_w, block, sz, pad=pad_left,
                         heading_size=lead_size)
        # Right column ends with the left one: each cell keeps its own height
        # and the spare space is shared, so no cell is squeezed below its content.
        extra = (block - sum(right_hs) - small_gap * (len(right_hs) - 1)) / len(right_hs)
        y = top
        for index, (item, h) in enumerate(zip(items[1:], right_hs), start=1):
            _draw_point_cell(b, item, index, zone.x + left_w + gap, y, right_w, h + extra, sz,
                             pad=pad)
            y += h + extra + small_gap
    return block


def points_list2(b, content: PointsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Four or five points as a two-column list: number, heading, short body."""

    p = b.profile
    items = content.items
    carded = p.card_style != "none"
    gap_x = 48.0 if not carded else p.item_gap
    gap_y = 28.0 if not carded else p.item_gap
    col_w = (zone.w - gap_x) / 2
    pad = 22.0 if carded else 0.0
    num_w = 48.0
    text_w = col_w - 2 * pad - num_w

    def cell_h(item: PointItem) -> float:
        h = _height(item.heading, "h3", sz.heading, text_w) + 6
        h += _height(item.body, "body", sz.body, text_w)
        if item.ref:
            h += 8 + _height(item.ref, "caption", _ref_size(sz), text_w)
        return h + 2 * pad

    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    heights = [max(cell_h(it) for it in row) for row in rows]
    block = sum(heights) + gap_y * (len(rows) - 1)
    if draw:
        y = top
        for r, row in enumerate(rows):
            for c, item in enumerate(row):
                index = r * 2 + c
                x = zone.x + c * (col_w + gap_x)
                b.card(x, y, col_w, heights[r])
                if not carded:
                    b.shape(x=x, y=y - 12, w=col_w, h=1.5,
                            fill="primary" if p.name == "consulting" else "surface_alt")
                ix, iy = x + pad, y + pad
                b.text(f"{index + 1:02d}", x=ix, y=iy, w=num_w,
                       h=_height("00", "h3", sz.heading, num_w), role="h3", size=sz.heading,
                       color="accent" if p.name == "consulting" else "primary")
                hx = ix + num_w
                hh = _height(item.heading, "h3", sz.heading, text_w)
                b.text(item.heading, x=hx, y=iy, w=text_w, h=hh, role="h3", size=sz.heading,
                       color="text")
                bh = _height(item.body, "body", sz.body, text_w)
                b.text(item.body, x=hx, y=iy + hh + 6, w=text_w, h=bh, role="body",
                       size=sz.body, color="muted" if p.dark else "text")
                if item.ref:
                    rh = _height(item.ref, "caption", _ref_size(sz), text_w)
                    b.text(item.ref, x=hx, y=iy + hh + 6 + bh + 8, w=text_w, h=rh,
                           role="caption", size=_ref_size(sz),
                           color="muted")
            y += heights[r] + gap_y
    return block


def points_rows(b, content: PointsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    p = b.profile
    items = content.items
    carded = p.card_style != "none"
    pad_y = 16.0 if carded else 14.0
    pad_x = p.card_pad if carded else 0.0
    num_w = 56.0
    inner = zone.w - 2 * pad_x - num_w
    # Size the heading column to the longest heading so headings stay on one line.
    longest = max(text_width_units(item.heading, sz.heading) for item in items) + 12
    head_w = min(max(longest, inner * 0.22), inner * 0.4)
    body_w = inner - head_w - 24
    texts = [item.body + (f"（{item.ref}）" if item.ref else "") for item in items]
    gap = 12.0 if carded else 0.0
    natural = [max(_height(item.heading, "h3", sz.heading, head_w),
                   _height(text, "body", sz.body, body_w)) for item, text in zip(items, texts)]
    # Sparse list: open up the rows instead of leaving the lower half empty.
    spare = zone.h * b.profile.fill_target - (sum(natural) + 2 * pad_y * len(items)
                                              + gap * (len(items) - 1))
    if spare > 0:
        pad_y += min(spare / (2 * len(items)), 30.0)
    rows = [h + 2 * pad_y for h in natural]
    block = sum(rows) + gap * (len(rows) - 1)
    if draw:
        y = top
        for index, (item, text, h) in enumerate(zip(items, texts, rows)):
            b.card(zone.x, y, zone.w, h)
            if not carded and index:
                b.line(zone.x, y, zone.x + zone.w, y, color="surface_alt", width=0.75)
            # Centre every column in the row: estimates are conservative, so a
            # top-aligned single line would float above a two-line-high row.
            b.text(f"{index + 1:02d}", x=zone.x + pad_x, y=y + pad_y, w=num_w,
                   h=h - 2 * pad_y, role="h3", size=sz.heading, valign="middle",
                   color="accent" if p.name == "consulting" else "primary")
            b.text(item.heading, x=zone.x + pad_x + num_w, y=y + pad_y, w=head_w,
                   h=h - 2 * pad_y, role="h3", size=sz.heading, color="text", valign="middle")
            b.text(text, x=zone.x + pad_x + num_w + head_w + 24, y=y + pad_y,
                   w=_widow_safe_width(text, sz.body, body_w), h=h - 2 * pad_y, role="body",
                   size=sz.body, color="muted" if p.dark else "text", valign="middle")
            y += h + gap
    return block


# --------------------------------------------------------------------------
# process
# --------------------------------------------------------------------------


def process_horizontal(b, content: ProcessContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    p = b.profile
    steps = content.steps
    n = len(steps)
    arrow = 40.0 if p.marker in ("rule_number", "badge_number") else 24.0
    col_w = (zone.w - arrow * (n - 1)) / n
    pad = p.card_pad if p.card_style == "outlined" else 0.0
    inner_w = col_w - 2 * pad
    marker_h = _marker_height(p, sz) if p.marker != "badge_number" else 32.0
    block = max(marker_h + 16 + _height(s.label, "h3", sz.heading, inner_w) + 8
                + _height(s.body, "body", sz.body, inner_w) for s in steps) + 2 * pad
    if not draw:
        return block
    if p.marker == "circle_number":
        b.line(zone.x + 22, top + 22, zone.x + zone.w - col_w + 22, top + 22,
               color="primary_soft", width=4)
    if p.marker == "big_number":
        b.line(zone.x, top + marker_h + 6, zone.x + zone.w, top + marker_h + 6,
               color="surface_alt", width=1.25)
    for index, step in enumerate(steps):
        x = zone.x + index * (col_w + arrow)
        if pad:
            b.card(x, top, col_w, block)
        ix, iy = x + pad, top + pad
        if p.marker == "badge_number":
            b.shape(x=ix, y=iy, w=32, h=32, shape="rounded_rectangle", fill="primary")
            b.text(f"{index + 1}", x=ix, y=iy, w=32, h=32, role="h3", size=min(sz.marker, 17.0),
                   color="on_primary", align="center", valign="middle")
        else:
            _draw_marker(b, index, ix, iy, inner_w, sz)
        iy += marker_h + 16
        label_h = _height(step.label, "h3", sz.heading, inner_w)
        b.text(step.label, x=ix, y=iy, w=inner_w, h=label_h, role="h3", size=sz.heading,
               color="text")
        iy += label_h + 8
        body_h = _height(step.body, "body", sz.body, inner_w)
        b.text(step.body, x=ix, y=iy, w=_widow_safe_width(step.body, sz.body, inner_w),
               h=body_h, role="body", size=sz.body, color="muted")
        if index < n - 1 and arrow >= 40:
            ay = top + pad + 16 - 16 if p.marker == "badge_number" else top + marker_h / 2 - 16
            b.text("→", x=x + col_w, y=ay, w=arrow, h=32, role="h3", size=18, color="muted",
                   align="center", valign="middle")
    return block


def process_chevron(b, content: ProcessContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Classic consulting chevron band: labels inside arrows, detail underneath."""

    steps = content.steps
    n = len(steps)
    overlap = 10.0
    col_w = (zone.w + overlap * (n - 1)) / n
    band_h = _height("标签", "h3", sz.heading, col_w) + 30
    text_w = col_w - overlap - 28
    body_h = max(_height(s.body, "body", sz.body, text_w) for s in steps)
    block = band_h + 22 + body_h
    if draw:
        for index, step in enumerate(steps):
            x = zone.x + index * (col_w - overlap)
            fill = "primary" if index == n - 1 else ("secondary" if index % 2 else "primary")
            b.shape(x=x, y=top, w=col_w, h=band_h, shape="chevron" if index else "rectangle",
                    fill=fill)
            b.text(step.label, x=x + (40 if index else 18), y=top, w=col_w - 64, h=band_h,
                   role="h3", size=sz.heading, color="on_primary", valign="middle")
            b.text(step.body, x=x + (16 if index else 0), y=top + band_h + 22,
                   w=_widow_safe_width(step.body, sz.body, text_w),
                   h=_height(step.body, "body", sz.body, text_w), role="body", size=sz.body,
                   color="text")
    return block


def process_vertical(b, content: ProcessContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Stacked stepper: number rail on the left, label and detail side by side."""

    p = b.profile
    steps = content.steps
    rail = 56.0
    label_w = (zone.w - rail) * 0.26
    body_w = zone.w - rail - label_w - 24
    pad_y = 14.0
    natural = [max(_height(s.label, "h3", sz.heading, label_w),
                   _height(s.body, "body", sz.body, body_w), 40.0) for s in steps]
    spare = zone.h * p.fill_target - (sum(natural) + 2 * pad_y * len(steps))
    if spare > 0:
        pad_y += min(spare / (2 * len(steps)), 22.0)
    rows = [h + 2 * pad_y for h in natural]
    block = sum(rows)
    if draw:
        b.line(zone.x + 20, top + rows[0] / 2, zone.x + 20, top + block - rows[-1] / 2,
               color="primary_soft" if not p.dark else "surface_alt", width=3)
        y = top
        for index, (step, h) in enumerate(zip(steps, rows)):
            cy = y + h / 2
            b.shape(x=zone.x, y=cy - 20, w=40, h=40, shape="ellipse", fill="primary")
            b.text(f"{index + 1}", x=zone.x, y=cy - 20, w=40, h=40, role="h3",
                   size=max(MIN_BODY_PT, sz.marker if sz.marker < 24 else 18),
                   color="on_primary", align="center", valign="middle")
            b.text(step.label, x=zone.x + rail, y=y + pad_y, w=label_w, h=h - 2 * pad_y,
                   role="h3", size=sz.heading, color="text", valign="middle")
            b.text(step.body, x=zone.x + rail + label_w + 24, y=y + pad_y,
                   w=_widow_safe_width(step.body, sz.body, body_w), h=h - 2 * pad_y,
                   role="body", size=sz.body, color="muted" if p.dark else "text",
                   valign="middle")
            if index < len(steps) - 1 and p.card_style == "none":
                b.line(zone.x + rail, y + h, zone.x + zone.w, y + h, color="surface_alt",
                       width=0.75)
            y += h
    return block


# --------------------------------------------------------------------------
# chart
# --------------------------------------------------------------------------


def _chart_item(b: _Builder, content: ChartContent, x: float, y: float, w: float, h: float):
    p = b.profile
    b.elements.append(
        ChartItem(
            id=b._id("chart"), frame=Frame(x=x, y=y, w=w, h=h), chart=content.chart,
            categories=content.categories,
            series=[ChartSeries(name=content.chart_title, values=content.values)],
            show_legend=False, show_data_labels=True, font_pt=p.chart_font,
            show_value_axis=False, show_gridlines=False, number_format=content.unit_format,
        )
    )


def _chart_min_h(b: _Builder, content: ChartContent) -> float:
    if content.chart != "bar":
        return 220.0
    # ~2x the label height per bar keeps bars readable without starving the page.
    per = b.profile.chart_font * 1.333 * 2.0
    return max(200.0, len(content.categories) * per)


def _insight_height(insight, w: float, sz: _Sizes, stat: float) -> float:
    h = _height(insight.text, "body", sz.body, w)
    if insight.value:
        h += _height(insight.value, "stat", stat, w) + 6
    return h


def _draw_insight(b: _Builder, insight, x: float, y: float, w: float, sz: _Sizes, stat: float,
                  *, color: str = "primary") -> None:
    p = b.profile
    if insight.value:
        vh = _height(insight.value, "stat", stat, w)
        b.text(insight.value, x=x, y=y, w=w, h=vh, role="stat", size=stat, color=color)
        y += vh + 6
    b.text(insight.text, x=x, y=y, w=_widow_safe_width(insight.text, sz.body, w),
           h=_height(insight.text, "body", sz.body, w), role="body", size=sz.body,
           color="muted" if p.dark else "text")


def _chart_panel(b: _Builder, content: ChartContent, x: float, y: float, w: float, h: float,
                 sz: _Sizes) -> None:
    pad = _pad(b)
    b.card(x, y, w, h)
    title_h = _height(content.chart_title, "h3", sz.heading, w - 2 * pad)
    b.text(content.chart_title, x=x + pad, y=y + pad, w=w - 2 * pad, h=title_h, role="h3",
           size=sz.heading, color="text")
    _chart_item(b, content, x + pad - 6, y + pad + title_h + 12, w - 2 * pad + 6,
                h - title_h - 12 - 2 * pad)


def chart_side(b, content: ChartContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    p = b.profile
    gap = 48.0
    chart_w = zone.w * 0.56
    side_w = zone.w - chart_w - gap
    pad = _pad(b)
    title_h = _height(content.chart_title, "h3", sz.heading, chart_w - 2 * pad)
    left = title_h + 12 + _chart_min_h(b, content) + 2 * pad
    stat = sz.stat * 0.8
    hs = [_insight_height(i, side_w, sz, stat) for i in content.insights]
    right = sum(hs) + 28 * (len(hs) - 1)
    block = max(left, right, zone.h * 0.9)
    if draw:
        _chart_panel(b, content, zone.x, top, chart_w, block, sz)
        x = zone.x + chart_w + gap
        y = top + (block - right) / 2 if p.centered_block else top
        for index, (insight, h) in enumerate(zip(content.insights, hs)):
            if index and p.card_style == "none":
                b.line(x, y - 14, x + side_w, y - 14, color="surface_alt", width=0.75)
            _draw_insight(b, insight, x, y, side_w, sz, stat)
            y += h + 28
    return block


def chart_kpi_top(b, content: ChartContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Dashboard: KPI cards across the top, the chart full width underneath."""

    p = b.profile
    insights = content.insights
    n = len(insights)
    gap = p.item_gap
    card_w = (zone.w - gap * (n - 1)) / n
    pad = 22.0
    stat = min(_one_line(i.value or "", sz.stat * 0.75, card_w - 2 * pad - 8) for i in insights)
    card_h = max(_insight_height(i, card_w - 2 * pad - 8, sz, stat) for i in insights) + 2 * pad
    chart_min = (_height(content.chart_title, "h3", sz.heading, zone.w) + 12
                 + _chart_min_h(b, content) + 2 * _pad(b))
    block = max(card_h + gap + chart_min, zone.h * 0.95)
    if draw:
        for index, insight in enumerate(insights):
            x = zone.x + index * (card_w + gap)
            shape = "rounded_rectangle" if p.card_radius else "rectangle"
            b.shape(x=x, y=top, w=card_w, h=card_h, shape=shape,
                    fill="surface" if p.card_style != "none" else "primary_soft",
                    stroke="surface_alt" if p.card_style == "outlined" else None)
            b.shape(x=x, y=top, w=6, h=card_h, fill="secondary" if index else "primary")
            _draw_insight(b, insight, x + pad + 8, top + pad, card_w - 2 * pad - 8, sz, stat,
                          color="primary" if index == 0 else "text")
        _chart_panel(b, content, zone.x, top + card_h + gap, zone.w, block - card_h - gap, sz)
    return block


def chart_hero(b, content: ChartContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """One headline number owns the left; the chart supports it on the right."""

    p = b.profile
    hero, rest = content.insights[0], content.insights[1:]
    left_w = zone.w * 0.38
    gap = 56.0
    chart_w = zone.w - left_w - gap
    hero_size = _one_line(hero.value or "", sz.stat * 1.55, left_w)
    hero_h = _height(hero.value or "", "stat", hero_size, left_w)
    text_h = _height(hero.text, "h3", sz.heading, left_w)
    rest_hs = [_height(f"{i.value or ''} {i.text}", "body", sz.body, left_w) for i in rest]
    left = hero_h + 10 + text_h + (28 + sum(rest_hs) + 12 * max(0, len(rest_hs) - 1)
                                   if rest else 0)
    pad = _pad(b)
    chart_need = (_height(content.chart_title, "h3", sz.heading, chart_w - 2 * pad) + 12
                  + _chart_min_h(b, content) + 2 * pad)
    block = max(left, chart_need, zone.h * 0.9)
    if draw:
        y = top + max(0.0, (block - left) / 2)
        b.text(hero.value or "", x=zone.x, y=y, w=left_w, h=hero_h, role="stat", size=hero_size,
               color="primary")
        y += hero_h + 10
        b.text(hero.text, x=zone.x, y=y, w=_widow_safe_width(hero.text, sz.heading, left_w),
               h=text_h, role="h3", size=sz.heading, color="text")
        y += text_h + 28
        if rest:
            b.line(zone.x, y - 14, zone.x + left_w * 0.5, y - 14, color="accent", width=2)
        for insight, h in zip(rest, rest_hs):
            text = f"{insight.value} {insight.text}" if insight.value else insight.text
            b.text(text, x=zone.x, y=y, w=_widow_safe_width(text, sz.body, left_w), h=h,
                   role="body", size=sz.body, color="muted" if p.dark else "text")
            y += h + 12
        _chart_panel(b, content, zone.x + left_w + gap, top, chart_w, block, sz)
    return block


def chart_stacked(b, content: ChartContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Chart across the full width; the conclusions read as a row of notes beneath."""

    p = b.profile
    insights = content.insights
    n = len(insights)
    gap = 40.0
    col_w = (zone.w - gap * (n - 1)) / n
    stat = sz.stat * 0.7
    notes_h = max(_insight_height(i, col_w, sz, stat) for i in insights)
    title_h = _height(content.chart_title, "h3", sz.heading, zone.w)
    chart_min = title_h + 12 + _chart_min_h(b, content) + 2 * _pad(b)
    block = max(chart_min + 28 + notes_h, zone.h * 0.95)
    if draw:
        chart_h = block - 28 - notes_h
        _chart_panel(b, content, zone.x, top, zone.w * 0.82, chart_h, sz)
        y = top + chart_h + 28
        b.line(zone.x, y - 14, zone.x + zone.w, y - 14, color="surface_alt", width=0.75)
        for index, insight in enumerate(insights):
            x = zone.x + index * (col_w + gap)
            _draw_insight(b, insight, x, y, col_w, sz, stat,
                          color="accent" if index == 0 and p.name == "consulting" else "primary")
    return block


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def metrics_cards(b, content: MetricsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    p = b.profile
    items = content.metrics
    n = len(items)
    gap = p.item_gap
    w = (zone.w - gap * (n - 1)) / n
    pad = 28.0
    inner = w - 2 * pad
    stat = min(_one_line(m.value, sz.stat * 1.05, inner) for m in items)

    note_size = max(MIN_BODY_PT, min(sz.small, p.sizes.small))

    def cell_h(m) -> float:
        h = _height(m.value, "stat", stat, inner) + 8 + _height(m.label, "h3", sz.heading, inner)
        if m.note:
            h += 10 + _height(m.note, "body_small", note_size, inner)
        return h

    block = max(cell_h(m) for m in items) + 2 * pad
    if draw:
        for index, m in enumerate(items):
            x = zone.x + index * (w + gap)
            shape = "rounded_rectangle" if p.card_radius else "rectangle"
            b.shape(x=x, y=top, w=w, h=block, shape=shape,
                    fill="primary" if index == 0 else "surface",
                    stroke="surface_alt" if p.card_style == "outlined" and index else None)
            on = "on_primary" if index == 0 else "text"
            y = top + pad
            vh = _height(m.value, "stat", stat, inner)
            b.text(m.value, x=x + pad, y=y, w=inner, h=vh, role="stat", size=stat,
                   color="on_primary" if index == 0 else "primary")
            y += vh + 8
            lh = _height(m.label, "h3", sz.heading, inner)
            b.text(m.label, x=x + pad, y=y, w=inner, h=lh, role="h3", size=sz.heading, color=on)
            y += lh + 10
            if m.note:
                b.text(m.note, x=x + pad, y=y, w=_widow_safe_width(m.note, note_size, inner),
                       h=_height(m.note, "body_small", note_size, inner), role="body_small",
                       size=note_size, color="primary_soft" if index == 0 else "muted")
    return block


def metrics_columns(b, content: MetricsContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Big numbers under a hairline, no boxes: editorial and quiet."""

    p = b.profile
    items = content.metrics
    n = len(items)
    gap = 48.0
    w = (zone.w - gap * (n - 1)) / n
    stat = min(_one_line(m.value, sz.stat * (1.25 if p.dark else 1.1), w) for m in items)

    note_size = max(MIN_BODY_PT, min(sz.body, p.sizes.body + 1))

    def cell_h(m) -> float:
        h = 18 + _height(m.value, "stat", stat, w) + 6 + _height(m.label, "h3", sz.heading, w)
        if m.note:
            h += 10 + _height(m.note, "body", note_size, w)
        return h

    block = max(cell_h(m) for m in items)
    if draw:
        for index, m in enumerate(items):
            x = zone.x + index * (w + gap)
            b.shape(x=x, y=top, w=w, h=2, fill="accent" if index == 0 else "primary")
            y = top + 18
            vh = _height(m.value, "stat", stat, w)
            b.text(m.value, x=x, y=y, w=w, h=vh, role="stat", size=stat, color="primary")
            y += vh + 6
            lh = _height(m.label, "h3", sz.heading, w)
            b.text(m.label, x=x, y=y, w=w, h=lh, role="h3", size=sz.heading, color="text")
            y += lh + 10
            if m.note:
                b.text(m.note, x=x, y=y, w=_widow_safe_width(m.note, note_size, w),
                       h=_height(m.note, "body", note_size, w), role="body", size=note_size,
                       color="muted")
    return block


# --------------------------------------------------------------------------
# statement (breathing page)
# --------------------------------------------------------------------------


def _statement_sizes(b: _Builder, sz: _Sizes) -> tuple[float, float]:
    return sz.heading * 2.1, sz.body + 2


def _statement_block(b, content: StatementContent, w: float, sz: _Sizes) -> tuple[float, float, float]:
    big, support = _statement_sizes(b, sz)
    label_h = _height(content.title, "kicker", b.profile.sizes.kicker + 2, w)
    st_h = _height(content.statement, "title", big, w)
    sup_h = _height(content.support, "subtitle", support, w) if content.support else 0.0
    return label_h, st_h, sup_h


def statement_left(b, content: StatementContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    w = zone.w * 0.78
    big, support = _statement_sizes(b, sz)
    label_h, st_h, sup_h = _statement_block(b, content, w - 32, sz)
    block = label_h + 20 + st_h + (24 + sup_h if sup_h else 0)
    if draw:
        x = zone.x + 32
        b.shape(x=zone.x, y=top, w=6, h=block, fill="accent")
        b.text(content.title, x=x, y=top, w=w - 32, h=label_h, role="kicker",
               size=b.profile.sizes.kicker + 2, color="muted")
        y = top + label_h + 20
        b.text(content.statement, x=x, y=y, w=_widow_safe_width(content.statement, big, w - 32),
               h=st_h, role="title", size=big, color="primary")
        if content.support:
            b.text(content.support, x=x, y=y + st_h + 24, w=w - 32, h=sup_h, role="subtitle",
                   size=support, color="muted")
    return block


def statement_center(b, content: StatementContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    w = zone.w * 0.84
    big, support = _statement_sizes(b, sz)
    label_h, st_h, sup_h = _statement_block(b, content, w, sz)
    block = label_h + 24 + st_h + (28 + sup_h if sup_h else 0)
    if draw:
        x = zone.x + (zone.w - w) / 2
        b.text(content.title, x=x, y=top, w=w, h=label_h, role="kicker",
               size=b.profile.sizes.kicker + 2,
               color="secondary" if b.profile.dark else "primary", align="center")
        y = top + label_h + 24
        b.text(content.statement, x=x, y=y, w=w, h=st_h, role="title", size=big, color="text",
               align="center")
        if content.support:
            b.text(content.support, x=x, y=y + st_h + 28, w=w, h=sup_h, role="subtitle",
                   size=support, color="muted", align="center")
    return block


def statement_band(b, content: StatementContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """A full-bleed brand band carries the sentence across the slide."""

    pad = 48.0
    w = zone.w - 2 * pad
    big, support = _statement_sizes(b, sz)
    label_h, st_h, sup_h = _statement_block(b, content, w, sz)
    band_h = label_h + 18 + st_h + 2 * pad
    block = band_h + (28 + sup_h if sup_h else 0)
    if draw:
        b.shape(x=0, y=top, w=1280, h=band_h, fill="primary")
        b.text(content.title, x=zone.x + pad, y=top + pad, w=w, h=label_h, role="kicker",
               size=b.profile.sizes.kicker + 2, color="primary_soft")
        b.text(content.statement, x=zone.x + pad, y=top + pad + label_h + 18,
               w=_widow_safe_width(content.statement, big, w), h=st_h, role="title", size=big,
               color="on_primary")
        if content.support:
            b.text(content.support, x=zone.x + pad, y=top + band_h + 28, w=w, h=sup_h,
                   role="subtitle", size=support, color="muted")
    return block


# --------------------------------------------------------------------------
# compare
# --------------------------------------------------------------------------


def _bullets(points: list[str]) -> str:
    return "\n".join(point.rstrip("。.") for point in points)


def compare_split(b, content: CompareContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Two columns under coloured header strips; the right side is favoured."""

    p = b.profile
    gap = p.item_gap * 1.6
    col_w = (zone.w - gap) / 2
    pad = 26.0
    inner = col_w - 2 * pad
    head_h = _height("标题", "h3", sz.heading + 2, inner) + 24
    sides = (content.left, content.right)
    body_hs = [_height(_bullets(side.points), "body", sz.body, inner - 18) for side in sides]
    block = head_h + 20 + max(body_hs) + pad
    if draw:
        for index, side in enumerate(sides):
            x = zone.x + index * (col_w + gap)
            favoured = index == 1
            if p.card_style != "none" or favoured:
                b.shape(x=x, y=top, w=col_w, h=block,
                        shape="rounded_rectangle" if p.card_radius else "rectangle",
                        fill="surface" if not favoured else "primary_soft",
                        stroke="surface_alt" if p.card_style == "outlined" else None)
            b.shape(x=x, y=top, w=col_w, h=head_h,
                    shape="rectangle", fill="primary" if favoured else "surface_alt")
            b.text(side.heading, x=x + pad, y=top, w=inner, h=head_h, role="h3",
                   size=sz.heading + 2, color="on_primary" if favoured else "text",
                   valign="middle")
            b.text(_bullets(side.points), x=x + pad, y=top + head_h + 20, w=inner - 18,
                   h=body_hs[index], role="body", size=sz.body, bullet="dot",
                   color="muted" if p.dark else "text")
    return block


def compare_versus(b, content: CompareContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Two cards facing each other with a VS badge between them."""

    p = b.profile
    badge = 64.0
    gap = badge + 40
    col_w = (zone.w - gap) / 2
    pad = 30.0
    inner = col_w - 2 * pad
    sides = (content.left, content.right)
    head_hs = [_height(side.heading, "title", sz.heading + 6, inner) for side in sides]
    body_hs = [_height(_bullets(side.points), "body", sz.body, inner - 18) for side in sides]
    block = max(h + 18 + bh for h, bh in zip(head_hs, body_hs)) + 2 * pad
    if draw:
        for index, side in enumerate(sides):
            x = zone.x + index * (col_w + gap)
            favoured = index == 1
            shape = "rounded_rectangle" if p.card_radius else "rectangle"
            b.shape(x=x, y=top, w=col_w, h=block, shape=shape,
                    fill="primary" if favoured else "surface",
                    stroke="surface_alt" if (p.card_style == "outlined" and not favoured) else None)
            on = "on_primary" if favoured else "text"
            b.text(side.heading, x=x + pad, y=top + pad, w=inner, h=head_hs[index], role="title",
                   size=sz.heading + 6, color=on)
            b.text(_bullets(side.points), x=x + pad, y=top + pad + head_hs[index] + 18,
                   w=inner - 18, h=body_hs[index], role="body", size=sz.body, bullet="dot",
                   color="primary_soft" if favoured else ("muted" if p.dark else "text"))
        cx = zone.x + col_w + (gap - badge) / 2
        cy = top + block / 2 - badge / 2
        b.shape(x=cx, y=cy, w=badge, h=badge, shape="ellipse", fill="accent")
        b.text("VS", x=cx, y=cy, w=badge, h=badge, role="h3", size=20, color="on_primary",
               align="center", valign="middle")
    return block


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


def _milestone_heights(b, content: TimelineContent, w: float, sz: _Sizes, date_size: float):
    date_h = _height("2025.06", "stat", date_size, w)
    label_hs = [_height(m.label, "h3", sz.heading, w) for m in content.milestones]
    body_hs = [_height(m.body, "body", sz.body, w) if m.body else 0.0
               for m in content.milestones]
    return date_h, label_hs, body_hs


def timeline_axis(b, content: TimelineContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Dates above a horizontal axis, labels and detail below it."""

    p = b.profile
    n = len(content.milestones)
    gap = 28.0
    col_w = (zone.w - gap * (n - 1)) / n
    date_size = min(_one_line(m.date, sz.stat * 0.6, col_w) for m in content.milestones)
    date_h, label_hs, body_hs = _milestone_heights(b, content, col_w, sz, date_size)
    axis_y = date_h + 18
    below = max(lh + (8 + bh if bh else 0) for lh, bh in zip(label_hs, body_hs))
    block = axis_y + 26 + below
    if draw:
        b.line(zone.x, top + axis_y, zone.x + zone.w, top + axis_y,
               color="surface_alt" if p.card_style == "none" else "primary_soft", width=3)
        for index, m in enumerate(content.milestones):
            x = zone.x + index * (col_w + gap)
            b.text(m.date, x=x, y=top, w=col_w, h=date_h, role="stat", size=date_size,
                   color="accent" if index == n - 1 else "primary")
            b.shape(x=x, y=top + axis_y - 8, w=16, h=16, shape="ellipse",
                    fill="accent" if index == n - 1 else "primary")
            y = top + axis_y + 26
            b.text(m.label, x=x, y=y, w=col_w, h=label_hs[index], role="h3", size=sz.heading,
                   color="text")
            if m.body:
                b.text(m.body, x=x, y=y + label_hs[index] + 8, w=col_w, h=body_hs[index],
                       role="body", size=sz.body, color="muted")
    return block


def timeline_cards(b, content: TimelineContent, zone: _Zone, sz: _Sizes, *, draw, top) -> float:
    """Milestone cards hanging off a track, each with a date chip."""

    p = b.profile
    n = len(content.milestones)
    gap = p.item_gap
    col_w = (zone.w - gap * (n - 1)) / n
    pad = 22.0
    inner = col_w - 2 * pad
    chip_size = max(MIN_BODY_PT, min(sz.small, p.sizes.small + 1))
    chip_h = _height("2025", "h3", chip_size, inner) + 12
    _, label_hs, body_hs = _milestone_heights(b, content, inner, sz, 20)
    card_h = max(lh + (8 + bh if bh else 0) for lh, bh in zip(label_hs, body_hs)) + 2 * pad
    block = chip_h / 2 + card_h + chip_h / 2
    if draw:
        b.line(zone.x, top + chip_h / 2, zone.x + zone.w, top + chip_h / 2,
               color="primary_soft", width=3)
        for index, m in enumerate(content.milestones):
            x = zone.x + index * (col_w + gap)
            shape = "rounded_rectangle" if p.card_radius else "rectangle"
            b.shape(x=x, y=top + chip_h, w=col_w, h=card_h, shape=shape, fill="surface",
                    stroke="surface_alt" if p.card_style == "outlined" else None)
            chip_w = min(inner, text_width_units(m.date, chip_size) * _BOLD_WIDTH + 28)
            b.shape(x=x + pad, y=top, w=chip_w, h=chip_h, shape="pill",
                    fill="accent" if index == n - 1 else "primary")
            b.text(m.date, x=x + pad, y=top, w=chip_w, h=chip_h, role="h3", size=chip_size,
                   color="on_primary", align="center", valign="middle")
            y = top + chip_h + pad
            b.text(m.label, x=x + pad, y=y, w=inner, h=label_hs[index], role="h3",
                   size=sz.heading, color="text")
            if m.body:
                b.text(m.body, x=x + pad, y=y + label_hs[index] + 8, w=inner, h=body_hs[index],
                       role="body", size=sz.body, color="muted" if p.dark else "text")
    return block


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def _n(field: str, lo: int, hi: int) -> Callable[[AnyContent], bool]:
    return lambda c: lo <= len(getattr(c, field)) <= hi


def _always(_: AnyContent) -> bool:
    return True


COMPOSITIONS: list[Composition] = [
    Composition("points", "columns", points_columns, _n("items", 2, 4),
                description="等宽分栏（2–4 条）"),
    Composition("points", "list2", points_list2, _n("items", 4, 5),
                description="两栏编号列表（4–5 条）"),
    Composition("points", "grid", points_grid, _n("items", 4, 4),
                description="2×2 网格"),
    Composition("points", "feature", points_feature, _n("items", 3, 3),
                description="首条放大在左，其余两条叠在右"),
    Composition("points", "rows", points_rows, _always, description="行式列表"),
    Composition("points", "panel", points_rows, _always, header="panel",
                description="左侧色块标题列 + 右侧列表"),
    Composition("process", "horizontal", process_horizontal, _always, description="横向步骤"),
    Composition("process", "chevron", process_chevron, _n("steps", 3, 5),
                description="箭头色带"),
    Composition("process", "vertical", process_vertical, _always, description="纵向步骤轨道"),
    Composition("chart", "side", chart_side, _always, description="左图右结论"),
    Composition("chart", "kpi_top", chart_kpi_top,
                lambda c: len(c.insights) >= 2 and all(i.value for i in c.insights),
                description="上方指标卡 + 下方通栏图表"),
    Composition("chart", "hero", chart_hero, lambda c: bool(c.insights[0].value),
                description="左侧一个大数字 + 右侧图表"),
    Composition("chart", "stacked", chart_stacked, lambda c: len(c.insights) >= 2,
                description="通栏图表 + 下方一排结论"),
    Composition("metrics", "cards", metrics_cards, _always, description="指标卡片（首张反色）"),
    Composition("metrics", "columns", metrics_columns, _always, description="细线大数字"),
    Composition("compare", "split", compare_split, _always, description="双栏对比（右侧为推荐方）"),
    Composition("compare", "versus", compare_versus, _always, description="两张对峙卡片 + VS"),
    Composition("timeline", "axis", timeline_axis, _always, description="横轴时间线"),
    Composition("timeline", "cards", timeline_cards, _always, description="日期标签 + 里程碑卡片"),
    Composition("statement", "left", statement_left, _always, header="none",
                description="左对齐大字 + 强调竖线"),
    Composition("statement", "center", statement_center, _always, header="none",
                description="居中大字"),
    Composition("statement", "band", statement_band, _always, header="none",
                description="通栏品牌色带"),
]

BY_KEY: dict[str, Composition] = {c.key: c for c in COMPOSITIONS}


def candidates(content: AnyContent) -> list[Composition]:
    return [c for c in COMPOSITIONS if c.archetype == content.archetype and c.valid(content)]
