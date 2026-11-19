"""Find the data a page could draw, and prove each value against the source.

A chart is only as good as its numbers, so the page's author never writes one.
A separate step reads the evidence for sets of values of one measure in one
unit, quoting the source for every value; the rules here then check each quote
against the evidence text, bind each value to its label, and decide which page
may draw the series. Anything that fails is dropped, and says why. An empty
result is a correct result.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from ppt_agent.v2.visual.content import DataSeries

MIN_NUMBERS = 2  # evidence with fewer figures than this cannot hold a series
MAX_POINTS = 16
MAX_LOST = 1 / 3   # more of a series than this failing its check, and what is left is not that series
MIN_LEFT = 3       # a comparison cut down to two is a different comparison
_PAGE = re.compile(r"^\[(?P<name>[^|\]]+?)\s*\|\s*(?P<kind>[^\]]*?)\s*p(?P<page>\d+)\]\s*$", re.M)
_FIGURE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_YEAR = re.compile(r"(?:19|20)\d{2}")
# Only words that say a value is not yet observed. "提升到2025年的47.5%" is a measurement; a date after
# "到" or "截至" says nothing about whether the value was observed.
_FORECAST = re.compile(r"预计|预测|预期|有望|计划|目标值|目标为|将达|将增|将超|将突破|将实现|规划"
                       # English reports (IEA): checked on text with its spaces removed ("setto")
                       r"|expected|projected|forecast|anticipated|setto|will|scenario|STEPS|outlook", re.I)
_BEFORE = "约超近逾达至少多于不足低高过"   # qualifiers a source puts before a figure
_AFTER = re.compile(r"^(?:余|多|来)")        # ...and straight after it: 80余种
_TAIL = re.compile(r"(?:以上|以下|左右|上下)$")
_WHEN = re.compile(r"(?:19|20)\d{2}|\d{1,2}月|季度|Q[1-4]|H[12]|上半年|下半年|年初|年底|世纪"
                   r"|January|February|March|April|May|June|July|August|September|October|November|December|quarter")
# A category is a thing being measured, not another measure or a change in one.
_MEASURE = re.compile(r"率$|比例|占比|数量|次数|周期|成本|规模|能耗|排放|时间|时长|效率|增加|减少|缩短|提升|提高|下降|降低|增长|节省"
                      r"|(?i:\bshare\b|\brate\b|growth|increase|decrease|decline)")

SERIES_SYSTEM = (
    "你在为一页演示文稿找可以画成图表的数据。只从给出的原文里找「同一个指标、同一个单位、两个及以上取值」的组合："
    "一个指标在不同时间的取值（time）、同一指标在不同对象之间的对比（category）、一个整体的构成占比（share）、"
    "一个指标在不同区间的分布（distribution）。\n"
    "规则：\n"
    "- 每个值都要给出包含这个数字的原文片段 quote，逐字照抄，不超过 120 个字，不要省略号，不要改写；"
    "多个值出现在同一句话里时可以共用同一个 quote。\n"
    "- 不同指标、不同单位、不同口径的数字不能拼成一条：金额和占比不是一条，同比增速和规模不是一条，"
    "百分比和百分点不是一条。\n"
    "- 不要计算，不要补全原文没有的年份或类别，不要把「约为 2022 年的 9 倍」这样的话换算成数值。\n"
    "- display 写原文里的数字和单位（如 4.5%、316亿美元），value 是其中的数字，unit 是单位。\n"
    "- label 是这个值对应的时间或对象，用原文的说法（如 2022年、2024年上半年、GPT-3）。\n"
    "- 只有原文明确说是预计、预测、计划、目标的值才把 projected 设为 true；「提升到 2025 年」「截至 2025 年」是实测。\n"
    "- metric 写清指标的完整定义（谁的、什么的），scope 写口径或主体（如 全球、中国），period 写时间范围。\n"
    "- 标着「图中坐标配对」的段落，是资料里一张图的标签和数值按它们在页面上的位置一一配好的，每行「标签 数值」；"
    "用它时 quote 照抄那一行，label 照抄行里的标签。它只说明这张图画了什么，指标、口径和时间要从图题和正文里找。\n"
    "- 找不到就返回空列表。空结果是正确答案，不要凑。\n"
    '只输出 JSON：{"series":[{"metric":"","unit":"","kind":"time|category|share|distribution","period":"","scope":"",'
    '"points":[{"label":"","value":0,"display":"","quote":"","projected":false}]}]}'
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("％", "%")


def pages_of(evidence_text: str) -> list[tuple[str, int, str]]:
    """(file name, page, text) for each labelled block of an evidence packet."""

    marks = list(_PAGE.finditer(evidence_text or ""))
    blocks = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(evidence_text)
        blocks.append((mark["name"].strip(), int(mark["page"]), evidence_text[mark.end():end]))
    return blocks


def _locate(quote: str, blocks: list[tuple[str, int, str]]) -> str | None:
    """Where the quote stands in the source, as "<file> 第N页", or ``None``."""

    wanted = _squash(quote)
    if len(wanted) < 6:
        return None
    for name, page, text in blocks:
        if wanted in _squash(text):
            return f"{name} 第{page}页"
    # A sentence can run over a page break.
    ordered = sorted(set((name, page) for name, page, _ in blocks))
    by_page: dict[tuple[str, int], str] = {}
    for name, page, text in blocks:
        by_page.setdefault((name, page), text)  # a figure's paired text is not the page's running text
    for name, page in ordered:
        if (name, page + 1) in by_page and wanted in _squash(by_page[(name, page)] + by_page[(name, page + 1)]):
            return f"{name} 第{page}-{page + 1}页"
    return None


def _number(display: str) -> tuple[str, str] | None:
    """The figure in a display string and the unit that follows it."""

    # Read before spaces are removed: "23 000 2023 USD" is 23000 in 2023 dollars, not 230002023.
    raw = (display or "").replace("％", "%").strip().lstrip(_BEFORE).strip()
    match = re.match(r"\d+(?:,\d{3}(?!\d))*(?:[ \u00a0\u202f]\d{3}(?![\d,.]))*(?:\.\d+)?", raw)
    if not match:
        return None
    figure = re.sub(r"[\s\u202f]", "", match.group())
    return figure, _TAIL.sub("", _AFTER.sub("", _squash(raw[match.end():])))


def approximate(display: str) -> bool:
    """Whether the source gives this value as "about", "over" or "nearly" a figure."""

    text = _squash(display)
    match = _FIGURE.search(text)
    return bool(match) and (match.start() > 0 or bool(_AFTER.match(text[match.end():])) or bool(_TAIL.search(text)))


def _anchor(label: str, quote: str) -> int | None:
    """Where in the quote this label is named; ``None`` when it is not."""

    label, quote = _squash(label), _squash(quote)
    if label in quote:
        return quote.find(label)
    years = _YEAR.findall(label)
    if years:
        return quote.find(years[0]) if years[0] in quote else None
    words = re.findall(r"[A-Za-z][A-Za-z0-9.\-]+", label)
    grams = [run[i:i + 2] for run in re.findall(r"[㐀-鿿]+", label) for i in range(len(run) - 1)]
    found = [quote.find(token) for token in words + grams if token in quote]
    if len(found) < max(1, (len(words) + len(grams) + 1) // 2):
        return None
    return min(found)


def _value_at(figure: str, quote: str, after: int = 0) -> int | None:
    """Where the figure stands in the quote as a whole number, not inside another."""

    quote = _squash(quote)
    for match in re.finditer(re.escape(figure), quote):
        before = quote[match.start() - 1] if match.start() else ""
        behind = quote[match.end()] if match.end() < len(quote) else ""
        if before.isdigit() or before == "." or behind.isdigit() or (behind == "." and quote[match.end() + 1:match.end() + 2].isdigit()):
            continue
        if match.start() >= after:
            return match.start()
    return None


def verify(raw: list[dict[str, Any]], evidence_text: str) -> tuple[list[DataSeries], list[dict[str, Any]]]:
    """The series that stand up against the evidence, and what was dropped and why."""

    blocks = pages_of(evidence_text)
    kept: list[DataSeries] = []
    rejected: list[dict[str, Any]] = []

    def drop(series: dict[str, Any], why: str, point: dict[str, Any] | None = None) -> None:
        rejected.append({"metric": str(series.get("metric") or "")[:36], "why": why,
                         **({"label": str(point.get("label") or ""), "display": str(point.get("display") or "")}
                            if point else {})})

    for series in raw or []:
        if not isinstance(series, dict) or series.get("kind") not in ("time", "category", "share", "distribution"):
            if isinstance(series, dict):
                drop(series, "不是四种序列之一")
            continue
        unit = _squash(str(series.get("unit") or ""))
        points: list[dict[str, Any]] = []
        proposed = [point for point in (series.get("points") or []) if isinstance(point, dict)]
        if len(proposed) > MAX_POINTS:
            drop(series, f"超过 {MAX_POINTS} 个值，一张图画不下；不截断成一部分")
            continue
        for point in proposed:
            label = str(point.get("label") or "").strip()
            if series["kind"] == "time" and not _WHEN.search(label):
                drop(series, "时间序列的标签不是时间", point)
                continue
            if series["kind"] in ("category", "share", "distribution") and _MEASURE.search(label):
                drop(series, "标签是另一个指标或变化量，不是被比较的对象", point)
                continue
            display, quote = str(point.get("display") or ""), str(point.get("quote") or "")
            parsed = _number(display)
            if parsed is None:
                drop(series, "display 里没有数字", point)
                continue
            figure, suffix = parsed
            try:
                value = float(figure.replace(",", ""))
            except ValueError:
                drop(series, "数字无法解析", point)
                continue
            if not isinstance(point.get("value"), (int, float)) or abs(float(point["value"]) - value) > 1e-9:
                drop(series, "value 和 display 里的数字不一致", point)
                continue
            # English writes a currency before the amount ("USD 40 400"): the unit then precedes the figure.
            before_unit = not suffix and unit and (unit + figure) in _squash(quote)
            if suffix != unit and not before_unit:
                drop(series, f"单位「{suffix}」和序列单位「{unit}」不一致", point)
                continue
            ref = _locate(quote, blocks)
            if ref is None:
                drop(series, "引文不是原文里的原话", point)
                continue
            at = _value_at(figure, quote)
            if at is None or not (before_unit and _squash(quote)[:at].endswith(unit)
                                  or _AFTER.sub("", _squash(quote)[at + len(figure):]).startswith(unit)):
                drop(series, "引文里没有这个数字和单位", point)
                continue
            anchor = _anchor(label, quote)
            if anchor is None:
                drop(series, "引文里没有提到这个标签", point)
                continue
            if series["kind"] == "time" and _YEAR.search(label):
                # The year a value belongs to is the last one named before it.
                earlier = _YEAR.findall(_squash(quote)[:at])
                if earlier and earlier[-1] not in label:
                    drop(series, f"数值前最近的年份是 {earlier[-1]}，不是这个标签的年份", point)
                    continue
            explicit = bool(_FORECAST.search(_squash(quote)))
            if point.get("projected") and not explicit:
                # Called a forecast with no forecast word in the source: its status cannot be checked,
                # and neither the observed nor the forecast drawing may be used for it.
                drop(series, "标为预测，但原文没有预测措辞，状态无法核实", point)
                continue
            points.append({"label": label[:20], "value": value, "display": _squash(display)[:16],
                           "quote": quote.strip()[:160], "ref": ref,
                           "projected": explicit,
                           "_anchor": anchor, "_at": at})
        omitted: list[str] = []
        if len(points) < len(proposed):
            # Part of a series is a different claim from the whole of it. What is left may be drawn only when
            # it is still a fair comparison: not parts of a whole, most of it standing, and neither its largest
            # nor its smallest value among those lost. The page then names what is missing.
            lost = [point for point in proposed
                    if str(point.get("label") or "").strip() not in {kept["label"] for kept in points}]
            claimed = [float(point["value"]) for point in lost if isinstance(point.get("value"), (int, float))]
            values = [point["value"] for point in points]
            fair = (series["kind"] in ("time", "category") and len(points) >= MIN_LEFT
                    and len(lost) <= MAX_LOST * len(proposed) and len(claimed) == len(lost)
                    and all(min(values) <= value <= max(values) for value in claimed))
            if not fair:
                drop(series, "有的值没有通过核对，剩下的不构成完整的比较，整条不画")
                continue
            omitted = [str(point.get("label") or "")[:20] for point in lost]
            rejected.append({"metric": str(series.get("metric") or "")[:36],
                             "why": f"{len(lost)} 个值未通过核对，已从图中去掉并在图注注明：" + "、".join(omitted)})
        # Values named in one sentence pair with their labels in reading order.
        by_quote: dict[str, list[dict[str, Any]]] = {}
        for point in points:
            by_quote.setdefault(_squash(point["quote"]), []).append(point)
        crossed = False
        for group in by_quote.values():
            if len(group) > 1 and [id(p) for p in sorted(group, key=lambda p: p["_anchor"])] != \
                    [id(p) for p in sorted(group, key=lambda p: p["_at"])]:
                crossed = True
        if crossed:
            drop(series, "同一句话里标签和数值的顺序对不上，无法确定哪个值属于哪个标签")
            continue
        seen, unique = set(), []
        for point in points:
            if point["label"] not in seen:
                seen.add(point["label"])
                unique.append({key: value for key, value in point.items() if not key.startswith("_")})
        if len(unique) < 2:
            drop(series, "不足两个值")
            continue
        from ppt_agent.v2.relations import series_classes

        classes = series_classes(unique)
        if len(classes) > 1:
            # A total beside an increase, a rate beside a change in it: not one measure, whatever the metric says.
            drop(series, "序列里混了不同种类的量：" + "、".join(sorted(classes)))
            continue
        kind = series["kind"]
        if kind == "share" and not 99.0 <= sum(point["value"] for point in unique) <= 101.0:
            kind = "category"  # parts that do not make a whole (several answers allowed) are compared, not split
        try:
            kept.append(DataSeries(
                metric=str(series.get("metric") or "")[:36], unit=unit[:10], kind=kind,
                measure=next(iter(classes)) if len(classes) == 1 else None,
                period=(str(series.get("period") or "")[:28] or None),
                scope=(str(series.get("scope") or "")[:30] or None), points=unique, omitted=omitted))
        except ValidationError as error:
            drop(series, "不构成一条序列：" + str(error.errors()[0].get("msg", ""))[:60])
    return kept, rejected


AUDIT_SYSTEM = (
    "你是数据审校。下面每条序列都准备画成一张图表，数值已经逐字对过原文。请只判断它能不能画在同一张图上："
    "所有的值必须是同一个指标（同一个定义、同一个口径、同一个单位）在不同时间或不同对象上的取值，彼此可以直接比较大小。"
    "下面这些情况不能画：把两个不同的指标并排（准确率和周期缩短幅度）；口径不同（一个是累计、一个是当年）；"
    "对象不可比（一个是全球、一个是某家公司）；一个是实际值一个是增幅；标签张冠李戴。拿不准就判不能画。"
    '只输出 JSON：{"verdicts":[{"index":序号,"ok":true或false,"reason":"不超过30个字"}]}'
)


async def audit(client: Any, series: list[DataSeries], evidence_text: str) -> tuple[list[DataSeries], list[dict[str, Any]]]:
    """A second reader, who did not propose the series, says whether each is one
    measure. A series the reader cannot vouch for is not drawn."""

    if not series:
        return [], []
    listing = [{"index": index, "metric": item.metric, "unit": item.unit, "kind": item.kind, "scope": item.scope,
                "points": [{"label": point.label, "display": point.display, "quote": point.quote} for point in item.points]}
               for index, item in enumerate(series)]
    try:
        reply = await client.complete_json(task="series_audit", system=AUDIT_SYSTEM,
                                           user="序列：" + json.dumps(listing, ensure_ascii=False),
                                           max_output_tokens=1200, context={"series": listing})
        verdicts = {entry.get("index"): entry for entry in (reply.get("verdicts") or []) if isinstance(entry, dict)}
    except Exception as error:  # noqa: BLE001
        verdicts, failure = {}, str(error)[:80]
    else:
        failure = "复核没有给出结论"
    kept, rejected = [], []
    for index, item in enumerate(series):
        verdict = verdicts.get(index)
        if verdict and verdict.get("ok") is True:
            kept.append(item)
        else:
            rejected.append({"metric": item.metric, "why": "复核未通过：" + str((verdict or {}).get("reason") or failure)[:60]})
    return kept, rejected


def signature(series: DataSeries) -> tuple:
    return (series.unit, tuple(sorted((point.label, point.value) for point in series.points)))


async def extract(client: Any, checkpoints: Any, evidence_text: str) -> dict[str, Any]:
    """Verified series in one evidence packet: {"series": [...], "rejected": [...]}.

    One model call per distinct packet, saved under ``series/<hash>.json``."""

    name = "series/" + hashlib.sha1((evidence_text or "").encode("utf-8")).hexdigest()[:12] + ".json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        # Whether a value is a forecast is read from its quote; a saved series is judged by the current rule.
        for series in saved.get("series") or []:
            for point in series.get("points") or []:
                point["projected"] = bool(_FORECAST.search(_squash(point.get("quote") or "")))
        return saved
    result: dict[str, Any] = {"series": [], "rejected": [], "called": False}
    if len(_FIGURE.findall(_YEAR.sub("", evidence_text or ""))) >= MIN_NUMBERS and hasattr(client, "complete_json"):
        try:
            reply = await client.complete_json(task="data_series", system=SERIES_SYSTEM,
                                               user="原文：\n" + evidence_text, max_output_tokens=3000,
                                               context={"evidence": evidence_text})
        except Exception as error:  # noqa: BLE001 - a page without a chart is still a page
            result["error"] = str(error)[:200]
            reply = None
        raw = reply.get("series") if isinstance(reply, dict) else None
        kept, rejected = verify(raw if isinstance(raw, list) else [], evidence_text)
        kept, doubted = await audit(client, kept, evidence_text)
        rejected += doubted
        result = {"series": [item.model_dump(mode="json") for item in kept], "rejected": rejected, "called": True,
                  "proposed": len(raw) if isinstance(raw, list) else 0, **({"error": result["error"]} if "error" in result else {})}
    if checkpoints is not None and "error" not in result:
        checkpoints.save(name, result)
    return result


def prune(items: list[DataSeries]) -> list[DataSeries]:
    """Drop a series whose every value is in a fuller one: it is the same figure with values
    missing (running text names five purposes, the figure beside it all seven)."""

    def values(item: DataSeries) -> set[tuple[str, str]]:
        return {(_squash(point.display), point.ref.split(" ")[0]) for point in item.points}

    kept = []
    for item in items:
        mine = values(item)
        if any(mine < values(other) for other in items if other is not item):
            continue
        if any(mine == values(other) for other in kept):
            continue  # the same values, perhaps under another wording
        kept.append(item)
    return kept


def assign(found: dict[int, list[DataSeries]], contents: dict[int, dict[str, Any]]) -> dict[int, list[DataSeries]]:
    """Give each distinct series to the one page that is about it.

    A page is about a series when its own text already states one of the
    series' values; the page that states the most wins, an earlier page on a tie.
    A series no page talks about is drawn nowhere."""

    from ppt_agent.v2.htmldeck.ledger import fact_keys, page_facts

    best: dict[tuple, tuple[tuple, int, DataSeries]] = {}
    for page in sorted(found):
        content = {key: value for key, value in contents.get(page, {}).items() if key not in ("series", "figure")}
        lead, support = page_facts(content)
        for series in found[page]:
            keys = [key for point in series.points for key in fact_keys(point.display)[:1]]
            score = (sum(key in lead for key in keys), sum(key in lead or key in support for key in keys))
            if score[1] == 0:
                continue
            mark = signature(series)
            if mark not in best or score > best[mark][0]:
                best[mark] = (score, page, series)
    placed: dict[int, list[DataSeries]] = {}
    for score, page, series in sorted(best.values(), key=lambda entry: (-entry[0][0], -entry[0][1], entry[1])):
        if page not in placed:  # one chart a page: the one the page leans on most
            placed[page] = [series]
    return placed


def summary(by_packet: list[dict[str, Any]], placed: dict[int, list[DataSeries]]) -> dict[str, Any]:
    distinct = {signature(DataSeries.model_validate(item)) for result in by_packet for item in result["series"]}
    return {"packets_read": sum(bool(result.get("called")) for result in by_packet),
            "series_proposed": sum(result.get("proposed", 0) for result in by_packet),
            "series_verified": len(distinct),
            "points_rejected": sum(len(result["rejected"]) for result in by_packet),
            "pages_with_series": sorted(placed)}
