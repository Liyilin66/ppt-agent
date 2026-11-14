"""Tables of the source PDF, read row by row and checked against the page.

A table is read from the page's text laid out as it is printed, where each row
of the table is a line. The model says which cell is which; the rules then
require every cell to stand on the page, each row's own cells to stand on one
line, the rows to follow the page's order, and a cell shared by several rows
(a merged cell) to stand among the lines of those rows. A table with a row
that fails is not used: a table missing a row is a different table.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

MAX_ROWS, MAX_COLUMNS = 40, 8
_CAPTION = re.compile(r"^\s*(表\s*\d+)\s*(\S[^\n]{1,48})$", re.M)
_FIGURE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_ASIDE = re.compile(r"[（(][^）)]*[）)]$")  # 1000k（100万）: the parenthesis restates the value

TABLE_SYSTEM = (
    "下面是一页 PDF 按版面排出来的文字，里面有一张表。请把这张表逐行读出来。"
    "规则：单元格文字逐字照抄，不要改写、不要补全、不要合并两行；合并单元格（一个单元格管几行）在它管的每一行都重复写一遍；"
    "表头是表格的第一行；不属于表格的正文不要读进来。读不出规整的行列就返回空表。"
    '只输出 JSON：{"headers":["列名"],"rows":[["单元格"]]}'
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("％", "%")


def captions(path: str | Path) -> list[dict[str, Any]]:
    """Each captioned table of a PDF: its label, title, page and the page's laid-out text."""

    from pypdf import PdfReader

    found = []
    try:
        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001
        return found
    for number, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except Exception:  # noqa: BLE001
            continue
        for label, title in _CAPTION.findall(text):
            found.append({"label": re.sub(r"\s+", " ", re.sub(r"表\s*", "表 ", label)), "title": title.strip(),
                          "document": Path(path).name, "page": number, "text": text})
    return found


def verify(headers: list[Any], rows: list[Any], page_text: str) -> tuple[list[list[str]], list[dict[str, Any]]]:
    """The rows as they stand on the page, and every row that does not."""

    lines = [_squash(line) for line in page_text.splitlines()]
    lines = [line for line in lines if line]
    problems: list[dict[str, Any]] = []
    clean: list[list[str]] = []
    width = len(headers)
    if not 2 <= width <= MAX_COLUMNS or not 2 <= len(rows) <= MAX_ROWS:
        return [], [{"row": None, "why": "不是一张 2 到 8 列、2 到 40 行的表"}]
    head = next((index for index, line in enumerate(lines) if all(_squash(str(cell)) in line for cell in headers)), None)
    if head is None:
        return [], [{"row": None, "why": "表头不在页面的同一行上"}]
    at = head
    placed: list[tuple[int, list[str]]] = []
    for number, row in enumerate(rows, 1):
        cells = [str(cell).strip() for cell in row] if isinstance(row, list) else []
        if len(cells) != width or not all(cells):
            problems.append({"row": number, "why": "这一行的单元格数和表头不一致"})
            continue
        squashed = [_squash(cell) for cell in cells]
        # The row's line: after the previous row, holding its last cell and all but the shared ones.
        def explains(text: str) -> bool:
            """The line holds the row's last cell and most of the others, and nothing that is not the row's."""
            if squashed[-1] not in text or sum(cell in text for cell in squashed) < max(1, width - 1):
                return False
            for cell in sorted(squashed, key=len, reverse=True):
                text = text.replace(cell, "", 1)
            return not text

        line = next((index for index in range(at + 1, len(lines)) if explains(lines[index])), None)
        if line is None:
            problems.append({"row": number, "cells": cells, "why": "这一行的单元格不在页面的同一行上，或顺序和页面不一致"})
            continue
        at = line
        placed.append((line, cells))
    # A cell not on its row's own line is a merged cell: it must stand among the lines of the rows that share it.
    for index, (line, cells) in enumerate(placed):
        for column, cell in enumerate(cells):
            if _squash(cell) in lines[line]:
                continue
            group = [other_line for other_line, other in placed if other[column] == cell]
            low = placed[index - 1][0] + 1 if index and placed[index - 1][1][column] != cell else min(group)
            if not any(_squash(cell) in lines[candidate] for candidate in range(min(low, min(group)), max(group) + 1)):
                problems.append({"row": index + 1, "cells": cells, "why": f"「{cell}」不在它所管的那几行之间"})
    if not problems and placed:
        # Nothing between the heading and the last row may be left unread, and a table
        # that runs to the foot of the page may go on overleaf.
        every = sorted({_squash(cell) for _, cells in placed for cell in cells}, key=len, reverse=True)
        for index in range(head + 1, placed[-1][0]):
            rest = lines[index]
            for cell in every:
                rest = rest.replace(cell, "")
            if rest:
                problems.append({"row": None, "why": f"表格中间有一行没有读进来：{lines[index][:24]}"})
                break
        if all(re.fullmatch(r"\d{1,4}", line) for line in lines[placed[-1][0] + 1:]):
            problems.append({"row": None, "why": "表格排到了页末，可能续到下一页；不完整的表不使用"})
    if problems:
        return [], problems
    clean = [cells for _, cells in placed]
    return clean, []


