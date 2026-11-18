"""How a designed page is composed, measured from where its elements landed.

Chart type, wording and colour do not enter: two pages with a bar chart and a pie chart
placed the same way, a big figure beside them, are the same composition. What does enter:

- split: how the body is divided - side by side (``cols``), stacked (``rows``) or neither,
  and where the division falls;
- weight: the visual centre of gravity (display type, data and colour blocks weigh most);
- dominant: how much of the page its largest element takes;
- whitespace: how much of the body is left empty;
- path: the order the eye meets the major elements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ppt_agent.v2.ir import (CANVAS_HEIGHT, CANVAS_WIDTH, BoxItem, ChartItem, ImageItem, PageDesign, RichTextItem,
                             TableItem)

DISPLAY_PT = 40.0       # type this large is read as a shape
BODY_TOP = 0.16         # the heading band is the same on every page and is left out
BODY_BOTTOM = 0.93      # so is the foot
MIN_SHARE = 0.012       # smaller than this is detail
GRID = (24, 14)
WEIGHT = {"data": 1.0, "display": 1.4, "block": 0.6, "text": 0.35}
CLOSE = 0.2             # compositions nearer than this read as the same
WINDOW = 4              # pages apart that a reader still compares


@dataclass(frozen=True)
class Composition:
    split: str          # cols | rows | none
    ratio: float        # share of the body before the division (0.5 when none)
    cx: float           # centre of gravity, 0-1 across the body
    cy: float
    dominant: float     # largest element's share of the body
    whitespace: float   # empty share of the body
    path: str           # kinds of the major elements in reading order, e.g. "display>data>text"

    def as_dict(self) -> dict:
        return asdict(self)


def _blocks(page: PageDesign) -> list[tuple[str, float, float, float, float]]:
    """(kind, left, top, right, bottom) of the body's major elements, in body units 0-1."""

    top_px, bottom_px = BODY_TOP * CANVAS_HEIGHT, BODY_BOTTOM * CANVAS_HEIGHT
    height = bottom_px - top_px
    found = []
    for element in page.elements:
        if isinstance(element, (ChartItem, TableItem, ImageItem)):
            kind = "data"
        elif isinstance(element, BoxItem):
            if element.frame.w * element.frame.h > 0.85 * CANVAS_WIDTH * CANVAS_HEIGHT:
                continue  # page background
            if min(element.frame.w, element.frame.h) < 12:
                continue  # a rule
            kind = "block"
        elif isinstance(element, RichTextItem):
            size = max((run.size_pt for run in element.runs if run.text.strip()), default=0)
            if not any(run.text.strip() for run in element.runs):
                continue
            kind = "display" if size >= DISPLAY_PT else "text"
        else:
            continue
        frame = element.frame
        right, bottom = frame.x + frame.w, frame.y + frame.h
        if isinstance(element, RichTextItem):
            # Text boxes carry spare width; count only where the words are.
            chars = sum(len(run.text) for run in element.runs)
            size = max((run.size_pt for run in element.runs if run.text.strip()), default=12) / 0.75
            lines = max(1, round(frame.h / (size * 1.3)))
            right = min(right, frame.x + max(size, chars * size * 0.92 / lines))
        left, top = max(frame.x, 0.0) / CANVAS_WIDTH, (max(frame.y, top_px) - top_px) / height
        right, bottom = min(right, CANVAS_WIDTH) / CANVAS_WIDTH, (min(bottom, bottom_px) - top_px) / height
        if right <= left or bottom <= top or (right - left) * (bottom - top) < MIN_SHARE:
            continue
        found.append((kind, left, top, right, bottom))
    return found


def _division(blocks, axis: int) -> float | None:
    """A gap across the whole body, on the x (axis 0) or y (axis 1) axis, with substantial
    content on both sides; the position of the widest such gap."""

    spans = sorted((block[1 + axis], block[3 + axis]) for block in blocks)
    best, reach = None, None
    for start, end in spans:
        if reach is not None and start > reach + 0.008:
            before = sum((b[3 + axis] - b[1 + axis]) * (b[4 - axis] - b[2 - axis])
                         for b in blocks if b[3 + axis] <= reach + 1e-6)
            after = sum((b[3 + axis] - b[1 + axis]) * (b[4 - axis] - b[2 - axis])
                        for b in blocks if b[1 + axis] >= start - 1e-6)
            total = before + after
            if total and min(before, after) / total > 0.1:
                gap = start - reach
                if best is None or gap > best[1]:
                    best = ((reach + start) / 2, gap)
        reach = end if reach is None else max(reach, end)
    return None if best is None else best[0]


