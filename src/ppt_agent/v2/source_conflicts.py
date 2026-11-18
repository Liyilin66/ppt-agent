"""Figures the source itself states two ways (P4 stage A).

The CNNIC 57 report says the netizen count grew "1717万人" in its key-data summary (p9) and
"17179万人" in the body (p19) - the same sentence, a figure ten times larger. The page quoted the
body; every check passed, because the figure is printed verbatim in the source. A figure printed
verbatim is only as good as the source's agreement with itself.

For each figure on a page, the clause that introduces it in the source ("截至2025年12月，我国网民
规模达11.25亿人，较2024年12月增长") is looked for elsewhere in the source followed by another figure
in the same unit. The context is the clause up to the figure (at most 24 characters: a section heading
runs into its first clause unpunctuated), so two measures that share only a phrase ("较2024年12月增长")
are not confused, and a figure straight after a comma or colon is a chart label, not a statement. A page carrying such a figure needs a person: which
version is right is not something a rule can know.
"""

from __future__ import annotations

import re
from typing import Any

_CLAUSE_END = "。；！？!?;"
MIN_CONTEXT = 8  # characters of clause before the figure; shorter phrases recur across measures
MAX_CONTEXT = 24  # headings run into the first clause of a section with no punctuation between them


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("％", "%")


def _flat(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flat(part) for part in value.values())
    if isinstance(value, list):
        return " ".join(_flat(part) for part in value)
    return str(value or "")


def _figures(text: str) -> set[str]:
    found = set(re.findall(r"\d+(?:\.\d+)?", text))
    # Years, months and short counts recur everywhere; a conflict worth stopping for has three digits.
    return {n for n in found if len(n.replace(".", "")) >= 3 and not re.fullmatch(r"(19|20)\d\d", n)}


def _pages(store: Any) -> list[tuple[str, int, str]]:
    return [(document["name"], page["page"], _squash(page["text"]))
            for document in store.documents if document.get("page_kind") != "web"
            for page in document["pages"]]


def conflicts(content: dict[str, Any], store: Any) -> list[dict[str, Any]]:
    """Each figure on the page that the source also states, in the same clause, as another figure."""

    pages = _pages(store)
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for figure in sorted(_figures(_squash(_flat(content)))):
        pattern = re.compile(rf"(?<![\d.]){re.escape(figure)}(?![\d.])")
        for name, number, text in pages:
            for match in pattern.finditer(text):
                start = max(text.rfind(mark, 0, match.start()) for mark in _CLAUSE_END) + 1
                context = text[max(start, match.start() - MAX_CONTEXT):match.start()]
                unit = re.match(r"[^\d\s，,。；;]{0,2}", text[match.end():]).group(0)
                if len(context) < MIN_CONTEXT or len(re.sub(r"[\d.%]", "", re.split(r"[,，:：、]", context)[-1])) < 2:
                    continue  # a figure straight after a separator is a chart label ("男,50.3%"), not a statement
                other = re.compile(rf"{re.escape(context)}(\d+(?:\.\d+)?){re.escape(unit)}")
                for other_name, other_number, other_text in pages:
                    for rival in other.finditer(other_text):
                        value = rival.group(1)
                        if value != figure and (figure, value) not in seen:
                            seen.add((figure, value))
                            found.append({"figure": figure + unit, "page": number, "other": value + unit,
                                          "other_page": other_number, "context": context[-40:]})
    return found
