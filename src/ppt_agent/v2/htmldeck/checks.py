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
FILL_BOTTOM = 0.62  # content pages should reach at least this far down the page
EXEMPT_CLASSES = ("pageno", "kicker", "index")


@dataclass(frozen=True)
class Defect:
    kind: str  # overflow | off_canvas | overlap | small_text | sparse | extra_number | bad_chart | unsupported
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


def _ordinal(token: str) -> bool:
    """"01", "3": a page counting its own items, not a claim about the source."""

    digits = token.strip()
    return bool(re.fullmatch(r"0\d|\d", digits))


def find_defects(
    measured: Measured,
    allowed: set[Decimal] | None = None,
    *,
    require_fill: bool = True,
) -> list[Defect]:
    defects: list[Defect] = []
    for note in measured.notes:
        defects.append(Defect("unsupported", f"用了不支持的写法：{note}。请只用主题提供的类。"))
    for clip in measured.clipped:
        defects.append(Defect("overflow", f"「{_short(clip.get('text', ''))}」所在的容器装不下内容，请减少文字或放大容器。", clip.get("text", "")))
    texts = [item for item in measured.items if item["kind"] == "text" and _item_text(item)]
    for item in texts:
        rect, parent, label = item["text"], item["parent"], _short(_item_text(item))
        if (rect["x"] < -TOLERANCE or rect["y"] < -TOLERANCE
                or rect["x"] + rect["w"] > CANVAS_WIDTH + TOLERANCE
                or rect["y"] + rect["h"] > CANVAS_HEIGHT + TOLERANCE):
            defects.append(Defect("off_canvas", f"文字「{label}」超出了页面边界。", label))
        if (rect["x"] < parent["x"] - TOLERANCE or rect["y"] < parent["y"] - TOLERANCE
                or rect["x"] + rect["w"] > parent["x"] + parent["w"] + TOLERANCE
                or rect["y"] + rect["h"] > parent["y"] + parent["h"] + TOLERANCE):
            defects.append(Defect("overflow", f"文字「{label}」超出了它所在的色块，请减少文字或放大色块。", label))
        sizes = [run["size"] for run in item["runs"] if run.get("t", "").strip()]
        if sizes and min(sizes) < MIN_TEXT_PX - 0.01:
            defects.append(Defect("small_text", f"文字「{label}」只有 {min(sizes):.0f} 像素，太小。请减少内容，不要缩小字号。", label))

    for index, first in enumerate(texts):
        a = first["text"]
        for second in texts[index + 1:]:
            b = second["text"]
            width = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            height = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if width <= TOLERANCE or height <= TOLERANCE:
                continue
            smaller = min(a["w"] * a["h"], b["w"] * b["h"])
            if smaller > 0 and width * height / smaller > 0.2:
                defects.append(Defect(
                    "overlap",
                    f"文字「{_short(_item_text(first))}」和「{_short(_item_text(second))}」重叠了。",
                    _item_text(first),
                ))

    for item in measured.items:
        if item["kind"] in ("chart", "table") and (
            item["x"] < -TOLERANCE or item["y"] < -TOLERANCE
            or item["x"] + item["w"] > CANVAS_WIDTH + TOLERANCE
            or item["y"] + item["h"] > CANVAS_HEIGHT + TOLERANCE
        ):
            name = "图表" if item["kind"] == "chart" else "表格"
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

    if require_fill:
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
    return list(dict.fromkeys(defects))
