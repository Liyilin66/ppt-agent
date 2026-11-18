"""Sentences, words and measured figures in Chinese or English source text (P4 stage B).

The first run on an English source (IEA Global EV Outlook 2025) found 10 of 19 content pages
supported by a 173-page report, and an outline of three sections: sentences were split only at
Chinese punctuation, counted only in Chinese characters, measured only in Chinese units, and PDF
lines were joined with no space ("the\\nmarket" -> "themarket"). To every check built on those,
an English report held almost no facts.

One measure for both languages: a Chinese character counts one, an English word counts two
(a Chinese sentence of twelve characters says about what six English words do).
"""

from __future__ import annotations

import re

CJK = r"㐀-䶿一-鿿"
_CN_UNITS = r"%|％|个百分点|亿|万|倍|元|美元|人|家|个|项|款|笔"
_EN_UNITS = (r"percentage points?|million|billion|trillion|thousand|bn|mn|"
             r"[GMkT]Wh|[GMk]W|tonnes?|Mt|kt|km|vehicles|cars|units|times|-fold|x(?![A-Za-z])")
# A figure with the unit that makes it a measurement; English amounts may also lead with a currency.
MEASURE = re.compile(rf"(?:(?:USD|EUR|CNY|RMB|US\$|\$|€|£)\s*)?\d+(?:[.,]\d+)*\s*(?:{_CN_UNITS}|{_EN_UNITS})"
                     rf"|(?:USD|EUR|CNY|RMB|US\$|\$|€|£)\s*\d+(?:[.,  ]\d{{3}})*(?:\.\d+)?")


def join_lines(text: str | None) -> str:
    """PDF lines joined: no space between Chinese characters, one space between English words."""

    # A line break next to a Chinese character (on either side) is no space; between Latin words it is one.
    text = re.sub(rf"(?<=[{CJK}，。；：、（）])\s*\n\s*|\s*\n\s*(?=[{CJK}，。；：、（）])", "", text or "")
    return re.sub(r"\s*\n\s*", " ", text)


def sentences(text: str | None) -> list[str]:
    """Sentences of joined text, ended by Chinese punctuation or by . ; ! ? before a space and a new
    sentence (a decimal point or "e.g. the" does not end one)."""

    joined = join_lines(text)
    parts = re.split(r"(?<=[。；！？])|(?<=[.;!?])\s+(?=[A-Z\d\"“(‘])", joined)
    return [" ".join(part.split()) for part in parts if part and part.strip()]


def weight(text: str | None) -> int:
    """How much a sentence says: Chinese characters plus two per English word."""

    text = text or ""
    return len(re.findall(rf"[{CJK}]", text)) + 2 * len(re.findall(r"[A-Za-z]{2,}", text))
