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

import math
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


# --- the row solver: rows sized by measurement, not by the designer's guess -------------------

PAGE_TOP, PAGE_LEFT, ROW_PX, COLUMN_PX = 50.0, 76.0, 75.5, 96.0
LAST_LINE = ROWS + 1   # grid lines run 1..9


def region_of(rect: dict[str, float], cells: list[tuple[int, int, int, int]]) -> int | None:
    """Which region a measured rectangle is, by the grid cell it starts in."""

    column = round((rect["x"] - PAGE_LEFT) / COLUMN_PX) + 1
    row = round((rect["y"] - PAGE_TOP) / ROW_PX) + 1
    for index, (c0, c1, r0, r1) in enumerate(cells):
        if c0 == column and r0 == row:
            return index
    return None


def cell_rect(cell: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    c0, c1, r0, r1 = cell
    return (PAGE_LEFT + (c0 - 1) * COLUMN_PX, PAGE_TOP + (r0 - 1) * ROW_PX,
            PAGE_LEFT + (c1 - 1) * COLUMN_PX - 24, PAGE_TOP + (r1 - 1) * ROW_PX)


def measured_rows(html: str, items: list[dict]) -> dict[int, int]:
    """Rows each text region's content takes, from where its text actually landed: overflowing regions
    grow, regions with spare rows give them back. Regions holding a chart, picture or table keep their rows:
    those fill whatever room they are given."""

    cells = regions(html)
    needs: dict[int, int] = {}
    for index, cell in enumerate(cells):
        x0, y0, x1, y1 = cell_rect(cell)

        def inside(rect):
            cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + min(rect["h"], 40) / 2
            return x0 - 4 <= cx <= x1 + 4 and y0 - 4 <= cy <= y1 + 4

        if any(item["kind"] in ("chart", "table", "image") and inside(item) for item in items):
            continue
        bottoms = [item["text"]["y"] + item["text"]["h"] for item in items
                   if item["kind"] == "text" and item.get("text") and inside(item["text"])]
        if bottoms:
            needs[index] = max(1, math.ceil((max(bottoms) - y0 + 6) / ROW_PX - 0.05))
    return needs


def solve(html: str, overflow: dict[int, float], rows: dict[int, int] | None = None) -> str | None:
    """Rows re-allocated so every region holds its measured content: a region that overflows by
    N px gets ceil(N / row) more rows, the regions below it in its columns move down, regions
    anchored to the foot stay there. ``None`` when the page cannot hold it all."""

    cells, anchored = regions(html), _anchored(html)
    if not cells:
        return None
    need = [(rows or {}).get(index, (r1 - r0) + (math.ceil(overflow[index] / ROW_PX - 0.05)
                                                  if overflow.get(index, 0) > 2 else 0))
            for index, (_, _, r0, r1) in enumerate(cells)]
    order = sorted(range(len(cells)), key=lambda index: (cells[index][2], cells[index][0]))
    placed: dict[int, tuple[int, int]] = {}
    for index in order:
        c0, c1, r0, r1 = cells[index]
        if anchored[index]:
            top = LAST_LINE - need[index]          # a foot region keeps its foot
        else:
            above = [placed[other][1] for other in placed
                     if not anchored[other] and cells[other][2] < r0 or (cells[other][2] == r0 and other < index)
                     if cells[other][0] < c1 and c0 < cells[other][1] and not anchored[other]]
            # Measured rows: a region follows the one above it in its columns (moving up into rows that one
            # gave back), but never above where the designer started it unless something above it moved.
            top = max(above) if rows is not None and above else max([r0] + above)
        placed[index] = (top, top + need[index])
    # Nothing past the last line, and nothing on top of a foot region.
    for index, (top, bottom) in placed.items():
        if bottom > LAST_LINE or top < 1:
            return None
        for other, (o_top, o_bottom) in placed.items():
            if other != index and cells[other][0] < cells[index][1] and cells[index][0] < cells[other][1] \
                    and top < o_bottom and o_top < bottom:
                return None
    solved = [(c0, c1, placed[index][0], placed[index][1]) for index, (c0, c1, _, _) in enumerate(cells)]
    if solved == cells:
        return None
    index_iter = iter(solved)

    def replace(match: re.Match) -> str:
        c0, c1, r0, r1 = next(index_iter)
        return f"{match.group(1)}grid-column:{c0}/{c1};grid-row:{r0}/{r1}"

    return _CELL.sub(replace, html)
