"""Whether a series is all of what the source gives, or a selection of it.

"北京最多，达到 158 家；其次为上海和广东，均达到 92 家" reads as three regions; the figure
beside it has twenty-seven. Drawn without saying so, three bars pass for the whole
distribution. A series is an *excerpt* when its source page has a figure axis holding all of
its labels and more, or when its own quotes say they pick out some ("最多""其次""前三"…);
*complete* when such an axis holds exactly its labels, or when it was read whole from a figure.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ppt_agent.v2.visual.content import DataSeries

SELECTING = re.compile(r"最多|最高|最低|最少|其次|居首|位居|排名|前[一二三四五六七八九十\d]+[位名个]|领先|主要|等[。；，、]|[如例]")


def _squash(text: str) -> str:
    return re.sub(r"[\s、，,（）()·\-]", "", text or "")


def _source_page(series: DataSeries) -> tuple[str, int] | None:
    places = {point.ref for point in series.points}
    if len(places) != 1:
        return None
    match = re.fullmatch(r"(.+?) 第(\d+)页", places.pop())
    return (match.group(1), int(match.group(2))) if match else None


def assess(series: DataSeries, pdfs: dict[str, str | Path], axes_cache: dict | None = None) -> DataSeries:
    """The series with ``coverage``, ``of_total`` and ``selection`` filled in."""

    from pypdf import PdfReader

    from ppt_agent.v2 import figure_text

    cache = axes_cache if axes_cache is not None else {}
    labels = {_squash(point.label) for point in series.points}
    place = _source_page(series)
    if place is not None and place[0] in pdfs:
        if place not in cache:
            reader = PdfReader(str(pdfs[place[0]]))
            cache[place] = figure_text.axis_labels(reader.pages[place[1] - 1]) if place[1] <= len(reader.pages) else []
        for axis in cache[place]:
            members = {_squash(label) for label in axis}
            if labels <= members:
                if len(members) == len(labels):
                    return series.model_copy(update={"coverage": "complete", "of_total": len(members)})
                return _excerpt(series, len(members), f"原图共 {len(members)} 项，图中为其中 {len(labels)} 项")
    if any(SELECTING.search(point.quote) for point in series.points):
        return _excerpt(series, None, "原文只点出这几项，不是全部")
    return series


def _excerpt(series: DataSeries, total: int | None, words: str) -> DataSeries:
    # A selection of members is compared, never shown as the parts of a whole.
    kind = "category" if series.kind in ("share", "distribution") else series.kind
    return series.model_copy(update={"coverage": "excerpt", "of_total": total, "selection": words[:30], "kind": kind})


def describe(series: dict[str, Any]) -> str | None:
    """What a caption says about coverage, or ``None`` for a complete or unknown series."""

    if series.get("coverage") != "excerpt":
        return None
    return series.get("selection") or "节选"
