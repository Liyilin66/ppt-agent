"""What kind of quantity a figure is, and which connections between figures are true.

Two figures can both be in the source and the line between them still be false: an arrow
from a half-year increase to a total (2.66亿 → 5.15亿), from a percentage-point rise to a
rate (18.8个百分点 → 36.5%), "翻倍" between values that are not double. Each figure is
classed by the words the source puts around it:

- ``total``: a level or stock (规模 5.15亿人, 6233亿美元)
- ``increment``: a change in amount (新增 2.66亿人, 增加 8 家)
- ``share``: a part of a whole or a rate (占 38.2%, 普及率 36.5%, 使用率 26.1%)
- ``growth``: a rate of change (同比增长 21.5%, 增速 12%)
- ``points``: a difference of two rates (提升 18.8 个百分点)
- ``multiple``: 2 倍, 翻倍

and a connection is allowed only between figures of one class, one unit and, for a change,
the right direction and size.
"""

from __future__ import annotations

import re
from typing import Any

FIGURE = re.compile(r"(?P<n>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<u>个百分点|%|％|亿元|亿美元|万亿|亿|万|倍|元|美元|人|家|个|项|款|笔|起)?")
_POINTS = re.compile(r"个百分点")
_GROWTH = re.compile(r"同比|环比|增速|增长率|年均|复合|CAGR|增幅|降幅|(?:增长|上升|下降|提高|提升|降低|减少)了?\s*$")
_SHARE = re.compile(r"占|比例|份额|比重|率|渗透|覆盖|使用|首选|用户中|的用户|的网民")
_INCREMENT = re.compile(r"新增|增加了?|增长了?|多了|减少了?|较.{0,16}(?:增|减|多|少|提升|下降)|净增|增量")
_CAUSE = re.compile(r"导致|带动|推动|驱动|得益于|由于|因此|从而|使得|促使|拉动|源于|归因")
_ARROW = re.compile(r"→|➜|⟶|->")
_DOUBLE = re.compile(r"翻(?:一)?(?:倍|番)")
_TIMES = re.compile(r"(\d+(?:\.\d+)?)\s*倍")
CONTEXT = 18  # characters before a figure that say what it is
NEARBY = 40   # characters around a figure in which the source puts the value it is compared with


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("％", "%")


def classify(display: str, evidence: str) -> str | None:
    """The class of a figure, from its unit and the source words just before it; ``None`` when
    the figure is not in the evidence."""

    match = FIGURE.search(_squash(display))
    if match is None:
        return None
    unit = match.group("u") or ""
    if _POINTS.search(unit):
        return "points"
    if unit == "倍":
        return "multiple"
    text = _squash(evidence)
    number = match.group("n")
    classes = []
    for found in re.finditer(re.escape(number) + re.escape(unit.replace("％", "%")), text):
        before = text[max(0, found.start() - CONTEXT): found.start()]
        after = text[found.end(): found.end() + 2]
        if after.startswith("个百分点"):
            classes.append("points")
        elif unit in ("%", "％"):
            classes.append("growth" if _GROWTH.search(before) and not re.search(r"占|比例|份额", before[-6:])
                           else "share")
        else:
            classes.append("increment" if _INCREMENT.search(before[-10:]) else "total")
    if not classes:
        return None
    # A figure named in several places takes the class most of them give it.
    return max(set(classes), key=classes.count)


def _figures(text: str) -> list[str]:
    return [match.group(0) for match in FIGURE.finditer(_squash(text)) if match.group("u")]


def _value(display: str) -> float | None:
    match = FIGURE.search(_squash(display))
    return float(match.group("n").replace(",", "")) if match else None


def _unit(display: str) -> str:
    match = FIGURE.search(_squash(display))
    unit = (match.group("u") or "") if match else ""
    return "%" if unit == "％" else unit


