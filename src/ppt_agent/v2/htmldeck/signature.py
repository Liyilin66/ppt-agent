"""A coarse fingerprint of how a page is arranged, to measure layout repetition."""

from __future__ import annotations

from collections import Counter

from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH, BoxItem, ChartItem, PageDesign, TableItem

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
