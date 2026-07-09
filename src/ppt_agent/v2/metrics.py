"""Text measurement heuristics shared by the renderer (autoshrink) and QA.

python-pptx cannot measure rendered text, so overflow control relies on
character-width estimation: CJK glyphs advance roughly one em, Latin glyphs
roughly half. Estimates deliberately lean ~5% conservative so borderline
fits shrink instead of clipping in PowerPoint.
"""

from __future__ import annotations

import math
import unicodedata

from ppt_agent.v2.design import TYPE_SCALE, TypeSpec


# Canvas is 1280 units / 13.333 in = 96 units per inch; points are 1/72 in.
UNITS_PER_PT = 96.0 / 72.0


def char_width_em(char: str) -> float:
    """Approximate advance width of one character in em units."""

    if char == " ":
        return 0.34
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 1.05
    if char.isdigit():
        return 0.56
    if char.isupper():
        return 0.66
    return 0.52


def text_width_units(text: str, size_pt: float) -> float:
    """Estimated single-line width in canvas units."""

    em = sum(char_width_em(char) for char in text)
    return em * size_pt * UNITS_PER_PT


def wrapped_line_count(text: str, size_pt: float, frame_width_units: float) -> int:
    """Estimated number of rendered lines after word wrap inside the frame."""

    if frame_width_units <= 0:
        return len(text.splitlines()) or 1
    total = 0
    for line in text.split("\n"):
        if not line:
            total += 1
            continue
        width = text_width_units(line, size_pt)
        total += max(1, math.ceil(width / frame_width_units))
    return total


def text_height_units(
    text: str, size_pt: float, line_spacing: float, frame_width_units: float
) -> float:
    """Estimated rendered block height in canvas units."""

    lines = wrapped_line_count(text, size_pt, frame_width_units)
    return lines * size_pt * line_spacing * UNITS_PER_PT


def fit_font_size(
    text: str,
    *,
    role: str,
    frame_width_units: float,
    frame_height_units: float,
    requested_size_pt: float | None = None,
) -> float:
    """Largest size (requested or role default, floored at the role minimum)
    whose estimated block height fits the frame."""

    spec: TypeSpec = TYPE_SCALE[role]
    size = requested_size_pt or spec.size_pt
    min_size = min(spec.min_size_pt, size)
    while size > min_size:
        height = text_height_units(text, size, spec.line_spacing, frame_width_units)
        if height <= frame_height_units:
            return size
        size = max(min_size, size - 1.0)
    return size


def estimated_overflow_ratio(
    text: str,
    *,
    role: str,
    size_pt: float,
    frame_width_units: float,
    frame_height_units: float,
) -> float:
    """How much the text overshoots the frame vertically (0.0 = fits)."""

    spec: TypeSpec = TYPE_SCALE[role]
    height = text_height_units(text, size_pt, spec.line_spacing, frame_width_units)
    if frame_height_units <= 0:
        return 1.0
    return max(0.0, height / frame_height_units - 1.0)
