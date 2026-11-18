"""How much text a form can hold, estimated before the page is designed.

Round 1 of the P4 final validation: of the first design attempts, most failed because the text did
not fit - regions 9-173px too short, blocks pushed into each other. The designer was given content
first and had to discover the space afterwards. Here the space comes first: each form (and chart
arrangement) has a text area in grid cells; the content's demand is estimated in lines of the body
type at that width; items past what fits go to the notes, and the designer is told the budget.

Grid: 12 columns of 72px with 24px gaps, rows of 75px. Body type 19px, 1.65 line height (31px);
headings 24px (35px a line); the closing line 24px (36px a line). Display figures are not counted
here: their room is part of each form's geometry below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

COLUMN, GAP, ROW = 72, 24, 75
BODY_PX, BODY_LINE, HEAD_LINE, CALL_LINE, ITEM_GAP = 19, 31, 35, 36, 14


@dataclass(frozen=True)
class Area:
    columns: int           # width of one text cell
    rows: float            # height of one text cell
    cells: int = 1         # cells side by side (one item each); 1 = items stacked in one column
    call_columns: int = 12  # width of the closing line


# Text area left for items once the form's main visual, title row and closing line have their room.
AREAS: dict[str, Area] = {
    "poster": Area(5, 3.5), "units": Area(5, 3.5), "strip": Area(12, 2.0, cells=3), "statement": Area(12, 2.5, cells=3),
    "columns": Area(4, 3.5, cells=3), "split": Area(6, 4.5), "ledger": Area(8, 5.5), "margin": Area(7, 5.0),
    "matrix": Area(6, 2.2, cells=4), "stairs": Area(3, 3.0, cells=4), "flow": Area(3, 3.0, cells=4),
    "timeline": Area(3, 3.0, cells=4), "figure": Area(4, 4.0), "table": Area(12, 1.5, cells=3), "layers": Area(5, 4.0),
    "chart:side": Area(5, 3.5), "chart:inset": Area(7, 3.5), "chart:full": Area(6, 1.6, cells=2),
    "chart:band": Area(4, 1.6, cells=3),
}


def width_px(columns: int) -> int:
    return columns * COLUMN + (columns - 1) * GAP


def chars_per_line(columns: int, px: int = BODY_PX) -> int:
    return max(4, int(width_px(columns) / px * 0.95))


def item_height(item: dict[str, Any], columns: int) -> float:
    per_line = chars_per_line(columns)
    body = len(str(item.get("body") or ""))
    lines = math.ceil(body / per_line) if body else 0
    value = 1 if item.get("value") else 0
    return HEAD_LINE + (value * HEAD_LINE) + lines * BODY_LINE + ITEM_GAP


@dataclass
class Budget:
    items: int             # how many items fit
    body_chars: int        # the longest an item's body may be, in characters
    call_chars: int        # the closing line, in characters, to stay on two lines
    over: bool             # whether the content as written is more than the space


def budget(content: dict[str, Any], form: str, arrangement: str = "") -> Budget | None:
    """The text budget for the items the form keeps (their number is the form's item limit, set
    elsewhere): how long each body may be so that they fit the form's text area."""

    area = AREAS.get(f"chart:{arrangement or 'side'}" if form == "chart" else form)
    if area is None:
        return None
    items = list(content.get("items") or [])
    kept = max(1, len(items))
    room = area.rows * ROW
    per_line = chars_per_line(area.columns)
    if area.cells > 1:
        lines = max(1, int((room - HEAD_LINE - ITEM_GAP) / BODY_LINE))
    else:
        lines = max(1, int((room / kept - HEAD_LINE - ITEM_GAP) / BODY_LINE))
    body_chars = lines * per_line
    over = any(len(str(item.get("body") or "")) > body_chars for item in items)
    return Budget(len(items), body_chars, 2 * chars_per_line(area.call_columns, 24), over)


def words(plan: Budget) -> str:
    return (f"这一页的文字预算（按这种版式的文字区算出来的）：每条说明不超过 {plan.body_chars} 个字，超过的删短措辞，"
            f"不要缩小字号、不要压缩行距；结论一句不超过 {plan.call_chars} 个字。"
            "区域的行号先按估计写：程序会按渲染出来的实际内容重新分配行高，并把下面的区域顺次下移。"
            "你要定的是构图——哪些区域左右并排、哪些上下相接、各占多宽、谁是焦点；同一列里的区域按从上到下的顺序写。")
