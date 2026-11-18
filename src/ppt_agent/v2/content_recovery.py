"""Why a page's content failed its number check, and what recovers it (P4-Fix6).

Three attempts that keep failing the number check used to end in a placeholder: the unsupported
figures stripped, the title replaced by the section name. In the CNNIC 57 run four of five such
pages failed on figures that are real - printed verbatim in the report, only on pages outside the
page's evidence packet (a chart's bar values, a fact the deck plan showed it from another page) - and
one on a figure the model added up itself. Each rejected figure is now classed:

- CITATION_MISMATCH: printed verbatim in the source, on a page the packet did not hold
- CHART_LABEL_ONLY: printed only as a bare chart label, its unit or category not in the text
- OFF_TOPIC: printed elsewhere, but the page was moved off the topic it belongs to
- COMPUTED: not in the source, but a sum or difference of two figures that are
- UNSUPPORTED: in the source nowhere

and the page is written once more with that diagnosis (the pages holding mismatched citations are added
to its evidence; nothing is relaxed in the check). If it fails again it is reported as needing review.
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Any

STATUSES = ("CONTENT_VALID", "CONTENT_RECOVERED", "NEEDS_CONTENT_REVIEW")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("％", "%")


def rejected_values(record: dict[str, Any]) -> list[str]:
    """Figures the number check rejected at the last failed attempt."""

    for failure in reversed((record.get("numeric_check") or {}).get("failures") or []):
        values = [str(issue.get("value") or "") for issue in failure.get("issues") or [] if issue.get("value")]
        if values:
            return list(dict.fromkeys(values))
    return []


def locate(value: str, store: Any) -> list[tuple[str, int]]:
    """(document, page) where the figure is printed, as written (unit included when it has one)."""

    wanted = _squash(value)
    number = re.match(r"[约超近]?(\d+(?:\.\d+)?)", wanted)
    if not number:
        return []
    found = []
    for document in store.documents:
        if document.get("page_kind") == "web":
            continue
        for page in document["pages"]:
            text = _squash(page["text"])
            unit = wanted[number.end():]
            if wanted in text or (not unit and len(number.group(1)) >= 3
                                  and re.search(rf"(?<![\d.]){re.escape(number.group(1))}(?![\d])", text)):
                found.append((document["name"], page["page"]))
    return found


def _glued(value: str) -> str | None:
    """The bare decimal of a figure ("37.3亿" -> "37.3"): chart labels are extracted run together
    ("94.337.321.111.0"), so only a decimal with at least three digits is looked for inside such a run."""

    match = re.match(r"[约超近]?(\d+\.\d+)", _squash(value))
    return match.group(1) if match and len(match.group(1)) >= 4 else None


def locate_together(values: list[str], store: Any) -> dict[str, list[tuple[str, int]]]:
    """Pages where at least two of the figures (or the only one) are printed inside a run of chart labels.

    One decimal found inside another number may be chance; the same page carrying the page's other
    rejected figures in the same run is not."""

    bare = {value: _glued(value) for value in values}
    bare = {value: number for value, number in bare.items() if number}
    needed = 2 if len(values) > 1 else 1
    found: dict[str, list[tuple[str, int]]] = {}
    for document in store.documents:
        if document.get("page_kind") == "web":
            continue
        for page in document["pages"]:
            text = _squash(page["text"])
            hits = [value for value, number in bare.items() if number in text]
            if len(hits) >= needed:
                for value in hits:
                    found.setdefault(value, []).append((document["name"], page["page"]))
    return found


def computed(value: str, evidence: str) -> bool:
    """Whether a figure is the sum or difference of two figures of the evidence."""

    match = re.match(r"[约超近]?(\d+(?:\.\d+)?)", _squash(value))
    if not match:
        return False
    target = float(match.group(1))
    numbers = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", evidence or "") if 0 < float(n) < 1e7}
    for a, b in itertools.combinations(numbers, 2):
        if abs(a + b - target) < 1e-6 or abs(abs(a - b) - target) < 1e-6:
            return True
    return False


def diagnose(record: dict[str, Any], store: Any, *, moved: bool = False) -> dict[str, Any]:
    """``moved``: the overlap review gave the page new evidence because its old topic was another page's.
    A figure printed elsewhere is then that old topic coming back (CNNIC 57 p16: moved off 外卖, written
    with 3748万 again), so it is dropped rather than brought back with its page."""

    evidence = (record.get("evidence") or {}).get("text", "")
    classes, places = {}, {}
    values = rejected_values(record)
    together = locate_together(values, store) if store is not None else {}
    for value in values:
        where = locate(value, store) if store is not None else []
        if where and moved:
            classes[value] = "OFF_TOPIC"
        elif where:
            classes[value], places[value] = "CITATION_MISMATCH", where[:3]
        elif together.get(value):
            # Printed as a bare chart label ("142.5" under "单位：万个", or with no category names at all):
            # true as a number, but its unit and category are not text a quote can carry.
            classes[value], places[value] = "CHART_LABEL_ONLY", together[value][:3]
        elif computed(value, evidence):
            classes[value] = "COMPUTED"
        else:
            classes[value] = "UNSUPPORTED"
    # Every diagnosed page is written once more with its diagnosis; only a citation mismatch adds evidence.
    recoverable = bool(classes)
    return {"values": classes, "where": {value: [list(place) for place in where] for value, where in places.items()},
            "recoverable": recoverable}


def pages_to_add(diagnosis: dict[str, Any]) -> dict[str, set[int]]:
    """The source pages whose text the page needs: the first place each rejected figure is printed."""

    # Each figure's page is the one printing the most of the page's rejected figures: "9.7%" is on three
    # pages, but only one carries it with the three figures it was quoted next to.
    counts: dict[tuple[str, int], int] = {}
    cited = {value: where for value, where in diagnosis["where"].items()
             if diagnosis["values"].get(value) == "CITATION_MISMATCH"}
    for where in cited.values():
        for document, page in where:
            counts[(document, int(page))] = counts.get((document, int(page)), 0) + 1
    wanted: dict[str, set[int]] = {}
    for where in cited.values():
        if where:
            document, page = max(((d, int(p)) for d, p in where), key=lambda place: (counts[place], -place[1]))
            wanted.setdefault(document, set()).add(page)
    return wanted


def instruction(diagnosis: dict[str, Any]) -> str:
    by_kind: dict[str, list[str]] = {}
    for value, kind in diagnosis["values"].items():
        by_kind.setdefault(kind, []).append(value)
    lines = ["上一版有数字没有通过核对，原因已经查清，按下面逐条处理后重写这一页："]
    if by_kind.get("CITATION_MISMATCH"):
        lines.append("· " + "、".join(by_kind["CITATION_MISMATCH"]) + "：原文里有，但不在本页原来的证据里。证据里已经加上它们所在的原文页；照原文写，出处写那一页。")
    if by_kind.get("CHART_LABEL_ONLY"):
        lines.append("· " + "、".join(by_kind["CHART_LABEL_ONLY"]) + "：只是原文图表上的裸数值，单位或类别名称不在文字里，不能当作文字事实。不要写进标题、导语或条目；只用原文句子里写明的数字。")
    if by_kind.get("OFF_TOPIC"):
        lines.append("· " + "、".join(by_kind["OFF_TOPIC"]) + "：属于别页已经讲过的旧话题，本页已改讲现在证据里的内容。删掉它们，只用现在证据里写明的数字。")
    if by_kind.get("COMPUTED"):
        lines.append("· " + "、".join(by_kind["COMPUTED"]) + "：是你自己算出的合计或差值，原文没有。不要自己计算，分别写原文给出的各个数字。")
    if by_kind.get("UNSUPPORTED"):
        lines.append("· " + "、".join(by_kind["UNSUPPORTED"]) + "：原文里找不到，删掉，不要换成别的数字。")
    lines.append("论点不变；如果去掉这些数字后论点撑不住，改用证据里确实写明的数字支撑同一个论点。"
                 if "OFF_TOPIC" not in by_kind else "观点、焦点数字和条目都来自现在的证据，不要再讲原来的主题。")
    return "\n".join(lines)


def title_for(content_plan_entry: dict[str, Any] | None, fallback: str) -> str:
    """A placeholder never carries the section name as its claim: the plan's claim if it has one."""

    claim = ((content_plan_entry or {}).get("claim") or "").strip()
    return claim[:40] if claim and not re.search(r"\d", claim) else fallback


def summary(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    states = {number: record.get("content_status", "CONTENT_VALID") for number, record in records.items()}
    return {"by_status": {status: sorted(n for n, s in states.items() if s == status) for status in STATUSES},
            "failures": {str(n): records[n].get("content_failure") for n in sorted(records) if records[n].get("content_failure")}}


def dumps(diagnosis: dict[str, Any]) -> str:
    return json.dumps(diagnosis, ensure_ascii=False)
