"""Why a page's content failed its number check, and what recovers it (P4-Fix6).

Three attempts that keep failing the number check used to end in a placeholder: the unsupported
figures stripped, the title replaced by the section name. In the CNNIC 57 run four of five such
pages failed on figures that are real - printed verbatim in the report, only on pages outside the
page's evidence packet (a chart's bar values, a fact the deck plan showed it from another page) - and
one on a figure the model added up itself. Each rejected figure is now classed:

- CITATION_MISMATCH: printed verbatim in the source, on a page the packet did not hold
- COMPUTED: not in the source, but a sum or difference of two figures that are
- UNSUPPORTED: in the source nowhere

and the page is recovered only when every rejected figure is in the source: those pages' text is
added to its evidence and it is written once more. Otherwise it is reported as needing review.
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
            if wanted in text or (len(number.group(1)) >= 3 and re.search(rf"(?<![\d.]){re.escape(number.group(1))}(?![\d])", text)):
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


def diagnose(record: dict[str, Any], store: Any) -> dict[str, Any]:
    evidence = (record.get("evidence") or {}).get("text", "")
    classes, places = {}, {}
    values = rejected_values(record)
    together = locate_together(values, store) if store is not None else {}
    for value in values:
        where = (locate(value, store) if store is not None else []) or together.get(value, [])
        if where:
            classes[value], places[value] = "CITATION_MISMATCH", where[:3]
        elif computed(value, evidence):
            classes[value] = "COMPUTED"
        else:
            classes[value] = "UNSUPPORTED"
    recoverable = bool(classes) and all(kind == "CITATION_MISMATCH" for kind in classes.values())
    return {"values": classes, "where": {value: [list(place) for place in where] for value, where in places.items()},
            "recoverable": recoverable}


def pages_to_add(diagnosis: dict[str, Any]) -> dict[str, set[int]]:
    """The source pages whose text the page needs: the first place each rejected figure is printed."""

    # Each figure's page is the one printing the most of the page's rejected figures: "9.7%" is on three
    # pages, but only one carries it with the three figures it was quoted next to.
    counts: dict[tuple[str, int], int] = {}
    for where in diagnosis["where"].values():
        for document, page in where:
            counts[(document, int(page))] = counts.get((document, int(page)), 0) + 1
    wanted: dict[str, set[int]] = {}
    for where in diagnosis["where"].values():
        if where:
            document, page = max(((d, int(p)) for d, p in where), key=lambda place: (counts[place], -place[1]))
            wanted.setdefault(document, set()).add(page)
    return wanted


def instruction(diagnosis: dict[str, Any]) -> str:
    values = "、".join(diagnosis["values"])
    return ("上一版用到的这些数字是真实的，但不在本页原来的证据里：" + values + "。现在证据里已经加上了它们所在的原文页。"
            "重新写这一页：这些数字照原文写，出处写它们所在的页；不要自己计算合计或差值。")


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
