"""A diagram has to mean what it shows.

A bar's length, a column's height and the number of lit squares are read as
quantities whether or not the page meant them as such. These checks read the
page's HTML (not its measured layout) and compare every drawn quantity with the
verified content: a growth rate drawn as a share of a whole, or columns of
invented heights, are defects however well the page fits.
"""

from __future__ import annotations

import json
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
UNITS_TOLERANCE = 2.5   # a grid of ten shows tenths: a share further than this from one is misdrawn by it


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


def _numbers(text: str) -> set[float]:
    return {float(match.replace(",", "")) for match in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text or "")}


def _plain(text: Any) -> str:
    return re.sub(r"[\s、，,。（）()·：:\-—]", "", str(text or ""))


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
    page_values = _numbers(" ".join(
        [str((content.get("highlight") or {}).get("value") or "")]
        + [str(item.get("value") or "") for item in content.get("items") or [] if isinstance(item, dict)]
        + [str(point.get("display") or "") for series in content.get("series") or [] for point in series.get("points") or []]
        + [str(cell) for row in content.get("rows") or [] for cell in row]
        + [str(metric.get("value") or "") for metric in content.get("metrics") or [] if isinstance(metric, dict)]
        + [str(content.get("title") or "")]))
    unit_values = [re.sub(r"\s+", "", str(value)) for value in
                   [(content.get("highlight") or {}).get("value")] + [item.get("value") for item in content.get("items") or []
                                                                     if isinstance(item, dict)]
                   + [point.get("display") for series in content.get("series") or [] for point in series.get("points") or []]
                   if value]
    # The source's own name for a category goes with its value wherever the value is written out.
    for series in content.get("series") or []:
        if series.get("kind") == "time":
            continue  # a date need not stand beside each value
        for point in series.get("points") or []:
            display = re.sub(r"\s+", "", str(point.get("display") or ""))
            label = _plain(re.sub(r"[（(][^）)]*[）)]", "", str(point.get("label") or "")))  # "GPT-3（2020）" is GPT-3
            if not display or not label:
                continue
            for region in (node for node in tree.root.walk() if node.has("r")):
                text = re.sub(r"\s+", "", region.text)
                standalone = any(re.sub(r"\s+", "", node.own_text) == display for node in region.walk())
                if standalone and not any(child.has("chart") for child in region.walk()) and label not in _plain(text):
                    defects.append(Defect(
                        "unsupported",
                        f"{display} 在原文里对应的类别是「{point.get('label')}」，写它的地方要用原文的类别名，不要改名或概括。"))
    from ppt_agent.v2.htmldeck import charts

    drawable = [series for series in content.get("series") or [] if charts.kind_for(series)]

    for node in tree.root.walk():
        # An arrow between two figures says the first became the second. That is true only of two
        # values of one measure; an increase pointing at a total (2.66亿 → 5.15亿) says something false.
        if node.has("rowx"):
            big = [child for child in node.children if {"d1", "d2", "d3", "d4"} & set(child.classes)]
            arrow = any("→" in child.text and child not in big for child in node.children)
            values = [float(match.group(1)) for child in big
                      for match in [re.search(r"(\d+(?:\.\d+)?)", child.text.replace(",", ""))] if match]
            if arrow and len(big) == 2 and len(values) == 2:
                together = any({point["value"] for point in series.get("points") or []} >= set(values)
                               for series in content.get("series") or [])
                if not together:
                    defects.append(Defect(
                        "unbound_graphic",
                        f"「{big[0].text.strip()} → {big[1].text.strip()}」的箭头表示前一个数变成了后一个数，但内容里没有核对过的序列说明"
                        "它们是同一个指标的前后取值。请去掉箭头，把两个数分开写，各自标明它衡量的是什么。"))
        if node.has("plate") and "data-figure" in node.attrs:
            figure = content.get("figure") or {}
            if node.attrs["data-figure"] != figure.get("id"):
                defects.append(Defect(
                    "unsupported", "页面引用了一张不属于这一页的图。只能使用给你的那一个 plate 元件；没有给就不要放图。"))
        # A chart says what a verified series says, value for value, or it is not drawn.
        # The same holds for an arrow written into any line of text the designer added.
        if not node.has("rowx") and "→" in node.own_text:
            line = re.sub(r"\s+", "", node.own_text)
            unit = r"(?:%|％|个百分点|亿|万|倍|元|美元|人|家|个|项|款|笔|起)"  # figures, not model names like GPT-3.5
            for match in re.finditer(r"(?<![A-Za-z\-.\d])(\d+(?:\.\d+)?)" + unit + r"[^\d→]{0,4}→[^\d]{0,4}(\d+(?:\.\d+)?)" + unit, line):
                pair = {float(match.group(1)), float(match.group(2))}
                together = any({point["value"] for point in series.get("points") or []} >= pair
                               for series in content.get("series") or [])
                written = match.group(0) in re.sub(r"\s+", "", json.dumps(content, ensure_ascii=False))
                if not together and not written:
                    defects.append(Defect(
                        "unbound_graphic",
                        f"「{match.group(0)}」用箭头把两个数连起来，但它们不是同一条核对过的序列里的两个值，"
                        "也不是内容里原有的写法；箭头会读成前者变成了后者。改成两个并列的数字，各自写清楚它是什么。"))

        # A display figure is read as the page's data; it must be one of the page's values - never a
        # page reference, a footnote number or a figure of the designer's own (CAICT p15: "第49–50页").
        if {"d1", "d2", "d3", "d4"} & set(node.classes) and re.search(r"\d", node.text):
            shown = re.sub(r"\s+", "", node.text)
            cited = re.search(r"第\s*\d+.*页|\bp\.?\s*\d+", shown)
            # (a page with no values of its own is left to the general check on unsupported numbers)
            if cited or (page_values and not _numbers(shown) & page_values):
                defects.append(Defect(
                    "unsupported",
                    f"大字「{shown[:16]}」不是这一页内容里的数据（焦点数字、条目数值或图表数据）。"
                    "大字只能放这些值；出处、页码和其他数字用小字写在出处或说明里。"))

        # A display figure's unit stands on its line, not in another region (CNNIC 57 p12: "3162" | "万个").
        if {"d1", "d2", "d3", "d4"} & set(node.classes):
            shown_text = re.sub(r"\s+", "", node.text)
            whole = [value for value in unit_values if value != shown_text and value.startswith(shown_text)
                     and re.search(r"\d", shown_text) and not value[len(shown_text)].isdigit()
                     and value[len(shown_text)] != "."]
            line_text = re.sub(r"\s+", "", node.parent.text if node.parent is not None else "")
            if whole and not any(value in line_text for value in whole):
                # "483.8万" in big type and its "个" elsewhere: part of the unit left the figure.
                defects.append(Defect(
                    "split_unit",
                    f"大字「{shown_text}」只写了一部分，完整的是「{whole[0]}」。剩下的单位用 unit 类紧跟在同一个 rowx 里。"))
            bare = re.fullmatch(r"[约超近]?(\d+(?:,\d{3})*(?:\.\d+)?)", shown_text)
            if bare:
                number = bare.group(1).replace(",", "")
                units = {match.group(2) for value in unit_values for match in [re.fullmatch(
                    r"[约超近]?(\d+(?:\.\d+)?)\s*([^\d\s].{0,5})", value)] if match and match.group(1) == number}
                line = re.sub(r"\s+", "", node.parent.text if node.parent is not None else "")
                if units and not any(number + unit in line.replace(",", "") for unit in units):
                    defects.append(Defect(
                        "split_unit",
                        f"大字「{bare.group(0)}」没有带单位（{'/'.join(sorted(units))}）。单位用 unit 类紧跟在大字后面、"
                        "放在同一个 rowx 里，不要放到别的区域或别的行。"))

        if node.has("chart") and "data-chart" in node.attrs:
            try:
                drawn = json.loads(node.attrs["data-chart"])
            except ValueError:
                drawn = None
            if not drawable:
                defects.append(Defect(
                    "bad_chart", "这一页没有核对过的数据序列，不能画图表。请删掉图表，用文字和数字表达。"))
            elif not isinstance(drawn, dict) or not any(charts.same(drawn, series) for series in drawable):
                defects.append(Defect(
                    "bad_chart",
                    "图表的数据被改动了。data-chart 必须原样使用给你的这一段，一个字符都不要改："
                    + charts.attr(drawable[0])))

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
            if cells != 10 or not any(round(share / 10) == lit and abs(lit * 10 - share) <= UNITS_TOLERANCE
                                      for share in allowed):
                defects.append(Defect(
                    "unbound_graphic",
                    f"方块阵点亮了 {lit}/{cells} 个，对不上内容里的任何占比。必须是 10 个方块，点亮个数 = 占比 ÷ 10，"
                    f"而且占比离整十不超过 {UNITS_TOLERANCE:g} 个百分点（96.8% 点亮 10 格会被读成 100%）；不满足就不要用方块阵。"))

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
