"""A diagram has to mean what it shows.

A bar's length, a column's height and the number of lit squares are read as
quantities whether or not the page meant them as such. These checks read the
page's HTML (not its measured layout) and compare every drawn quantity with the
verified content: a growth rate drawn as a share of a whole, or columns of
invented heights, are defects however well the page fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from ppt_agent.v2.htmldeck.checks import Defect

# Words that make a percentage a change over time rather than a part of a whole.
_CHANGE = re.compile(r"同比|环比|增长|增速|增加|提升|提高|上升|下降|降低|减少|减轻|缩短|增强|翻倍|翻番|降幅|增幅")
_SHARE = re.compile(r"占|比例|份额|比重")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
_VOID = {"br", "hr", "img", "input", "meta", "link"}
QUALITATIVE_TEXT = 8   # a column that is a text container, not a bar, says at least this much
HEIGHT_TOLERANCE = 8.0  # percentage points between a column's height and its value's share


def shares(content: dict[str, Any]) -> set[float]:
    """Percentages in the content that are parts of a whole, so may be drawn as one.

    "占全行业融资比例 12.1%" and "准确率超 90%" are; "同比增长 21.5%" is not."""

    found: set[float] = set()

    def consider(value: Any, context: str) -> None:
        for match in _PERCENT.finditer(str(value or "")):
            number = float(match.group(1))
            if number > 100:
                continue
            if _SHARE.search(context) or not _CHANGE.search(context):
                found.add(number)

    highlight = content.get("highlight") or {}
    if isinstance(highlight, dict):
        consider(highlight.get("value"), str(highlight.get("text") or ""))
    for item in content.get("items") or content.get("metrics") or []:
        if isinstance(item, dict):
            consider(item.get("value"), f"{item.get('heading') or item.get('label') or ''}{item.get('body') or item.get('note') or ''}")
    for series in content.get("series") or []:
        if isinstance(series, dict) and series.get("kind") == "share":
            found.update(float(point["value"]) for point in series.get("points") or [] if 0 <= point["value"] <= 100)
    return found


def _percentages(content: Any) -> set[float]:
    found: set[float] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            found.update(float(match.group(1)) for match in _PERCENT.finditer(value))

    walk(content)
    return found


@dataclass
class _Node:
    classes: list[str]
    style: str
    attrs: dict[str, str]
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    own_text: str = ""

    @property
    def text(self) -> str:
        return self.own_text + "".join(child.text for child in self.children)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def has(self, name: str) -> bool:
        return name in self.classes


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node([], "", {})
        self._open = self.root

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        values = {name: value or "" for name, value in attrs}
        node = _Node(values.get("class", "").split(), values.get("style", ""), values, parent=self._open)
        self._open.children.append(node)
        self._open = node

    def handle_endtag(self, tag):
        if tag not in _VOID and self._open.parent is not None:
            self._open = self._open.parent

    def handle_data(self, data):
        self._open.own_text += data


def _style(node: _Node, name: str) -> float | None:
    match = re.search(rf"(?:^|;)\s*{name}\s*:\s*(\d+(?:\.\d+)?)%", node.style)
    return float(match.group(1)) if match else None


def _same(a: float, b: float, tolerance: float = 0.06) -> bool:
    return abs(a - b) <= tolerance


def _cell(node: _Node) -> tuple[int, int, int, int] | None:
    """(first column, last column line, first row, last row line) of a grid region."""

    column = re.search(r"grid-column\s*:\s*(\d+)\s*/\s*(\d+)", node.style)
    row = re.search(r"grid-row\s*:\s*(\d+)\s*/\s*(\d+)", node.style)
    if not column or not row:
        return None
    return int(column.group(1)), int(column.group(2)), int(row.group(1)), int(row.group(2))


def _label(node: _Node) -> str:
    return re.sub(r"\s+", " ", node.text).strip()[:12]


def find(html: str, content: dict[str, Any]) -> list[Defect]:
    tree = _Tree()
    tree.feed(html or "")
    defects: list[Defect] = []

    # Two regions that claim the same cells are laid on top of each other.
    page = next((node for node in tree.root.walk() if node.has("page")), None)
    regions = [(node, _cell(node)) for node in (page.children if page else []) if node.has("r") and _cell(node)]
    for index, (first, a) in enumerate(regions):
        for second, b in regions[index + 1:]:
            if a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]:
                defects.append(Defect(
                    "unsupported",
                    f"以「{_label(first)}」开头的区域（列 {a[0]}/{a[1]}、行 {a[2]}/{a[3]}）和以「{_label(second)}」开头的区域"
                    f"（列 {b[0]}/{b[1]}、行 {b[2]}/{b[3]}）占了同一块格子，内容会叠在一起。请改行号让它们上下错开。"))
    allowed, known = shares(content), _percentages(content)

    for node in tree.root.walk():
        # A region inside a region has no cell of its own; its grid placement is ignored.
        if node.has("r") and re.search(r"grid-(?:column|row)", node.style):
            ancestor = node.parent
            while ancestor is not None and not ancestor.has("r"):
                ancestor = ancestor.parent
            if ancestor is not None:
                label = re.sub(r"\s+", " ", node.text).strip()[:14]
                defects.append(Defect(
                    "unsupported",
                    f"以「{label}」开头的区域写在了另一个区域里面。区域不能嵌套：请把它写成 page 下面并列的区域，"
                    "并让两个区域的行号不重叠。"))

        if node.has("strip"):
            for fill in (child for child in node.children if child.has("fill")):
                width = _style(fill, "width")
                if width is None:
                    defects.append(Defect("unbound_graphic", "比例条的填色段没有写宽度；宽度必须等于内容里的一个占比。"))
                elif not any(_same(width, share) for share in allowed):
                    if any(_same(width, value) for value in known):
                        defects.append(Defect(
                            "unbound_graphic",
                            f"{width:g}% 是增长或变化的幅度，不是整体里的一部分，不能画成「全长 = 100%」的比例条。"
                            "请改用大数字加文字，或换一种不含比例条的排法。"))
                    else:
                        defects.append(Defect(
                            "unbound_graphic",
                            f"比例条画了 {width:g}%，但内容里没有这个占比。条的宽度必须等于内容里的真实占比，否则请删掉比例条。"))

        if node.has("units"):
            lit = sum(1 for child in node.children if child.has("u") and child.has("on"))
            cells = sum(1 for child in node.children if child.has("u"))
            if cells != 10 or not any(round(share / 10) == lit for share in allowed):
                defects.append(Defect(
                    "unbound_graphic",
                    f"方块阵点亮了 {lit}/{cells} 个，对不上内容里的任何占比。必须是 10 个方块，点亮个数 = 占比 ÷ 10（四舍五入）；"
                    "没有占比就不要用方块阵。"))

        if node.has("cols"):
            columns = [child for child in node.children if child.has("col")]
            heights = [_style(column, "height") for column in columns]
            levels = {round(height) for height in heights if height is not None}
            thin = [column for column in columns if len(re.sub(r"\s+", "", column.text)) < QUALITATIVE_TEXT]
            if thin:
                defects.append(Defect(
                    "unbound_graphic",
                    f"柱子「{re.sub(chr(92) + 's+', '', thin[0].text)[:8]}」里几乎没有内容，看起来像一张没有数据的柱状图。"
                    "柱子的高度没有数据依据时，请改成并列的文字，不要画柱子。"))
            elif len(levels) > 2:
                # Three or more heights read as measured values: each must be one.
                values = []
                for column in columns:
                    match = re.search(r"\d+(?:\.\d+)?", column.text)
                    values.append(float(match.group()) if match else None)
                top = max((value for value in values if value), default=0)
                if None in values or None in heights or not top or any(
                        abs(height - value / top * 100) > HEIGHT_TOLERANCE for height, value in zip(heights, values)):
                    defects.append(Defect(
                        "unbound_graphic",
                        "柱子有三种以上的高度，读者会把它当成数值来比较，但高度和柱子里的数字不成比例。"
                        "请让高度 = 数值 ÷ 最大值，或只用高、低两档表示快慢。"))
    return list(dict.fromkeys(defects))
