"""Pair the labels and values printed inside a PDF figure by where they stand on the page.

A chart's text layer comes out as two runs - every value, then every label
(``72.2%62.0%16.5% … 豆包DeepSeek腾讯元宝 …``) - so no quote of the text pairs one with the
other, and the series check rightly rejects every point. Their positions do pair them: in a
bar chart each label sits on the row (or under the column) of its value.

A pairing is kept only when it is unambiguous: every label has exactly one value on its row
or column and no value is claimed by two labels, the labels stand in one line, and there are
at least three pairs. Anything less is left out, and the figure stays unread. The pairs are
added to the evidence as their own block (``[file | 图中坐标配对 pN]``), one ``label value``
per line, and then go through the same checks as any quote.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

VALUE = re.compile(r"^[约超近]?\d+(?:\.\d+)?(?:%|％|亿|万|倍|个百分点)$|^\d+\.\d+$")  # a bare integer is a footnote or a page
LABEL = re.compile(r"^[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9、，·（）()\-\s]{0,17}$")
NOT_LABEL = re.compile(r"^图|^表|来源|资料|单位|注[:：]|[。；：]|^其中")
ROW = 4.0          # points: a value on a label's row
COLUMN = 10.0      # points: a value over a label's column
LINE = 6.0         # points: labels that stand in one line
MIN_PAIRS = 3
KIND = "图中坐标配对"


def _width(text: str, size: float) -> float:
    return sum(size if "一" <= char <= "鿿" else 0.55 * size for char in text)


def words(page) -> list[dict[str, Any]]:
    """Text fragments of a pypdf page with their position; a fragment pypdf gives no position
    for continues the one before it."""

    found: list[dict[str, Any]] = []

    def visit(text, cm, tm, font, size):
        text = text.strip()
        if not text:
            return
        x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        scale = abs(cm[0] * tm[0]) or 1.0
        size = (size or 10) * scale
        if x == 0 and y == 0:
            if found:
                found[-1]["after"] = True  # the next fragment has no place of its own
            return
        found.append({"text": text, "x": x, "y": y, "size": size, "w": _width(text, size)})

    page.extract_text(visitor_text=visit)
    kept = [word for word in found if not word.get("after")]
    # One label drawn as two runs on one baseline ("生成会议纪要、" + "PPT") is one label.
    merged: list[dict[str, Any]] = []
    for word in sorted(kept, key=lambda word: (-round(word["y"]), word["x"])):
        last = merged[-1] if merged else None
        if (last is not None and abs(last["y"] - word["y"]) < 1 and not VALUE.match(word["text"])
                and not VALUE.match(last["text"]) and 0 <= word["x"] - (last["x"] + last["w"]) < last["size"]):
            last["text"] += word["text"]
            last["w"] = word["x"] + word["w"] - last["x"]
            continue
        merged.append(dict(word))
    return merged


def _ticks(values: list[dict[str, Any]]) -> set[int]:
    """Axis ticks: three or more round values on one row or column, evenly spaced."""

    found = set()
    for axis in ("x", "y"):
        other = "y" if axis == "x" else "x"
        groups: dict[int, list[int]] = {}
        for index, word in enumerate(values):
            groups.setdefault(round(word[other] / LINE), []).append(index)
        for members in groups.values():
            numbers = [float(re.sub(r"[^\d.]", "", values[i]["text"]) or 0) for i in members]
            if len(members) >= 3 and all(number == int(number) for number in numbers):
                found.update(members)
    return found


def pairs(page) -> list[tuple[str, str]]:
    """(label, value) pairs of one figure on a page, or [] when they cannot be told for certain."""

    fragments = words(page)
    values = [word for word in fragments if VALUE.match(word["text"])]
    ticks = _ticks(values)
    values = [word for index, word in enumerate(values) if index not in ticks]
    labels = [word for word in fragments if LABEL.match(word["text"]) and not NOT_LABEL.search(word["text"])
              and not VALUE.match(word["text"])]
    best: list[tuple[str, str]] = []
    for mode in ("row", "column"):
        matched: dict[int, int] = {}
        claimed: dict[int, int] = {}
        clash = False
        for li, label in enumerate(labels):
            if mode == "row":
                near = [vi for vi, value in enumerate(values)
                        if abs(value["y"] - label["y"]) <= ROW and value["x"] > label["x"] + label["w"] - 1]
            else:
                centre = label["x"] + label["w"] / 2
                near = [vi for vi, value in enumerate(values)
                        if abs(value["x"] + value["w"] / 2 - centre) <= COLUMN and value["y"] > label["y"]]
            if not near:
                continue
            if mode == "row":
                near.sort(key=lambda vi: values[vi]["x"])
                # Two values on one label's row: which is its own cannot be told.
                if len(near) > 1:
                    clash = True
                    continue
            else:
                near.sort(key=lambda vi: values[vi]["y"])
                if len(near) > 1:
                    clash = True
                    continue
            vi = near[0]
            if vi in claimed:
                clash = True
                continue
            matched[li], claimed[vi] = vi, li
        if clash or len(matched) < MIN_PAIRS:
            continue
        # The figure's own labels stand in one line (left- or right-aligned in a bar chart's
        # axis, on one baseline under columns); a stray match elsewhere on the page does not.
        if mode == "row":
            edges = [lambda word: word["x"], lambda word: word["x"] + word["w"]]
        else:
            edges = [lambda word: word["y"]]
        axis: list[int] = []
        for edge in edges:
            for li in matched:
                group = [other for other in matched if abs(edge(labels[other]) - edge(labels[li])) <= LINE]
                if len(group) > len(axis):
                    axis = group
        if len(axis) < MIN_PAIRS:
            continue
        matched = {li: matched[li] for li in axis}
        # All of the figure or none of it: a value between the first and last label, on the
        # values' side, that no label claimed means a label was not read - the series would be partial.
        spot = [labels[li] for li in matched]
        if mode == "row":
            ys = sorted(w["y"] for w in spot)
            pitch = sorted(b - a for a, b in zip(ys, ys[1:]))[len(ys) // 2 - 1] if len(ys) > 1 else 0
            # One row beyond the first and the last label: a bar whose label was not read sits there.
            low, high = ys[0] - max(ROW, 1.2 * pitch), ys[-1] + max(ROW, 1.2 * pitch)
            inner = min(w["x"] for w in spot)
            strays = [vi for vi, value in enumerate(values) if vi not in matched.values()
                      and low <= value["y"] <= high and value["x"] > inner]
        else:
            low, high = min(w["x"] for w in spot) - COLUMN, max(w["x"] + w["w"] for w in spot) + COLUMN
            base = max(w["y"] for w in spot)
            strays = [vi for vi, value in enumerate(values) if vi not in matched.values()
                      and low <= value["x"] <= high and base < value["y"] <= base + 300]
        if strays:
            continue
        ordered = sorted(matched.items(), key=lambda item: (-labels[item[0]]["y"], labels[item[0]]["x"]))
        found = [(labels[li]["text"], values[vi]["text"]) for li, vi in ordered]
        if len(found) > len(best):
            best = found
    return best


def blocks(path: str | Path, page_numbers: list[int]) -> str:
    """Evidence blocks of coordinate-paired figure text for the given physical pages."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    name = Path(path).name
    out = []
    for number in sorted(set(page_numbers)):
        if not 1 <= number <= len(reader.pages):
            continue
        found = pairs(reader.pages[number - 1])
        if found:
            out.append(f"[{name} | {KIND} p{number}]\n" + "\n".join(f"{label} {value}" for label, value in found))
    return "\n".join(out)