async def read(client: Any, checkpoints: Any, table: dict[str, Any]) -> dict[str, Any]:
    """One table, read and verified: {"label","title","ref","headers","rows","problems"}."""

    name = "tables/" + hashlib.sha1((table["document"] + str(table["page"]) + table["label"]).encode()).hexdigest()[:12] + ".json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    result = {"label": table["label"], "title": table["title"], "document": table["document"], "page": table["page"],
              "ref": f"{table['document']} 第{table['page']}页", "headers": [], "rows": [], "problems": []}
    try:
        reply = await client.complete_json(
            task="source_table", system=TABLE_SYSTEM,
            user=f"要读的表：{table['label']} {table['title']}\n页面文字：\n{table['text']}",
            max_output_tokens=3000, context={"table": table["label"]})
    except Exception as error:  # noqa: BLE001
        result["problems"] = [{"row": None, "why": "读取失败：" + str(error)[:80]}]
        return result
    headers = [str(cell).strip() for cell in (reply.get("headers") or [])] if isinstance(reply, dict) else []
    rows, problems = verify(headers, (reply.get("rows") or []) if isinstance(reply, dict) else [], table["text"])
    result.update(headers=headers if rows else [], rows=rows, problems=problems, proposed_rows=len((reply or {}).get("rows") or []))
    if checkpoints is not None:
        checkpoints.save(name, result)
    return result


def _figure(cell: str) -> tuple[float, str] | None:
    text = _ASIDE.sub("", _squash(cell))
    match = _FIGURE.match(text)
    if not match:
        return None
    return float(match.group().replace(",", "")), text[match.end():]


def series_of(table: dict[str, Any]) -> list[dict[str, Any]]:
    """Each column of a verified table that is one measure in one unit, as a
    series across the table's rows. Nothing is computed and no row is left out."""

    headers, rows = table["headers"], table["rows"]
    if not rows:
        return []
    numeric = {column for column in range(len(headers))
               if all(_figure(row[column]) for row in rows) and len({_figure(row[column])[1] for row in rows}) == 1}
    # The rows are named by the left-most column whose cells are all different.
    names = next((column for column in range(len(headers))
                  if column not in numeric and len({row[column] for row in rows}) == len(rows)), None)
    if names is None:
        return []
    made = []
    for column in sorted(numeric):
        unit = _figure(rows[0][column])[1]
        made.append({
            "metric": f"{headers[column]}（{table['label']} {table['title']}）"[:36], "unit": unit[:10], "kind": "category",
            "scope": headers[names][:30], "period": None,
            "points": [{"label": row[names][:20], "value": _figure(row[column])[0], "display": _squash(row[column])[:16],
                        "quote": " ".join(row)[:160], "ref": table["ref"], "projected": False} for row in rows]})
    return made