def check_link(first: str, second: str, evidence: str) -> str | None:
    """Why an arrow from ``first`` to ``second`` is false, or ``None``."""

    a, b = classify(first, evidence), classify(second, evidence)
    if a is None or b is None:
        return f"「{first}」或「{second}」在原文里找不到，不能用箭头把它们连起来"
    if a != b:
        names = {"total": "总量", "increment": "增量", "share": "占比或比率", "growth": "增长率", "points": "百分点差",
                 "multiple": "倍数"}
        return (f"「{first}」是{names[a]}，「{second}」是{names[b]}，不是同一种量；箭头会读成前者变成了后者。"
                "改成两个并列的数字，各自写清楚它是什么")
    if _unit(first) != _unit(second):
        return f"「{first}」和「{second}」单位不同，不能用箭头连起来"
    return None


def check_text(text: str, evidence: str, *, where: str) -> list[str]:
    """Problems with the connections a sentence draws between figures."""

    problems = []
    squashed = _squash(text)
    for match in _ARROW.finditer(squashed):
        left = _figures(squashed[max(0, match.start() - 16): match.start()])
        right = _figures(squashed[match.end(): match.end() + 16])
        if left and right:
            why = check_link(left[-1], right[0], evidence)
            if why:
                problems.append(f"{where}：{why}")
    figures = [figure for figure in _figures(squashed) if not figure.endswith("倍")]
    stated = _squash(evidence)

    def comparable() -> list[float]:
        # The page's own levels, and those the source prints right beside them (the other end of
        # "17.7% → 36.5%" may be in the source and not on the page).
        found = []
        for figure in figures:
            kind = classify(figure, evidence)
            if kind not in ("total", "share"):
                continue
            found.append(_value(figure))
            for match in re.finditer(re.escape(_squash(figure)), stated):
                nearby = stated[max(0, match.start() - NEARBY): match.end() + NEARBY]
                found += [_value(other) for other in _figures(nearby)
                          if _unit(other) == _unit(figure) and classify(other, evidence) == kind]
        return [value for value in found if value]

    if _DOUBLE.search(squashed) and not _DOUBLE.search(stated):
        values = comparable()
        doubled = any(0 < low and 1.8 <= high / low <= 2.3 for low in values for high in values if high > low)
        if not doubled:
            problems.append(f"{where}：说「翻倍」，但原文没有这样说，页面上也没有两个相差约一倍的同类数字")
    for match in _TIMES.finditer(squashed):
        claimed = float(match.group(1))
        if match.group(0) in stated:
            continue
        values = comparable()
        if not any(0 < low and abs(high / low - claimed) <= 0.15 * claimed for low in values for high in values
                   if high > low):
            problems.append(f"{where}：「{match.group(0)}」原文没有写，页面上的数字也算不出这个倍数")
    if _CAUSE.search(squashed) and figures and not _CAUSE.search(stated):
        problems.append(f"{where}：「{_CAUSE.search(squashed).group(0)}」说的是因果，但原文这几页没有讲因果关系；"
                        "改成并列陈述，或只写原文给出的关系")
    return problems


def check_content(content: dict[str, Any], evidence: str) -> list[str]:
    """Connections the page's words draw that the source does not support."""

    problems = []
    places = [("标题", content.get("title")), ("导语", content.get("lead"))]
    highlight = content.get("highlight") or {}
    if isinstance(highlight, dict):
        places.append(("焦点", f"{highlight.get('value') or ''}{highlight.get('text') or ''}"))
    for index, item in enumerate(content.get("items") or [], 1):
        if isinstance(item, dict):
            places.append((f"第 {index} 条", f"{item.get('heading') or ''} {item.get('value') or ''} {item.get('body') or ''}"))
    for where, text in places:
        if text:
            problems += check_text(str(text), evidence, where=where)
    return problems


def series_classes(points: list[dict[str, Any]]) -> set[str]:
    """Classes of a series' points, each read from its own quote."""

    found = set()
    for point in points:
        kind = classify(str(point.get("display") or ""), str(point.get("quote") or ""))
        if kind is not None:
            found.add(kind)
    return found
