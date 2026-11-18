"""Find what is wrong with a measured page, in words a model can act on."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from ppt_agent.v2.evidence_check import _NUMBER, _key
from ppt_agent.v2.htmldeck.browser import Measured
from ppt_agent.v2.htmldeck.convert import ConvertError, chart_spec
from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH

MIN_TEXT_PX = 12.0
TOLERANCE = 2.0
CUT = 4.0  # how far text may cross a block's edge before it reads as a collision
FILL_BOTTOM = 0.62  # content pages should reach at least this far down the page
EXEMPT_CLASSES = ("pageno", "kicker", "index")
# Reasons to revise a page, never reasons to drop it.
AESTHETIC = frozenset({"tight", "focal_conflict", "long_display", "hole", "red_overuse", "small_note"})


@dataclass(frozen=True)
class Defect:
    # overflow | off_canvas | overlap | small_text | sparse | extra_number | bad_chart | unsupported
    # | unbound_graphic | missing, and the aesthetic kinds in ``AESTHETIC``
    kind: str
    message: str
    text: str = ""


def numbers_in(text: str) -> set[Decimal]:
    """Numeric values in ``text``, normalised the way the evidence check does."""

    found: set[Decimal] = set()
    for match in _NUMBER.finditer(text or ""):
        try:
            found.add(_key(match.group())[0].copy_abs().normalize())
        except (TypeError, InvalidOperation):
            continue
    return found


def allowed_numbers(values: Iterable[Any]) -> set[Decimal]:
    """Every number in verified content (nested strings and figures alike)."""

    found: set[Decimal] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                walk(child)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, (int, float)):
            found.update(numbers_in(f"{value:g}"))
        else:
            found.update(numbers_in(str(value)))

    walk(list(values))
    return found


def _item_text(item: dict[str, Any]) -> str:
    return "".join("\n" if run.get("br") else run.get("t", "") for run in item["runs"]).strip()


def _short(text: str, limit: int = 18) -> str:
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[:limit] + "…"


def _line_rect(item: dict[str, Any]) -> dict[str, float]:
    """The text's line boxes. The browser reports glyph boxes, which for CJK
    fonts are about a third taller than a tight line and would make stacked
    headings look as if they collide."""

    rect = item["text"]
    pitch = item.get("linePx")
    if not pitch:
        return rect
    height = min(rect["h"], pitch * max(1, int(item.get("lines", 1))))
    return {"x": rect["x"], "y": rect["y"] + (rect["h"] - height) / 2, "w": rect["w"], "h": height}


def _glyph_px(item: dict[str, Any]) -> float:
    return min((run["size"] for run in item.get("runs") or [] if run.get("t", "").strip()), default=0)


def _decorative(item: dict[str, Any]) -> bool:
    names = item.get("cls", "").split()
    return any(name.startswith("deco") or name == "backdrop" for name in names)


def _ordinal(token: str) -> bool:
    """"01", "3": a page counting its own items, not a claim about the source."""

    digits = token.strip()
    return bool(re.fullmatch(r"0\d|\d", digits))


# Type on a projected slide, by what it does (sizes in CSS px; 1px = 0.75pt on the 1280-wide page).
# Body text and the notes beside it must be read from the back of a room; labels and the source line
# may be smaller. Display figures and titles are large by design and are not listed.
ROLE_MIN_PX = {"body": 18.6, "secondary": 16.0, "caption": 14.6, "label": 14.0, "source": 12.0}
ROLE_NAMES = {"body": "正文", "secondary": "辅助说明", "caption": "图注", "label": "标签", "source": "来源"}
HARD_ROLES = {"body", "secondary", "caption"}
FIGURE_CLASSES = {"d1", "d2", "d3", "d4", "t3", "fig", "num", "key", "v", "nm"}  # where a figure is shown as data
_DISPLAY = {"d1", "d2", "d3", "d4", "t1", "t2", "t3", "q1", "h", "fig", "num", "key", "call", "unit", "nm",
            "title", "cover-title", "section-title", "section-no", "quote", "arrow"}


def _rgb(text: str) -> tuple[float, float, float] | None:
    match = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", text or "")
    return tuple(float(value) for value in match.groups()) if match else None


def contrast(foreground: str, background: str) -> float | None:
    """WCAG contrast ratio of two CSS rgb() colours."""

    first, second = _rgb(foreground), _rgb(background)
    if first is None or second is None:
        return None

    def luminance(rgb):
        channels = [value / 255 for value in rgb]
        channels = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def role_of(classes: str) -> str | None:
    names = set(classes.split())
    if names & {"source", "pageno"}:
        return "source"
    if names & _DISPLAY:
        return None
    if names & {"k", "kicker"}:
        return "label"
    if "c" in names:
        return "caption"
    if names & {"s", "lead", "label", "cover-sub"}:
        return "secondary"
    return "body"


def find_defects(
    measured: Measured,
    allowed: set[Decimal] | None = None,
    *,
    require_fill: bool = True,
    tier: str | None = None,
    roles: bool = False,
) -> list[Defect]:
    """``tier`` (visual / standard / deep) also asks for the aesthetic checks,
    which judge a designed content page against the density it was planned at."""

    defects: list[Defect] = []
    for note in measured.notes:
        defects.append(Defect("unsupported", f"用了不支持的写法：{note}。请只用主题提供的类。"))
    for clip in measured.clipped:
        label = _short(clip.get("text", ""))
        if clip.get("region"):
            over, wide = clip.get("over", 0), clip.get("wide", 0)
            if over > wide:
                rows = max(1, -(-int(over) // 75))
                message = (f"以「{label}」开头的区域，内容比它占的格子高出 {over:.0f} 像素。"
                           f"请把这个区域的 grid-row 再多占 {rows} 行（并相应挪动下面的区域），或删掉次要文字。")
            else:
                message = (f"以「{label}」开头的区域，内容比它占的格子宽出 {wide:.0f} 像素。"
                           "请让它多占几列，或把大数字换小一号（d1→d2→d3）。")
        else:
            message = f"「{label}」所在的容器装不下内容，请减少文字或放大容器。"
        defects.append(Defect("overflow", message, clip.get("text", "")))
    texts = [item for item in measured.items if item["kind"] == "text" and _item_text(item)]
    for item in texts:
        rect, parent, label = _line_rect(item), item["parent"], _short(_item_text(item))
        if (rect["x"] < -TOLERANCE or rect["y"] < -TOLERANCE
                or rect["x"] + rect["w"] > CANVAS_WIDTH + TOLERANCE
                or rect["y"] + rect["h"] > CANVAS_HEIGHT + TOLERANCE):
            defects.append(Defect("off_canvas", f"文字「{label}」超出了页面边界。", label))
        if (rect["x"] < parent["x"] - TOLERANCE or rect["y"] < parent["y"] - TOLERANCE
                or rect["x"] + rect["w"] > parent["x"] + parent["w"] + TOLERANCE
                or rect["y"] + rect["h"] > parent["y"] + parent["h"] + TOLERANCE):
            defects.append(Defect("overflow", f"文字「{label}」超出了它所在的色块，请减少文字或放大色块。", label))
        sizes = [run["size"] for run in item["runs"] if run.get("t", "").strip()]
        text_here = re.sub(r"\s+", "", _item_text(item))
        if roles and item.get("ground") and role_of(item.get("cls", "")) != "source":
            for run in item["runs"]:
                if not run.get("t", "").strip():
                    continue
                ratio = contrast(run.get("color", ""), item["ground"])
                need = 3.0 if run.get("size", 0) >= 24 else 4.5
                if ratio is not None and ratio < need:
                    defects.append(Defect(
                        "legibility",
                        f"文字「{label}」和背景的对比度只有 {ratio:.1f}:1（至少 {need:g}:1），投影上读不出来。"
                        "换成和背景反差足够的颜色，或去掉它下面的底色。", label))
                    break
        if roles and item.get("lines", 1) >= 2 and item.get("lastLine") is not None and sizes \
                and re.search(r"[\u4e00-\u9fff]", text_here) and item["lastLine"] < 1.3 * max(sizes):
            # One character alone on the last line ("…规模普" / "及").
            defects.append(Defect(
                "orphan", f"文字「{label}」最后一行只剩一个字。请删短几个字，或给它更宽的区域，让它整行结束。", label))
        if roles and item.get("lines", 1) >= 2 and len(text_here) / item["lines"] < 3.0:
            # Two characters a line is not a line: the words have been squeezed into a column.
            defects.append(Defect(
                "overflow", f"文字「{label}」被挤成了每行一两个字的竖条，读不成句。请给它更宽的区域，或删短它。", label))
        if roles and sizes:
            role = role_of(item.get("cls", ""))
            least = ROLE_MIN_PX.get(role)
            if least is not None and min(sizes) < least - 0.05:
                pt = min(sizes) * 0.75
                if role in HARD_ROLES:
                    defects.append(Defect(
                        "legibility",
                        f"{ROLE_NAMES[role]}「{label}」只有 {pt:.1f}pt，投影看不清（至少 {least * 0.75:.1f}pt）。"
                        "不要缩小字号：删掉次要的话、把条目合并，或换一种更宽松的排法。", label))
                else:
                    defects.append(Defect("small_text", f"{ROLE_NAMES[role]}「{label}」只有 {pt:.1f}pt，偏小。", label))
            continue
        if sizes and min(sizes) < MIN_TEXT_PX - 0.01:
            defects.append(Defect("small_text", f"文字「{label}」只有 {min(sizes):.0f} 像素，太小。请减少内容，不要缩小字号。", label))

    if roles:
        # A number and its unit are one thing: the unit stands right after its figure, not across the page.
        for figure in texts:
            size = max((run["size"] for run in figure["runs"] if run.get("t", "").strip()), default=0)
            if size < 48 or not re.search(r"\d\s*$", _item_text(figure)):
                continue
            box = _line_rect(figure)
            for unit in texts:
                if "unit" not in unit.get("cls", "").split():
                    continue
                ubox = _line_rect(unit)
                same_line = ubox["y"] < box["y"] + box["h"] and box["y"] < ubox["y"] + ubox["h"]
                gap = ubox["x"] - (box["x"] + box["w"])
                below = (not same_line and abs(ubox["x"] - box["x"]) < 12
                         and -6 <= ubox["y"] - (box["y"] + box["h"]) < size * 0.5)
                if below and len(_item_text(unit).strip()) <= 2:
                    # "29.4" with "个" on a line of its own under it: a one-character unit is part of the figure.
                    defects.append(Defect(
                        "split_unit",
                        f"单位「{_item_text(unit).strip()}」只有一两个字，却单独放在数字「{_short(_item_text(figure))}」下面一行。"
                        "把它和数字放在同一行（同一个 rowx 里，或直接写进数字的元素）。", _item_text(unit)))
                    continue
                if same_line and gap > max(40.0, size * 0.6):
                    defects.append(Defect(
                        "split_unit",
                        f"单位「{_short(_item_text(unit))}」离数字「{_short(_item_text(figure))}」有 {gap:.0f} 像素远，读起来像两样东西。"
                        "把单位紧跟在数字后面（同一个 rowx，不要用 spread 或 g 系列把它们拉开）。", _item_text(unit)))
    if roles:
        # One figure shown as data twice ("483.8" as the focal figure and "483.8万个" in the row below):
        # the second showing adds nothing. A title or a sentence restating it is fine.
        shown: dict[Decimal, list[str]] = {}
        for item in texts:
            names = set(item.get("cls", "").split())
            text_here = _item_text(item).strip()
            if not names & FIGURE_CLASSES or names & {"t1", "t2", "title", "kicker"} or len(text_here) > 12:
                continue
            numbers = numbers_in(text_here)
            if len(numbers) != 1:
                continue  # "28.5→28.4→29.4" compares; it does not repeat
            for number in numbers:
                if not (1900 <= number <= 2100 and number == number.to_integral_value()):  # a year is no figure
                    shown.setdefault(number, []).append(text_here)
        for number, places in shown.items():
            if len(places) >= 2:
                defects.append(Defect(
                    "repeated_figure",
                    f"数字「{places[1]}」在页面上作为数据出现了 {len(places)} 次（{'、'.join(places)}），重复的那处没有带来新信息。"
                    "只保留最有力的一处；另一处改写成比较、趋势或解释，或者删掉。", places[1]))
    for index, first in enumerate(texts):
        a = _line_rect(first)
        for second in texts[index + 1:]:
            b = _line_rect(second)
            width = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            height = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if width <= TOLERANCE or height <= TOLERANCE:
                continue
            smaller = min(a["w"] * a["h"], b["w"] * b["h"])
            # Or one glyph's worth: half a character wide and half a line tall. A long label makes 10% of
            # its area a lot, and a figure can run a whole character into it (CNNIC 57: "6.02亿人" 29px
            # into a 394px label, 7% of it, plainly colliding on the slide).
            glyph = min(_glyph_px(first), _glyph_px(second))
            if (smaller > 0 and width * height / smaller > 0.1) or (
                    glyph and width >= 0.5 * glyph and height >= 0.5 * min(a["h"], b["h"])):
                defects.append(Defect(
                    "overlap",
                    f"文字「{_short(_item_text(first))}」和「{_short(_item_text(second))}」重叠了。",
                    _item_text(first),
                ))

    blocks = [item for item in measured.items if item["kind"] == "box" and not _decorative(item)
              and item["w"] * item["h"] < 0.9 * CANVAS_WIDTH * CANVAS_HEIGHT
              and item["w"] > 24 and item["h"] > 24]
    for block in blocks:
        if (block["x"] < -TOLERANCE or block["y"] < -TOLERANCE
                or block["x"] + block["w"] > CANVAS_WIDTH + TOLERANCE
                or block["y"] + block["h"] > CANVAS_HEIGHT + TOLERANCE):
            over = max(block["y"] + block["h"] - CANVAS_HEIGHT, block["x"] + block["w"] - CANVAS_WIDTH, 0)
            defects.append(Defect(
                "off_canvas",
                f"内容比页面多出约 {over:.0f} 像素，有色块被挤出了页面。请删掉一个块或一段文字，不要只改措辞。"))
            break
    footer = [item for item in texts if any(name in item.get("cls", "").split() for name in ("source", "pageno"))]
    for block in blocks:
        hit = False
        for item in footer:
            rect = _line_rect(item)
            width = min(block["x"] + block["w"], rect["x"] + rect["w"]) - max(block["x"], rect["x"])
            height = min(block["y"] + block["h"], rect["y"] + rect["h"]) - max(block["y"], rect["y"])
            if width > TOLERANCE and height > TOLERANCE:
                hit = True
        if hit:
            defects.append(Defect("overlap", "内容区的色块压到了页脚（资料来源或页码），内容太高了，请减少内容。"))
            break
    for index, first in enumerate(blocks):
        for second in blocks[index + 1:]:
            width = min(first["x"] + first["w"], second["x"] + second["w"]) - max(first["x"], second["x"])
            height = min(first["y"] + first["h"], second["y"] + second["h"]) - max(first["y"], second["y"])
            if width <= TOLERANCE or height <= TOLERANCE:
                continue
            overlap = width * height
            smaller = min(first["w"] * first["h"], second["w"] * second["h"])
            # One block inside another is nesting; a partial intersection is a collision.
            if 0.05 * smaller < overlap < 0.95 * smaller:
                defects.append(Defect("overlap", "有两个色块互相压住了（通常是内容太多把下面的块挤上来），请减少内容。"))
                break
        else:
            continue
        break

    # Text half under a block or a ruled box that is not its own container: the block
    # (or the rule along its edge) cuts through the words.
    painted = [item for item in measured.items if item["kind"] == "box" and not _decorative(item)
               and item["w"] * item["h"] < 0.9 * CANVAS_WIDTH * CANVAS_HEIGHT]
    for item in texts:
        if any(name in item.get("cls", "").split() for name in ("source", "pageno")):
            continue
        rect = _line_rect(item)
        for block in painted:
            width = min(block["x"] + block["w"], rect["x"] + rect["w"]) - max(block["x"], rect["x"])
            height = min(block["y"] + block["h"], rect["y"] + rect["h"]) - max(block["y"], rect["y"])
            if width <= CUT or height <= CUT:
                continue
            inside = (rect["y"] >= block["y"] - CUT and rect["y"] + rect["h"] <= block["y"] + block["h"] + CUT)
            if not inside:
                defects.append(Defect(
                    "overlap",
                    f"文字「{_short(_item_text(item))}」被旁边的色块或线框压住了一部分（上下错开了 "
                    f"{min(height, rect['h'] - height):.0f} 像素）。请给它们各自的区域，行号不要重叠；内容太多就删减。",
                    _item_text(item)))
                break

    for data in (item for item in measured.items if item["kind"] in ("chart", "table", "image")):
        for item in texts:
            rect = _line_rect(item)
            width = min(data["x"] + data["w"], rect["x"] + rect["w"]) - max(data["x"], rect["x"])
            height = min(data["y"] + data["h"], rect["y"] + rect["h"]) - max(data["y"], rect["y"])
            if width > TOLERANCE and height > TOLERANCE and width * height > 0.1 * rect["w"] * rect["h"]:
                name = {"chart": "图表", "image": "原文图"}.get(data["kind"], "表格")
                defects.append(Defect("overlap", f"文字「{_short(_item_text(item))}」压在了{name}上，请把它们放进不同的区域。",
                                      _item_text(item)))
                break

    for item in measured.items:
        if item["kind"] == "image" and (item["w"] < 300 or item["h"] < 170):
            defects.append(Defect("bad_chart", f"原文图只有 {item['w']:.0f}×{item['h']:.0f} 像素，图里的字看不清。"
                                               "请让它所在的区域至少占 6 列、4 行。"))
        if item["kind"] in ("chart", "table", "image") and (
            item["x"] < -TOLERANCE or item["y"] < -TOLERANCE
            or item["x"] + item["w"] > CANVAS_WIDTH + TOLERANCE
            or item["y"] + item["h"] > CANVAS_HEIGHT + TOLERANCE
        ):
            name = {"chart": "图表", "image": "原文图"}.get(item["kind"], "表格")
            defects.append(Defect("off_canvas", f"{name}超出了页面边界，请减少内容或缩小它。"))
        if item["kind"] == "chart":
            try:
                chart_spec(item["spec"])
            except ConvertError as error:
                defects.append(Defect("bad_chart", f"图表数据有问题：{error}"))
            if item["w"] < 200 or item["h"] < 140:
                defects.append(Defect("bad_chart", "图表区域太小，至少需要 200×140 像素。"))
        elif item["kind"] == "table":
            if not item["rows"] or not item["headers"]:
                defects.append(Defect("bad_chart", "表格需要一行表头和至少一行内容。"))
            elif len(item["headers"]) > 8 or len(item["rows"]) > 12:
                defects.append(Defect("bad_chart", "表格最多 8 列、12 行，请拆分或删减。"))

    if require_fill and tier is None:
        # How far down the page real content reaches: text (not the footer),
        # charts, tables and any block that is not a full-page backdrop.
        reach = [0.0]
        for item in measured.items:
            if item["kind"] == "text":
                if any(name in item.get("cls", "").split() for name in ("source", "pageno")):
                    continue
                reach.append(item["text"]["y"] + item["text"]["h"])
            elif item["kind"] != "box" or item["w"] * item["h"] < 0.9 * CANVAS_WIDTH * CANVAS_HEIGHT:
                reach.append(item["y"] + item["h"])
        if min(max(reach), CANVAS_HEIGHT) < FILL_BOTTOM * CANVAS_HEIGHT:
            defects.append(Defect("sparse", "页面下半部分是空的。请把内容区撑满，或放大主要元素。"))

    if allowed is not None:
        for item in texts:
            if any(name in item.get("cls", "").split() for name in EXEMPT_CLASSES):
                continue
            text = _item_text(item)
            for match in _NUMBER.finditer(text):
                token = match.group().strip()
                if _ordinal(token):
                    continue
                try:
                    value = _key(token)[0].copy_abs().normalize()
                except (TypeError, InvalidOperation):
                    continue
                if value not in allowed:
                    defects.append(Defect("extra_number", f"数字「{token}」不在给定内容里，请删除或改用给定的数字。", text))
    # A unit cut in two - "6233亿" with "美元" on the line below - reads as two things. (A whole unit set
    # under its figure is a choice some pages make; half of one is a mistake.)
    for unit in (item for item in texts if "unit" in str(item.get("cls", "")).split()):
        own = _line_rect(unit)
        middle = own["y"] + own["h"] / 2
        figures = [item for item in texts if item is not unit and any(
            run["size"] >= 48 for run in item["runs"] if run.get("t", "").strip())]
        if any(_line_rect(item)["y"] <= middle <= _line_rect(item)["y"] + _line_rect(item)["h"] for item in figures):
            continue
        above = [item for item in figures
                 if 0 <= own["y"] - (_line_rect(item)["y"] + _line_rect(item)["h"]) < 40
                 and min(own["x"] + own["w"], _line_rect(item)["x"] + _line_rect(item)["w"]) > max(own["x"], _line_rect(item)["x"])]
        if above and re.search(r"[^\d\s.,%％+\-→~约超近]$", _item_text(above[0])):
            defects.append(Defect(
                "split_unit",
                f"「{_short(_item_text(above[0]))}」和「{_short(_item_text(unit))}」是一个单位被拆成了两行。数字和完整的单位必须在同一行："
                '把它们放进同一个 <div class="rowx base">，放不下就把数字换小一号或给这个区域多几列。',
                _item_text(unit)))
    if tier is not None:
        from ppt_agent.v2.htmldeck import aesthetics

        defects.extend(aesthetics.find(measured, tier=tier))
    return list(dict.fromkeys(defects))
