"""What makes a page that fits still look unfinished, in words a model can act on.

These are judgements about a measured page, not about its facts: display type
jammed against its neighbours, two things competing to be the focus, a sentence
set in poster-size type, a hole in the middle of a column, red everywhere.
None of them is a reason to throw a page away; each is a reason to revise it.
"""

from __future__ import annotations

import re
from typing import Any

from ppt_agent.v2.htmldeck.browser import Measured
from ppt_agent.v2.ir import CANVAS_HEIGHT

KINDS = frozenset({"tight", "focal_conflict", "long_display", "hole", "red_overuse", "small_note"})  # = checks.AESTHETIC

DISPLAY_PX = 48.0       # type this large reads as a shape: a figure or a poster line
TIGHT_SHARE = 0.2       # air a display line needs above and below, as a share of its size
TIGHT_MAX = 12.0        # ...but a 230px figure does not need 58px of it
LONG_LINES, LONG_CHARS = 2, 24
STATEMENT_LINES, STATEMENT_CHARS = 3, 44  # a page that is only its statement may run longer
RIVAL_SHARE = 0.6       # a second display element this close in size competes with the first
MAX_DISPLAY = 2
HOLE_PX = 150.0
BESIDE_SHARE = 0.4      # how much of that band the next column fills
PAUSE_PX = 230.0        # even a full-width pause has a limit
REACH = {"visual": 0.62, "standard": 0.72, "deep": 0.80}
MAX_RED = 3
MIN_NOTE_PX = 15.0
ACCENT = "#C5341C"
LABELS = ("kicker", "k", "source", "pageno", "index")


def _classes(item: dict[str, Any]) -> list[str]:
    return str(item.get("cls", "")).split()


def _text(item: dict[str, Any]) -> str:
    return "".join("\n" if run.get("br") else run.get("t", "") for run in item["runs"]).strip()


def _short(text: str, limit: int = 14) -> str:
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[:limit] + "…"


def _size(item: dict[str, Any]) -> float:
    return max((run["size"] for run in item["runs"] if run.get("t", "").strip()), default=0.0)


def _rect(item: dict[str, Any]) -> dict[str, float]:
    """Line boxes rather than glyph boxes, as the overlap check uses."""

    rect = item["text"]
    pitch = item.get("linePx")
    if not pitch:
        return rect
    height = min(rect["h"], pitch * max(1, int(item.get("lines", 1))))
    return {"x": rect["x"], "y": rect["y"] + (rect["h"] - height) / 2, "w": rect["w"], "h": height}


def _hex(color: str | None) -> str:
    match = re.match(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)", color or "")
    return "#%02X%02X%02X" % tuple(int(part) for part in match.groups()) if match else ""


def _shared_width(a: dict[str, float], b: dict[str, float]) -> float:
    """How much of the narrower one sits under (or over) the other."""

    overlap = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    narrow = min(a["w"], b["w"])
    return overlap / narrow if narrow > 0 else 0.0