def of(page: PageDesign) -> Composition:
    blocks = _blocks(page)
    if not blocks:
        return Composition("none", 0.5, 0.5, 0.5, 0.0, 1.0, "")
    # A strip across the whole width (a closing line, a rule with a note) does not undo a
    # side-by-side body above it.
    narrow = [block for block in blocks if block[3] - block[1] < 0.8]
    column, row = _division(narrow, 0) if len(narrow) >= 2 else None, _division(blocks, 1)
    if column is not None:
        split, ratio = "cols", column
    elif row is not None:
        split, ratio = "rows", row
    else:
        split, ratio = "none", 0.5
    mass = cx = cy = 0.0
    for kind, left, top, right, bottom in blocks:
        weight = WEIGHT[kind] * (right - left) * (bottom - top)
        mass += weight
        cx += weight * (left + right) / 2
        cy += weight * (top + bottom) / 2
    columns, rows = GRID
    covered = sum(
        any(left <= (i + 0.5) / columns <= right and top <= (j + 0.5) / rows <= bottom
            for _, left, top, right, bottom in blocks)
        for i in range(columns) for j in range(rows))
    dominant = max((right - left) * (bottom - top) for _, left, top, right, bottom in blocks)
    major = sorted((block for block in blocks if (block[3] - block[1]) * (block[4] - block[2]) >= 0.04
                    or block[0] in ("data", "display")),
                   key=lambda block: (round(block[2] * 3), block[1]))
    path, last = [], None
    for block in major:
        if block[0] != last:
            path.append(block[0])
        last = block[0]
    return Composition(split, round(ratio, 3), round(cx / mass, 3), round(cy / mass, 3), round(dominant, 3),
                       round(1 - covered / (columns * rows), 3), ">".join(path[:5]))


def mirrored(page: Composition) -> Composition:
    """The same composition flipped left to right: chart on the other side is not a new layout."""

    path = page.path
    if page.split == "cols":
        parts = path.split(">")
        path = ">".join(parts[::-1]) if len(parts) == 2 else path
    return Composition(page.split, round(1 - page.ratio, 3) if page.split == "cols" else page.ratio,
                       round(1 - page.cx, 3), page.cy, page.dominant, page.whitespace, path)


def distance(first: Composition, second: Composition) -> float:
    """0 for the same composition, towards 1 for unrelated ones; a mirror image counts as the same."""

    return min(_distance(first, second), _distance(mirrored(first), second))


def _distance(first: Composition, second: Composition) -> float:

    split = 0.0 if first.split == second.split else 1.0
    if not split and first.split != "none":
        split = min(1.0, abs(first.ratio - second.ratio) / 0.3)
    weight = min(1.0, ((first.cx - second.cx) ** 2 + (first.cy - second.cy) ** 2) ** 0.5 / 0.35)
    dominant = min(1.0, abs(first.dominant - second.dominant) / 0.3)
    space = min(1.0, abs(first.whitespace - second.whitespace) / 0.3)
    path = 0.0 if first.path == second.path else 1.0
    return round(0.3 * split + 0.25 * weight + 0.15 * dominant + 0.1 * space + 0.2 * path, 3)


def close_pairs(compositions: dict[int, Composition], *, window: int = WINDOW,
                limit: float = CLOSE) -> list[tuple[int, int, float]]:
    """(earlier, later, distance) of pages within ``window`` of each other composed alike."""

    pages = sorted(compositions)
    found = []
    for index, later in enumerate(pages):
        for earlier in pages[:index]:
            if later - earlier <= window:
                gap = distance(compositions[earlier], compositions[later])
                if gap < limit:
                    found.append((earlier, later, gap))
    return found


def rhythm(compositions: dict[int, Composition]) -> dict:
    """Deck-level numbers: how alike neighbouring pages are, and how the weight moves."""

    pages = sorted(compositions)
    steps = [distance(compositions[a], compositions[b]) for a, b in zip(pages, pages[1:])]
    sides = ["L" if compositions[page].cx < 0.45 else "R" if compositions[page].cx > 0.55 else "C" for page in pages]
    run, longest = 1, 1
    for a, b in zip(sides, sides[1:]):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    return {"pages": len(pages), "mean_neighbour_distance": round(sum(steps) / len(steps), 3) if steps else None,
            "min_neighbour_distance": min(steps) if steps else None,
            "close_pairs": [list(pair) for pair in close_pairs(compositions)],
            "weight_sides": "".join(sides), "longest_same_side": longest,
            "splits": {name: sum(c.split == name for c in compositions.values()) for name in ("cols", "rows", "none")}}
