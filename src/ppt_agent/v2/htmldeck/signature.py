"""A coarse fingerprint of how a page is arranged, to measure layout repetition."""

from __future__ import annotations

from collections import Counter

from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH, BoxItem, ChartItem, ImageItem, PageDesign, TableItem

COLUMNS, ROWS = 6, 4
MIN_SHARE = 0.03  # blocks smaller than this share of the page are detail, not structure
FULL_BLEED = 0.85


def of(page: PageDesign) -> str:
    """Major blocks snapped to a 6x4 grid, e.g. ``box:0-3x1-3|chart:3-6x1-3``."""

    area = CANVAS_WIDTH * CANVAS_HEIGHT
    parts = set()
    for element in page.elements:
        if isinstance(element, BoxItem):
            kind = "box"
        elif isinstance(element, ChartItem):
            kind = "chart"
        elif isinstance(element, TableItem):
            kind = "table"
        else:
            continue
        frame = element.frame
        left, top = max(frame.x, 0.0), max(frame.y, 0.0)
        right, bottom = min(frame.x + frame.w, CANVAS_WIDTH), min(frame.y + frame.h, CANVAS_HEIGHT)
        if right <= left or bottom <= top:
            continue
        share = (right - left) * (bottom - top) / area
        if share < MIN_SHARE or (kind == "box" and share > FULL_BLEED):
            continue
        c0, c1 = round(left / CANVAS_WIDTH * COLUMNS), round(right / CANVAS_WIDTH * COLUMNS)
        r0, r1 = round(top / CANVAS_HEIGHT * ROWS), round(bottom / CANVAS_HEIGHT * ROWS)
        parts.add(f"{kind}:{c0}-{max(c1, c0 + 1)}x{r0}-{max(r1, r0 + 1)}")
    return "|".join(sorted(parts)) or "text"


def conflicts(signatures: list[str], *, window: int = 5, max_share: float = 0.08) -> dict:
    """Where a layout repeats too soon, and which layouts are over-used overall."""

    near = [
        index for index, signature in enumerate(signatures)
        if signature in signatures[max(0, index - window + 1): index]
    ]
    counts = Counter(signatures)
    total = max(len(signatures), 1)
    overused = {signature: count for signature, count in counts.items() if count / total > max_share}
    return {
        "pages": len(signatures),
        "distinct": len(counts),
        "repeats_within_window": near,
        "overused": overused,
        "largest_share": round(max(counts.values()) / total, 3) if counts else 0.0,
    }


# --- silhouette: what is left of a page when the words are taken away ---------

GRID_W, GRID_H = 8, 4
BIG_TYPE_PT = 44.0  # display figures read as a shape, not as text


def silhouette(page: PageDesign) -> str:
    """The page as an 8x4 grid of what dominates each cell.

    ``.`` empty, ``t`` text, ``T`` display-size type, ``#`` a colour block,
    ``d`` a chart or table. Two pages with nearly the same string have the same
    outline however different their figures and wording are.
    """

    from ppt_agent.v2.ir import RichTextItem

    cell_w, cell_h = CANVAS_WIDTH / GRID_W, CANVAS_HEIGHT / GRID_H
    cover = [[{} for _ in range(GRID_W)] for _ in range(GRID_H)]
    for element in page.elements:
        if isinstance(element, BoxItem):
            kind = "#"
            if element.frame.w * element.frame.h > FULL_BLEED * CANVAS_WIDTH * CANVAS_HEIGHT:
                continue
            if min(element.frame.w, element.frame.h) < 12:
                continue  # a rule, not a block
        elif isinstance(element, (ChartItem, TableItem, ImageItem)):
            kind = "d"
        elif isinstance(element, RichTextItem):
            size = max((run.size_pt for run in element.runs if run.text.strip()), default=0)
            kind = "T" if size >= BIG_TYPE_PT else "t"
        else:
            continue
        frame = element.frame
        left, top = max(frame.x, 0.0), max(frame.y, 0.0)
        right, bottom = min(frame.x + frame.w, CANVAS_WIDTH), min(frame.y + frame.h, CANVAS_HEIGHT)
        if isinstance(element, RichTextItem):
            # Unwrapped lines are stored with spare width; count only where the words are.
            chars = sum(len(run.text) for run in element.runs)
            size = max((run.size_pt for run in element.runs if run.text.strip()), default=12) / 0.75
            lines = max(1, round((bottom - top) / (size * 1.3)))
            right = min(right, left + max(size, chars * size * 0.9 / lines))
        for row in range(GRID_H):
            for column in range(GRID_W):
                x0, y0 = column * cell_w, row * cell_h
                width = min(right, x0 + cell_w) - max(left, x0)
                height = min(bottom, y0 + cell_h) - max(top, y0)
                if width > 0 and height > 0:
                    cover[row][column][kind] = cover[row][column].get(kind, 0.0) + width * height
    rank = {"T": 4, "d": 3, "#": 2, "t": 1}
    cells = []
    for row in cover:
        for cell in row:
            strong = {kind: area for kind, area in cell.items() if area > 0.08 * cell_w * cell_h}
            cells.append(max(strong, key=lambda kind: (rank[kind], strong[kind])) if strong else ".")
    return "".join(cells)


def distance(first: str, second: str) -> int:
    """How many of the 32 cells differ between two silhouettes."""

    return sum(a != b for a, b in zip(first, second))


def lookalikes(silhouettes: dict[int, str], *, threshold: int = 5) -> list[tuple[int, int, int]]:
    """(earlier page, later page, distance) for every pair with nearly the same outline,
    compared across the whole deck."""

    pages = sorted(silhouettes)
    found = []
    for index, later in enumerate(pages):
        for earlier in pages[:index]:
            gap = distance(silhouettes[earlier], silhouettes[later])
            if gap <= threshold:
                found.append((earlier, later, gap))
    return found
