"""Measured browser layout -> literal ``PageDesign`` elements.

Every painted box becomes a shape and every block of text a text box, at the
position the browser put it. Single lines are emitted unwrapped so a small
difference in font metrics cannot push a word onto a second line; wrapped
paragraphs keep the browser's width and its exact line pitch.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ppt_agent.v2.htmldeck.browser import Measured
from ppt_agent.v2.htmldeck.fonts import FAMILIES
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    BleedFrame,
    BoxItem,
    ChartItem,
    ChartSeries,
    ColorStop,
    Frame,
    PageDesign,
    PageRole,
    Paint,
    RichTextItem,
    TableItem,
    TextRun,
)

PT_PER_PX = 0.75
SINGLE_LINE_PAD = 48.0  # spare width on the free side of an unwrapped line
WRAP_SLACK = 2.0
CHART_KINDS = {"bar", "column", "line", "area", "pie", "doughnut"}
_COLOR = re.compile(r"rgba?\(([^)]+)\)")


class ConvertError(ValueError):
    """The page uses something that cannot become a native element."""


def css_color(value: str | None) -> tuple[str, float] | None:
    match = _COLOR.search(value or "")
    if not match:
        return None
    parts = [float(part) for part in re.split(r"[,\s/]+", match.group(1).strip()) if part]
    if len(parts) < 3:
        return None
    alpha = parts[3] if len(parts) > 3 else 1.0
    return "%02X%02X%02X" % tuple(int(round(part)) for part in parts[:3]), alpha


def css_paint(background: str | None, image: str | None, opacity: float = 1.0) -> Paint | None:
    """A CSS background (colour and/or one gradient) as a literal paint."""

    if image and image != "none" and "gradient(" in image:
        found = re.findall(r"(rgba?\([^)]+\))\s*([\d.]+%)?", image)
        colors = [(css_color(color), position) for color, position in found]
        colors = [(color, position) for color, position in colors if color]
        if len(colors) >= 2:
            last = len(colors) - 1
            stops = [
                ColorStop(
                    hex=color[0],
                    alpha=max(0.0, min(1.0, color[1] * opacity)),
                    pos=float(position[:-1]) if position else 100.0 * index / last,
                )
                for index, (color, position) in enumerate(colors)
            ]
            if image.startswith("radial"):
                return Paint(kind="radial", stops=stops)
            angle = re.search(r"(-?[\d.]+)deg", image)
            css_angle = float(angle.group(1)) if angle else 180.0
            if not angle:
                for words, degrees in (("to right", 90.0), ("to left", 270.0), ("to top", 0.0)):
                    if words in image:
                        css_angle = degrees
            # CSS: 0deg points up, 90deg right. PowerPoint: 0 right, 90 down.
            return Paint(kind="linear", stops=stops, angle_deg=(css_angle - 90.0) % 360.0)
    color = css_color(background)
    if color and color[1] * opacity > 0.003:
        return Paint(hex=color[0], alpha=min(1.0, color[1] * opacity))
    return None


def _bleed(x: float, y: float, w: float, h: float) -> BleedFrame:
    w = max(0.5, min(w, 3 * CANVAS_WIDTH))
    h = max(0.5, min(h, 3 * CANVAS_HEIGHT))
    x = max(-2 * CANVAS_WIDTH, min(x, 2 * CANVAS_WIDTH))
    y = max(-2 * CANVAS_HEIGHT, min(y, 2 * CANVAS_HEIGHT))
    return BleedFrame(x=round(x, 2), y=round(y, 2), w=round(w, 2), h=round(h, 2))


def _inside(x: float, y: float, w: float, h: float) -> Frame:
    x = max(0.0, min(x, CANVAS_WIDTH - 4))
    y = max(0.0, min(y, CANVAS_HEIGHT - 4))
    return Frame(x=x, y=y, w=max(4.0, min(w, CANVAS_WIDTH - x)), h=max(4.0, min(h, CANVAS_HEIGHT - y)))


def _off_canvas(item: dict[str, Any]) -> bool:
    return (
        item["x"] >= CANVAS_WIDTH
        or item["y"] >= CANVAS_HEIGHT
        or item["x"] + item["w"] <= 0
        or item["y"] + item["h"] <= 0
    )


def _boxes(item: dict[str, Any], next_id) -> list[BoxItem]:
    x, y, w, h, opacity = item["x"], item["y"], item["w"], item["h"], item.get("op", 1.0)
    if w < 0.5 or h < 0.5 or _off_canvas(item) or opacity <= 0.003:
        return []
    sides = [side for side in item["sides"]]
    live = [side for side in sides if side["w"] > 0 and side["st"] != "none" and css_color(side["c"])]
    uniform = len(live) == 4 and all(
        side["w"] == live[0]["w"] and side["c"] == live[0]["c"] for side in live
    )
    fill = css_paint(item.get("bg"), item.get("img"), opacity)
    out: list[BoxItem] = []
    if fill is not None or uniform:
        radius = item.get("radius") or [0, 0, 0, 0]
        short = min(w, h)
        if min(radius) >= short / 2 - 0.5 and abs(w - h) < 1:
            shape, corner = "ellipse", 0.0
        elif radius[0] > 0.5 and radius[2] < 0.5:
            shape, corner = "round_top", min(radius[0], short / 2)
        elif radius[0] > 0.5:
            shape, corner = "round", min(radius[0], short / 2)
        else:
            shape, corner = "rect", 0.0
        stroke = css_color(live[0]["c"]) if uniform else None
        width = live[0]["w"] if uniform else 0.0
        glow = re.match(r"(rgba?\([^)]+\)) 0px 0px ([\d.]+)px", item.get("shadow") or "")
        glow_color = css_color(glow.group(1)) if glow else None
        # A CSS border sits inside the box; a PowerPoint outline straddles the edge.
        inset = width / 2 if stroke else 0.0
        out.append(
            BoxItem(
                id=next_id(),
                frame=_bleed(x + inset, y + inset, w - 2 * inset, h - 2 * inset),
                shape=shape,
                radius=round(corner, 2),
                fill=fill,
                stroke_hex=stroke[0] if stroke else None,
                stroke_alpha=min(1.0, stroke[1] * opacity) if stroke else 1.0,
                stroke_width=min(width, 40.0),
                glow_hex=glow_color[0] if glow_color else None,
                glow_alpha=min(1.0, glow_color[1] * opacity) if glow_color else 1.0,
                glow_radius=float(glow.group(2)) / 2 if glow_color else 0.0,
            )
        )
    if not uniform:  # one-sided rules become thin bars
        for name, side in zip(("top", "right", "bottom", "left"), sides):
            color = css_color(side["c"])
            if side["w"] <= 0 or side["st"] == "none" or not color or color[1] <= 0:
                continue
            bar = {
                "top": (x, y, w, side["w"]),
                "bottom": (x, y + h - side["w"], w, side["w"]),
                "left": (x, y, side["w"], h),
                "right": (x + w - side["w"], y, side["w"], h),
            }[name]
            out.append(
                BoxItem(
                    id=next_id(),
                    frame=_bleed(*bar),
                    fill=Paint(hex=color[0], alpha=min(1.0, color[1] * opacity)),
                )
            )
    return out


def _fonts(css_family: str) -> tuple[str, str | None]:
    names = [name.strip().strip("\"'") for name in css_family.split(",") if name.strip()]
    names = [name for name in names if name not in ("sans-serif", "serif", "monospace")]
    if not names:
        return "Microsoft YaHei", None
    east_asian = next((name for name in names if name in FAMILIES), None)
    return names[0], east_asian if east_asian and east_asian != names[0] else None


def _runs(raw: list[dict[str, Any]], opacity: float) -> list[TextRun]:
    parts = [dict(part) for part in raw]
    for part in parts:
        if "t" in part:
            part["t"] = re.sub(r"\s+", " ", part["t"])
    # Collapse the whitespace HTML ignores: at both ends and around forced breaks.
    for index, part in enumerate(parts):
        if "t" not in part:
            continue
        before_break = index + 1 < len(parts) and parts[index + 1].get("br")
        after_break = index > 0 and parts[index - 1].get("br")
        if index == 0 or after_break:
            part["t"] = part["t"].lstrip()
        if index == len(parts) - 1 or before_break:
            part["t"] = part["t"].rstrip()
    runs: list[TextRun] = []
    for part in parts:
        if part.get("br"):
            if runs:
                runs.append(TextRun(line_break=True))
            continue
        if not part["t"]:
            continue
        color = css_color(part["color"]) or ("000000", 1.0)
        font, east_asian = _fonts(part["font"])
        gradient = css_paint(None, part["grad"], opacity) if part.get("grad") else None
        runs.append(
            TextRun(
                text=part["t"],
                size_pt=round(max(part["size"] * PT_PER_PX, 1.5), 2),
                hex=color[0],
                alpha=min(1.0, color[1] * opacity),
                bold=part["weight"] >= 600,
                italic=bool(part.get("italic")),
                font=font,
                ea_font=east_asian,
                letter_spacing_pt=round(part.get("spacing", 0) * PT_PER_PX, 2),
                paint=gradient if gradient and gradient.kind != "solid" else None,
            )
        )
    while runs and runs[-1].line_break:
        runs.pop()
    return runs


def _text(item: dict[str, Any], next_id) -> RichTextItem | None:
    runs = _runs(item["runs"], item.get("op", 1.0))
    if not any(run.text.strip() for run in runs):
        return None
    lines = max(1, int(item["lines"]))
    text = item["text"]
    glyph = item.get("glyphPx") or text["h"] / lines
    pitch = item.get("linePx") or text["h"] / lines
    top = text["y"] - (pitch - glyph) / 2  # glyph boxes sit centred in the line box
    align = item.get("align") if item.get("align") in ("left", "center", "right") else "left"
    if lines == 1:
        if align == "center":
            x, w = text["x"] - SINGLE_LINE_PAD, text["w"] + 2 * SINGLE_LINE_PAD
        elif align == "right":
            x, w = text["x"] - SINGLE_LINE_PAD, text["w"] + SINGLE_LINE_PAD
        else:
            x, w = text["x"], text["w"] + SINGLE_LINE_PAD
        return RichTextItem(
            id=next_id(), frame=_bleed(x, top, w, pitch), runs=runs, align=align,
            valign="middle", wrap=False, line_pt=round(pitch * PT_PER_PX, 2),
        )
    box = item["box"]
    return RichTextItem(
        id=next_id(), frame=_bleed(box["x"], top, box["w"] + WRAP_SLACK, pitch * lines),
        runs=runs, align=align, valign="top", wrap=True, line_pt=round(pitch * PT_PER_PX, 2),
    )


def chart_spec(raw: str) -> dict[str, Any]:
    """Parse and validate a ``data-chart`` attribute."""

    try:
        spec = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ConvertError(f"chart data is not valid JSON: {error}") from None
    if not isinstance(spec, dict) or spec.get("kind") not in CHART_KINDS:
        raise ConvertError(f"chart kind must be one of {sorted(CHART_KINDS)}")
    categories = spec.get("categories")
    series = spec.get("series")
    if not isinstance(categories, list) or not 2 <= len(categories) <= 12:
        raise ConvertError("a chart needs 2 to 12 categories")
    if not isinstance(series, list) or not 1 <= len(series) <= 4:
        raise ConvertError("a chart needs 1 to 4 series")
    for entry in series:
        values = entry.get("values") if isinstance(entry, dict) else None
        if not isinstance(values, list) or len(values) != len(categories):
            raise ConvertError("every chart series needs one value per category")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            raise ConvertError("chart values must be numbers")
    return spec


def _chart(item: dict[str, Any], next_id) -> ChartItem:
    spec = chart_spec(item["spec"])
    series = [
        ChartSeries(name=str(entry.get("name") or f"系列{index + 1}"), values=entry["values"])
        for index, entry in enumerate(spec["series"])
    ]
    highlight = spec.get("highlight")
    return ChartItem(
        id=next_id(),
        frame=_inside(item["x"], item["y"], item["w"], item["h"]),
        chart=spec["kind"],
        categories=[str(category) for category in spec["categories"]],
        series=series,
        show_legend=len(series) > 1,
        show_data_labels=True,
        font_pt=12,
        show_value_axis=False,
        show_gridlines=False,
        number_format=spec.get("format") if isinstance(spec.get("format"), str) else None,
        highlight=highlight if isinstance(highlight, int) and 0 <= highlight < len(spec["categories"]) else None,
    )


def _table(item: dict[str, Any], next_id) -> TableItem:
    headers = [header or " " for header in item["headers"]][:8]
    if not headers or not item["rows"]:
        raise ConvertError("a table needs a header row and at least one body row")
    rows = [
        [(row[index] if index < len(row) else "") or " " for index in range(len(headers))]
        for row in item["rows"][:12]
    ]
    frame = _inside(item["x"], item["y"], item["w"], item["h"])
    widths = item.get("widths") or []
    column_widths = None
    if len(widths) >= len(headers) and sum(widths[: len(headers)]) > 0:
        scale = frame.w / sum(widths[: len(headers)])
        column_widths = [round(width * scale, 2) for width in widths[: len(headers)]]
    return TableItem(
        id=next_id(),
        frame=frame,
        headers=headers,
        rows=rows,
        style="minimal",
        strong_header=item.get("variant") == "strong",
        font_pt=round(max(item.get("size", 16) * PT_PER_PX, 6), 1),
        column_widths=column_widths,
    )


def to_page(
    measured: Measured,
    *,
    page_number: int,
    role: PageRole = "content",
    section: str | None = None,
    title: str | None = None,
    notes: str | None = None,
) -> PageDesign:
    counter = iter(range(1, 100000))

    def next_id() -> str:
        return f"e{next(counter)}"

    elements: list[Any] = []
    for item in measured.items:
        kind = item["kind"]
        if kind == "box":
            elements.extend(_boxes(item, next_id))
        elif kind == "text":
            text = _text(item, next_id)
            if text is not None:
                elements.append(text)
        elif kind == "chart":
            elements.append(_chart(item, next_id))
        elif kind == "table":
            elements.append(_table(item, next_id))
    if not elements:
        raise ConvertError("the page has nothing on it")
    background = css_color(measured.background)
    return PageDesign(
        page_number=page_number,
        role=role,
        section=section,
        title=title,
        background_hex=background[0] if background and background[1] > 0 else "FFFFFF",
        show_chrome=False,
        elements=elements,
        speaker_notes=notes,
    )
