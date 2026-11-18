"""Geometric repairs tried before a page goes back to the model.

Most first attempts that fail do so by a few dozen pixels: a region one row too short, a display
figure one step too wide for its column. Those are fixed here, by measuring again, at no model cost:

- ``grow``: a region whose cells below are empty takes one more row (its content does not move;
  it only gets room);
- ``step_down``: the largest display size on the page goes down one step (d1 → d2 → d3). Display
  type only - body text, notes and captions are never made smaller to fit.

Each repair is kept only if the page then has fewer hard defects.
"""

from __future__ import annotations

import re

ROWS, COLUMNS = 8, 12
_CELL = re.compile(r'(<div class="(r(?:\s[^"]*)?)"[^>]*style="[^"]*?)'
                   r'grid-column\s*:\s*(\d+)\s*/\s*(\d+)\s*;\s*grid-row\s*:\s*(\d+)\s*/\s*(\d+)')
STEPS = (("d1", "d2"), ("d2", "d3"), ("d3", "d4"))


def regions(html: str) -> list[tuple[int, int, int, int]]:
    """(first column, column line after, first row, row line after) of every top-level region."""

    return [tuple(int(value) for value in match.groups()[2:]) for match in _CELL.finditer(html)]


def _anchored(html: str) -> list[bool]:
    """Regions whose content sits at their foot: growing them would move it."""

    return ["bot" in match.group(2).split() for match in _CELL.finditer(html)]


def grow(html: str) -> str:
    """Every region whose next row is free in all of its columns gets that row."""

    cells, anchored = regions(html), _anchored(html)
    taken = {(column, row) for c0, c1, r0, r1 in cells for column in range(c0, c1) for row in range(r0, r1)}
    grown: list[tuple[int, int, int, int]] = []
    for (c0, c1, r0, r1), bottom in zip(cells, anchored):
        free = not bottom and r1 <= ROWS and all((column, r1) not in taken for column in range(c0, c1))
        if free:
            taken |= {(column, r1) for column in range(c0, c1)}
        grown.append((c0, c1, r0, r1 + 1 if free else r1))
    if grown == cells:
        return html
    index = iter(grown)

    def replace(match: re.Match) -> str:
        c0, c1, r0, r1 = next(index)
        return f"{match.group(1)}grid-column:{c0}/{c1};grid-row:{r0}/{r1}"

    return _CELL.sub(replace, html)


def step_down(html: str) -> str:
    """The largest display class used on the page, one step smaller."""

    for big, smaller in STEPS:
        pattern = re.compile(rf'(class="(?:[^"]*\s)?){big}(?=[\s"])')
        if pattern.search(html):
            return pattern.sub(rf"\g<1>{smaller}", html)
    return html
