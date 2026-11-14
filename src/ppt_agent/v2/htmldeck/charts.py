"""From a verified series to the chart a page may draw.

The model that designs the page chooses where the chart goes. It does not
choose what the chart says: the ``data-chart`` attribute and the caption are
written here from the series, and the page is rejected if either is altered.
"""

from __future__ import annotations

import json
import re
from typing import Any

WHOLE = (99.5, 100.5)  # shares that add up to this are a whole, and may be drawn as one
UNIT_IN_LABEL = 3      # a unit this short rides on every data label; a longer one goes in the caption


def kind_for(series: dict[str, Any]) -> str | None:
    """The chart that draws this series honestly, or ``None`` when no chart does."""

    points = series.get("points") or []
    if len(points) < 2:
        return None
    kind = series.get("kind")
    if kind == "time":
        return "line" if len(points) >= 3 else "column"
    if kind == "category":
        return "bar"
    if kind == "distribution":
        return "column"
    if kind == "share":
        return "pie" if WHOLE[0] <= sum(point["value"] for point in points) <= WHOLE[1] else None
    return None


def chartable(content: dict[str, Any]) -> dict[str, Any] | None:
    """The first series of a page that a chart can draw."""

    return next((series for series in content.get("series") or [] if kind_for(series)), None)


def _format(series: dict[str, Any]) -> str:
    decimals = max((len(f"{point['value']:.6f}".rstrip("0").split(".")[1]) for point in series["points"]), default=0)
    number = "#,##0" + ("." + "0" * min(decimals, 2) if decimals else "")
    unit = series.get("unit") or ""
    return number + (f'"{unit}"' if 0 < len(unit) <= UNIT_IN_LABEL else "")


def spec(series: dict[str, Any]) -> dict[str, Any]:
    """The ``data-chart`` value for a series."""

    kind = kind_for(series)
    if kind is None:
        raise ValueError("this series cannot be drawn as a chart")
    points = list(series["points"])
    if kind == "bar":
        points.sort(key=lambda point: -point["value"])  # a ranking reads top-down
    values = [point["value"] for point in points]
    # What the page is about: the latest point of a trend, the largest of a comparison.
    highlight = len(points) - 1 if series.get("kind") == "time" else values.index(max(values))
    made: dict[str, Any] = {
        "kind": kind,
        "categories": [point["label"] for point in points],
        "series": [{"name": series.get("metric") or "数值", "values": values}],
        "highlight": highlight,
        "format": _format(series),
    }
    projected = [index for index, point in enumerate(points) if point.get("projected")]
    if projected:
        made["projected"] = projected
    return made


def attr(series: dict[str, Any]) -> str:
    """The attribute as it goes into single-quoted HTML."""

    return json.dumps(spec(series), ensure_ascii=False, separators=(",", ":")).replace("'", "’")


def _pages(series: dict[str, Any]) -> str:
    pages = sorted({int(number) for point in series["points"]
                    for span in re.findall(r"第\s*([\d\-–]+)\s*页", point.get("ref") or "")
                    for number in re.findall(r"\d+", span)})
    if not pages:
        return ""
    return f"原文第 {pages[0]} 页" if len(pages) == 1 else f"原文第 {pages[0]}–{pages[-1]} 页"


def caption(series: dict[str, Any]) -> str:
    """What the chart measures, whose, when and from where: the line a reader
    needs to trust it. Forecasts and approximate values are named."""

    from ppt_agent.v2.data_series import approximate

    unit = series.get("unit") or ""
    parts = [(series.get("metric") or "") + (f"（{unit}）" if unit else "")]
    parts += [part for part in (series.get("scope"), series.get("period")) if part and part != "未说明"]
    points = series["points"]
    forecast = [point["label"] for point in points if point.get("projected")]
    if forecast:
        parts.append("、".join(forecast) + "为预测值" if len(forecast) < len(points) else "均为预测值")
    rough = [point["display"] for point in points if approximate(point.get("display") or "")]
    if rough:
        parts.append("原文作「" + "、".join(rough) + "」")
    pages = _pages(series)
    if pages:
        parts.append(pages)
    return " · ".join(parts)


def same(found: dict[str, Any], series: dict[str, Any]) -> bool:
    """Whether a chart on the page says exactly what the series says."""

    wanted = json.loads(attr(series))
    try:
        drawn = found["series"]
        return (found.get("kind") == wanted["kind"] and list(found.get("categories") or []) == wanted["categories"]
                and len(drawn) == 1 and [float(value) for value in drawn[0]["values"]] == wanted["series"][0]["values"]
                and list(found.get("projected") or []) == list(wanted.get("projected") or []))
    except (KeyError, TypeError, ValueError, IndexError):
        return False