def find(measured: Measured, *, tier: str = "standard", accent: str = ACCENT) -> list[Any]:
    from ppt_agent.v2.htmldeck.checks import Defect

    defects: list[Defect] = []
    texts = [item for item in measured.items if item["kind"] == "text" and _text(item)]
    body = [item for item in texts if not any(name in ("source", "pageno") for name in _classes(item))]
    display = sorted((item for item in body if _size(item) >= DISPLAY_PX), key=_size, reverse=True)
    titled = any(name in ("t1", "t2") for item in body for name in _classes(item))

    # Display type with no air around it.
    for big in display:
        a, need = _rect(big), min(TIGHT_SHARE * _size(big), TIGHT_MAX)
        for other in body:
            if other is big or (other in display and _size(other) > _size(big)):
                continue
            b = _rect(other)
            if _shared_width(a, b) <= 0.2:
                continue
            gap = max(b["y"] - (a["y"] + a["h"]), a["y"] - (b["y"] + b["h"]))
            if -2 <= gap < need:
                defects.append(Defect(
                    "tight",
                    f"大字「{_short(_text(big))}」和「{_short(_text(other))}」只隔了 {max(gap, 0):.0f} 像素，贴得太近。"
                    f"至少留出 {need:.0f} 像素：把大字放进单独的区域并给够行数，或把它换小一号。",
                    _text(big)))
                break

    # A sentence set in poster-size type.
    for item in display:
        text = re.sub(r"\s+", "", _text(item))
        lines = int(item.get("lines", 1))
        max_lines, max_chars = (LONG_LINES, LONG_CHARS) if titled else (STATEMENT_LINES, STATEMENT_CHARS)
        if lines > max_lines or len(text) > max_chars:
            defects.append(Defect(
                "long_display",
                f"「{_short(_text(item))}」有 {len(text)} 个字、排了 {lines} 行，不能用这么大的字。"
                "页面已经有标题时，结论请用 call（结论条）或 t3，大字只留给数字。",
                _text(item)))
    for item in body:
        if "t1" in _classes(item) and int(item.get("lines", 1)) > 2:
            defects.append(Defect("long_display",
                                  f"标题「{_short(_text(item))}」排了 {item['lines']} 行，请改用 t2 或加宽标题区域。",
                                  _text(item)))

    # More than one thing asking to be seen first.
    if display:
        first = display[0]
        if any("t1" in _classes(item) for item in body if item is not first):
            defects.append(Defect(
                "focal_conflict",
                f"这一页既有大标题（t1）又有大字「{_short(_text(first))}」，两个都在抢第一眼。"
                "有大数字或大字的页，标题请用 t2。"))
        if len(display) > MAX_DISPLAY:
            defects.append(Defect(
                "focal_conflict",
                f"这一页有 {len(display)} 处大字，只保留「{_short(_text(first))}」做最大，其余降到 t3 以下。"))
        elif len(display) == 2 and _size(display[1]) > RIVAL_SHARE * _size(first):
            defects.append(Defect(
                "focal_conflict",
                f"「{_short(_text(first))}」和「{_short(_text(display[1]))}」差不多大，分不出主次。"
                "只留一个做焦点，另一个至少小两号。"))

    # A hole inside a column, or a page that stops short.
    blocks = [(_rect(item), _short(_text(item))) for item in body
              if not {"kicker", "arrow"} & set(_classes(item))]
    blocks += [({"x": item["x"], "y": item["y"], "w": item["w"], "h": item["h"]},
                "图表" if item["kind"] == "chart" else "表格")
               for item in measured.items if item["kind"] in ("chart", "table")]
    blocks.sort(key=lambda entry: entry[0]["y"])
    for index, (upper, label) in enumerate(blocks):
        bottom = upper["y"] + upper["h"]
        if bottom > 0.8 * CANVAS_HEIGHT:
            continue
        below = [(rect, name) for rect, name in blocks[index + 1:]
                 if rect["y"] >= bottom - 2 and _shared_width(upper, rect) > 0.5]
        if not below:
            continue
        nearest, name = min(below, key=lambda entry: entry[0]["y"])
        gap = nearest["y"] - bottom
        # Anything else in that band (a neighbour column's text does not count) fills the hole.
        filled = any(rect is not upper and rect is not nearest and _shared_width(upper, rect) > 0.5
                     and rect["y"] < nearest["y"] and rect["y"] + rect["h"] > bottom for rect, _ in blocks)
        if gap <= HOLE_PX or nearest["y"] >= 0.8 * CANVAS_HEIGHT or filled:
            continue
        # A band of air across the whole page is a pause. The same band beside a
        # busy column is a hole: one half of the page is working and the other is not.
        beside = sum(min(rect["y"] + rect["h"], nearest["y"]) - max(rect["y"], bottom)
                     for rect, _ in blocks
                     # things that start in the band; a taller neighbour in the same row does not count
                     if _shared_width(upper, rect) <= 0.2 and bottom - 2 <= rect["y"] < nearest["y"])
        if beside >= BESIDE_SHARE * gap or gap > PAUSE_PX:
            defects.append(Defect(
                "hole",
                f"「{label}」和下面的「{name}」之间空了 {gap:.0f} 像素"
                + ("，旁边一栏却排满了" if beside >= BESIDE_SHARE * gap else "") + "。"
                "请把下面的内容上移，或让主要元素占满这一块，不要让一栏中间留洞。"))
            break
    reach = max([rect["y"] + rect["h"] for rect, _ in blocks] + [0.0])
    need = REACH.get(tier, REACH["standard"]) * CANVAS_HEIGHT
    if blocks and min(reach, CANVAS_HEIGHT) < need:
        defects.append(Defect(
            "hole",
            f"内容只排到页面的 {reach / CANVAS_HEIGHT:.0%}，下面空着。请把内容区撑到底部（多占几行），或放大主要元素。"))

    # Red is for the focus and one marker.
    red = 0
    for item in body:
        names = _classes(item)
        if any(name in LABELS for name in names) or "arrow" in names:
            continue
        text = _text(item)
        if any(_hex(run.get("color")) == accent and run.get("t", "").strip(" →—-·") for run in item["runs"]):
            red += 1 if text else 0
    red += sum(1 for item in measured.items
               if item["kind"] == "box" and _hex(item.get("bg")) == accent and item["w"] * item["h"] > 2000
               and not re.match(r"rgba\(.*,\s*0\)$", item.get("bg") or ""))
    if red > MAX_RED:
        defects.append(Defect("red_overuse", f"朱红用了 {red} 处。只留给焦点和一个关键标记，其余改回墨色。"))

    for item in body:
        if any(name in LABELS for name in _classes(item)):
            continue
        sizes = [run["size"] for run in item["runs"] if run.get("t", "").strip()]
        if sizes and min(sizes) < MIN_NOTE_PX - 0.01:
            defects.append(Defect(
                "small_note",
                f"说明文字「{_short(_text(item))}」只有 {min(sizes):.0f} 像素，投影上看不清。请用 c 或更大的字号类。",
                _text(item)))
            break
    return list(dict.fromkeys(defects))
