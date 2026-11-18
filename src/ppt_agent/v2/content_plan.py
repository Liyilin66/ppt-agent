"""Plan what every page says before any page is written, and catch pages that say it twice.

Pages are written concurrently, each from its own evidence. Two pages whose evidence
overlaps then tend to argue the same conclusion in different words (CNNIC p4/p5: both
"普及率半年升到 36.5%"). The plan gives each page its own claim, the fact it leads with and
what it adds over the others; after writing, near pairs are found by rule and judged by a
model, and the later page is rewritten to its planned increment.

A key figure may come back - in a summary and in the analysis it summarises - but only one
page leads with it.
"""

from __future__ import annotations

import json
import re
from typing import Any

NUMBER = re.compile(r"\d+(?:\.\d+)?")
MEASURE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|个百分点|亿|万|倍|元|美元|人|家|个|项|款|笔)")
WINDOW = 3          # pages apart that a reader still holds in mind
SHARED_FIGURES = 2  # figures in common that make two pages a suspect pair
FACTS_PER_PAGE = 16
FACT_CHARS = 90

PLAN_SYSTEM = (
    "你是这套演示文稿的内容策划。下面是所有内容页的计划和各页证据里带数字的句子。"
    "在写任何一页之前，为每页定下：claim（本页的核心观点，一句完整结论，40 字内）、"
    "lead（本页领衔的那一个事实，有数字就带数字，24 字内）、increment（相对其他页，本页新增的信息，40 字内）、"
    "owns（本页独有的事实，2–5 条，每条带数字，写成证据里的原样短句）、"
    "context（可以作为背景在本页出现、但由别页领衔的数字，最多 3 个）、role（summary 表示这一页总结别页，否则 detail）。\n"
    "要求：\n"
    "1. 每页观点必须不同；连续几页不能只是换一种说法讲同一个结论。\n"
    "2. 同一个关键数字只能有一页领衔；它可以在摘要页和详细分析页各出现一次，但别页只能把它当背景。\n"
    "3. 两页的证据讲的是同一件事时，把它们分成不同的角度（例如一页讲规模、一页讲结构，一页讲现状、一页讲原因），"
    "角度必须来自各自的证据，不能编造。\n"
    "4. 观点和领衔事实只能来自该页自己的证据句；不要自己计算合计、差值或倍数。\n"
    "5. 各页的 owns 互不重叠。几页共用一份证据时，把证据里的事实分组分给它们；"
    "宁可让一页讲一组别页都没用的事实（例如构成、人群、原因），也不要让它复述别页。\n"
    "6. 一页的价值不只来自独有的数字。role 写这一页在叙事里的任务：detail（用自己的事实论证一个观点）；"
    "summary（大纲本身就是总结页，汇总前面几页）；judgement（战略判断：把别页的事实合起来，得出它们各自没有说出的判断或建议，"
    "draws_on 写它依据的页码，至少两页）；visual（由一张资料原图或数据图承担的页，visual 写图的名称，例如「图 3 …」）。"
    "这几种页不需要独有数字，但必须有别页没有的观点。某页既没有自己的事实，也没有上面这些任务时，"
    "不要硬分：owns 只写它真有的，role 写 thin。\n"
    '只输出 JSON：{"pages":[{"page":页码,"claim":"","lead":"","increment":"","owns":[""],"context":[""],'
    '"role":"detail","draws_on":[],"visual":""}]}'
)

JUDGE_SYSTEM = (
    "下面是同一套演示文稿里相隔不远的页（earlier 可能是一页或前面几页）。判断 later 是否只是换一种说法在讲前面已有的结论"
    "（核心观点相同，读者看完第二页没有得到新信息）。同一个数字出现在两页不等于重复：一页用它做背景、"
    "另一页讲它的构成、原因或影响，是两个结论。但如果后一页的数字全都已经在前面几页出现过（nothing_new），"
    "只有当它给出了前面没有的解释、构成、原因或影响时才不算重复；仅仅把前几页的数字汇总一遍、换个说法下结论，算重复。"
    '只输出 JSON：{"same":true 或 false,"why":"30 字内"}'
)


def facts(text: str | None, limit: int = FACTS_PER_PAGE) -> list[str]:
    """Sentences of the evidence that carry a measured figure, in reading order.

    PDF text breaks lines mid-sentence, so lines are joined before splitting; axis ticks
    and bare chart labels (few words, many figures) are not sentences."""

    ticks = re.sub(r"(?:(?<![\d.])\d{1,3}%\s+){2,}\d{1,3}%", "", text or "")  # 0% 20% 40% ...: an axis
    joined = re.sub(r"\s*\n\s*", "", ticks)
    found, seen = [], set()
    for sentence in re.split(r"(?<=[。；！？])", joined):
        sentence = " ".join(sentence.split())
        words = len(re.findall(r"[\u4e00-\u9fff]", sentence))
        if not MEASURE.search(sentence) or words < 6 or len(MEASURE.findall(sentence)) > max(3, words // 4):
            continue
        sentence = sentence[:FACT_CHARS]
        if sentence in seen:
            continue
        seen.add(sentence)
        found.append(sentence)
        if len(found) >= limit:
            break
    return found


def figures(text: str | None) -> set[str]:
    """The measured figures in a text, normalised (``36.5 %`` → ``36.5%``)."""

    return {re.sub(r"\s+", "", match).replace("％", "%") for match in MEASURE.findall(text or "")}


def owned_figures(entry: dict[str, Any]) -> set[str]:
    return figures(entry.get("lead")) | set().union(*(figures(item) for item in entry.get("owns") or []))


def conflicts(plan: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Pairs of pages that lead with the same figure, neither of them a summary."""

    found = []
    pages = sorted(plan)
    for index, later in enumerate(pages):
        for earlier in pages[:index]:
            first, second = plan[earlier], plan[later]
            if "summary" in (first.get("role"), second.get("role")):
                continue
            shared = figures(first.get("lead")) & figures(second.get("lead"))
            owned = owned_figures(first) & owned_figures(second)
            if shared or owned:
                found.append({"pages": [earlier, later], "figures": sorted(shared | owned)})
    return found


SUMMARY_WORDS = ("总结", "小结", "摘要", "回顾", "概览", "要点", "结论")


def _summary_slot(slot) -> bool:
    brief = getattr(slot, "brief", None)
    text = " ".join([getattr(brief, "title", "") or "", getattr(brief, "layout_hint", "") or ""])
    return any(word in text for word in SUMMARY_WORDS) or "summary" in text


def _digest(slots, packets) -> list[dict[str, Any]]:
    digest = []
    for slot in slots:
        packet = packets.get(slot.page_number)
        brief = slot.brief
        digest.append({"page": slot.page_number, "section": slot.section_title,
                       "planned_title": brief.title if brief else None,
                       "points": list(brief.points)[:4] if brief else [],
                       "evidence_facts": facts(packet.text if packet is not None else None)})
    return digest


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    context = entry.get("context") if isinstance(entry.get("context"), list) else []
    owns = entry.get("owns") if isinstance(entry.get("owns"), list) else []
    return {"claim": str(entry.get("claim") or "")[:60], "lead": str(entry.get("lead") or "")[:40],
            "owns": [str(item)[:60] for item in owns][:5],
            "increment": str(entry.get("increment") or "")[:60],
            "context": [str(item)[:24] for item in context][:3],
            "role": entry.get("role") if entry.get("role") in NARRATIVE + ("thin",) else "detail",
            "draws_on": [page for page in entry.get("draws_on") or [] if isinstance(page, int)][:6],
            "visual": str(entry.get("visual") or "")[:40]}


NARRATIVE = ("summary", "judgement", "visual")  # tasks that give a page its place without figures of its own


async def plan(client, checkpoints, slots, packets) -> dict[int, dict[str, Any]]:
    """Each page's claim, lead fact and increment; cached at ``typeset/content_plan.json``."""

    saved = checkpoints.load("typeset/content_plan.json")
    numbers = sorted(slot.page_number for slot in slots)
    evidence = {str(number): list(getattr(packets.get(number), "chunk_ids", []) or []) for number in numbers}
    if saved and sorted(int(page) for page in saved.get("pages", {})) == numbers \
            and saved.get("evidence", evidence) == evidence:
        return {int(page): entry for page, entry in saved["pages"].items()}
    digest = _digest(slots, packets)
    user = json.dumps(digest, ensure_ascii=False)
    result: dict[int, dict[str, Any]] = {}
    found: list[dict[str, Any]] = []
    rounds = 0
    for _ in range(2):
        rounds += 1
        try:
            reply = await client.complete_json(task="content_plan", system=PLAN_SYSTEM, user=user,
                                               max_output_tokens=6000, context={"pages": digest})
        except Exception:  # noqa: BLE001 - pages are still written, from their own evidence
            break
        entries = reply.get("pages") if isinstance(reply, dict) else None
        unsupported = []
        result = {entry["page"]: _clean(entry) for entry in entries or []
                  if isinstance(entry, dict) and entry.get("page") in numbers}
        for page, entry in result.items():
            packet = packets.get(page)
            own = figures(packet.text if packet is not None else "")
            missing = owned_figures(entry) - own
            if missing:
                # A lead the page's evidence does not hold (often a sum the planner worked out)
                # would make the page argue a number the source never gives.
                unsupported.append({"page": page, "lead": entry["lead"], "not_in_evidence": sorted(missing)})
                entry.update(lead="", claim="", owns=[], unsupported_lead=True)
            if entry["role"] == "summary" and not _summary_slot(next(
                    (slot for slot in slots if slot.page_number == page), None)):
                entry["role"] = "detail"  # only a page the outline made a summary may restate others
        found = conflicts(result)
        if not found and not unsupported:
            break
        user += ("\n上一版策划有问题，请改正后整套重新输出："
                 + ("\n这些页领衔了同一个数字，让每个数字只有一页领衔：" + json.dumps(found, ensure_ascii=False) if found else "")
                 + ("\n这些页的领衔事实里有证据中没有的数字（不能自己计算合计或差值）：" + json.dumps(unsupported, ensure_ascii=False)
                    if unsupported else ""))
    checkpoints.save("typeset/content_plan.json", {"evidence": evidence,
                                                   "pages": {str(k): v for k, v in sorted(result.items())},
                                                   "conflicts_left": found, "rounds": rounds})
    return result


def brief_for(page: int, content_plan: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """What one page is told while it is written: its own plan and what the others lead with."""

    if page not in content_plan:
        return None
    return {"this_page": content_plan[page],
            "other_pages": [{"page": other, "claim": entry["claim"], "lead": entry["lead"], "owns": entry.get("owns", [])}
                            for other, entry in sorted(content_plan.items()) if other != page]}


def _text(content: dict[str, Any]) -> str:
    parts = [content.get("title"), content.get("lead"), (content.get("highlight") or {}).get("value"),
             (content.get("highlight") or {}).get("text"), content.get("takeaway")]
    for item in content.get("items") or []:
        parts += [item.get("heading"), item.get("value"), item.get("body")]
    return " ".join(str(part) for part in parts if part)


def suspects(contents: dict[int, dict[str, Any]], *, window: int = WINDOW) -> list[dict[str, Any]]:
    """Near pairs that may argue one conclusion twice: same focal figure, or the same
    figures in the title, or several figures in common."""

    found = []
    pages = sorted(contents)
    for index, later in enumerate(pages):
        for earlier in pages[:index]:
            if later - earlier > window:
                continue
            first, second = contents[earlier], contents[later]
            focal = [(content.get("highlight") or {}).get("value") for content in (first, second)]
            same_focal = bool(focal[0]) and figures(focal[0]) and figures(focal[0]) == figures(focal[1])
            titles = figures(first.get("title")) & figures(second.get("title"))
            shared = figures(_text(first)) & figures(_text(second))
            seen = set().union(*(figures(_text(contents[page])) for page in pages[:index]
                                 if 0 < later - page <= window))
            own = figures(_text(second))
            focal_shown = bool(figures(focal[1])) and figures(focal[1]) <= figures(_text(first))
            # A page whose every figure is already on the pages just before it adds nothing new, even
            # when it carries one figure only (CNNIC: four pages, one figure each, one story).
            nothing_new = bool(own) and own <= seen and (len(own) >= SHARED_FIGURES or focal_shown)
            if same_focal or titles or len(shared) >= SHARED_FIGURES or (nothing_new and shared) or focal_shown:
                found.append({"pages": [earlier, later], "shared": sorted(shared),
                              "why": "焦点数字相同" if same_focal else "标题数字相同" if titles
                              else "后页没有新数字" if nothing_new
                              else "后页的焦点数字前页已经讲过" if focal_shown else "共用多个数字",
                              "nothing_new": nothing_new})
    return found


async def judge(client, first, second: dict[str, Any], nothing_new: bool = False) -> dict[str, Any]:
    """``first`` is the earlier page, or every earlier page in the window when the later one
    has nothing new: restating two pages at once is not new because neither page alone held it all."""
    def view(content):
        return {"title": content.get("title"), "highlight": content.get("highlight"),
                "items": [{key: item.get(key) for key in ("heading", "value", "body")} for item in content.get("items") or []],
                "takeaway": content.get("takeaway")}

    try:
        reply = await client.complete_json(task="content_overlap", system=JUDGE_SYSTEM,
                                           user=json.dumps({"earlier": [view(page) for page in first] if isinstance(first, list) else view(first),
                                            "later": view(second),
                                            "nothing_new": nothing_new}, ensure_ascii=False),
                                           max_output_tokens=400, context={})
    except Exception:  # noqa: BLE001 - an unanswered question is not a finding
        return {"same": False, "why": "未判断"}
    return {"same": bool(reply.get("same")) if isinstance(reply, dict) else False,
            "why": str((reply or {}).get("why") or "")[:60] if isinstance(reply, dict) else ""}


def to_rewrite(pair: list[int], contents: dict[int, dict[str, Any]], content_plan: dict[int, dict[str, Any]],
               fixed: set[int]) -> int | None:
    """Which page of a repeated pair is rewritten: one not signed off, preferring the page whose
    focal figure is not the lead its plan gave it, else the later page."""

    open_pages = [page for page in pair if page not in fixed]
    if not open_pages:
        return None

    def strays(page):
        lead = figures((content_plan.get(page) or {}).get("lead"))
        focal = figures((contents[page].get("highlight") or {}).get("value"))
        return bool(lead) and bool(focal) and not (lead & focal)

    straying = [page for page in open_pages if strays(page)]
    return (straying or open_pages)[-1]


def instruction(page: int, other: int, contents: dict[int, dict[str, Any]],
                content_plan: dict[int, dict[str, Any]], why: str) -> str:
    own = content_plan.get(page) or {}
    taken = contents[other]
    return (f"本页和第 {other} 页在讲同一个结论（{why}）。第 {other} 页已经讲了：「{taken.get('title')}」。"
            f"本页改为讲策划给它的观点：「{own.get('claim') or '本页证据里别页没讲的那一面'}」，"
            f"领衔事实：「{own.get('lead') or '本页证据里别页没用过的数字'}」；新增信息：「{own.get('increment') or ''}」。"
            f"第 {other} 页的焦点数字在本页最多作为背景出现一次，不能做标题或焦点。")


async def review(client, checkpoints, contents: dict[int, dict[str, Any]], content_plan: dict[int, dict[str, Any]],
                 fixed: set[int], *, rewritten: dict[str, Any] | None = None) -> dict[str, Any]:
    """Suspect pairs judged by a model; which pages to rewrite, and with what instruction.

    Judgements are cached by the two pages' text, so a resumed run asks only about pages that
    changed. With ``rewritten`` (the first review), the pairs still repeated after the rewrite
    are reported as ``left`` and nothing more is rewritten.
    """

    import asyncio
    import hashlib

    async def judged(pair):
        earlier, later = pair["pages"]
        second = contents[later]
        first = ([contents[page] for page in sorted(contents) if 0 < later - page <= WINDOW]
                 if pair.get("nothing_new") else contents[earlier])
        text = "\x00".join(_text(page) for page in (first if isinstance(first, list) else [first]))
        key = hashlib.sha1((text + "\x00" + _text(second) + str(pair.get("nothing_new"))).encode("utf-8")).hexdigest()[:12]
        name = f"typeset/overlap/{key}.json"
        saved = checkpoints.load(name)
        if saved is None:
            saved = await judge(client, first, second, pair.get("nothing_new", False))
            checkpoints.save(name, saved)
        return {**pair, **saved}

    async def decided(pair):
        roles = {(content_plan.get(page) or {}).get("role") for page in pair["pages"]}
        if pair["why"] == "焦点数字相同" and not roles & {"summary", "judgement"}:
            # Two pages leading with one figure is what the plan forbids; no judgement needed.
            return {**pair, "same": True, "by": "rule"}
        return await judged(pair)

    found = await asyncio.gather(*(decided(pair) for pair in suspects(contents)))
    repeated = [pair for pair in found if pair["same"]]
    if rewritten is not None:
        return {**rewritten, "after": found, "left": [pair["pages"] for pair in repeated]}
    rewrite: dict[int, str] = {}
    for pair in repeated:
        page = to_rewrite(pair["pages"], contents, content_plan, fixed)
        if page is None or page in rewrite:
            continue
        other = pair["pages"][0] if page == pair["pages"][1] else pair["pages"][1]
        rewrite[page] = instruction(page, other, contents, content_plan, pair["why"])
    return {"suspects": found, "repeated": [pair["pages"] for pair in repeated],
            "rewrite": {page: text for page, text in sorted(rewrite.items())}}


def thin(content_plan: dict[int, dict[str, Any]], packets: dict[int, Any]) -> list[int]:
    """Pages with too little of their own to say: marked thin by the planner, or owning fewer than
    two figures while their evidence has figures to own (CNNIC p3-6: one figure each)."""

    found = []
    judged: list[int] = []
    for page, entry in sorted(content_plan.items()):
        has_figures = len(figures(getattr(packets.get(page), "text", "") or "")) >= 2
        if entry.get("role") in NARRATIVE and narrative_ok(page, entry, content_plan, packets, judged):
            if entry.get("role") in ("summary", "judgement"):
                judged.append(page)
            continue
        if entry.get("role") in ("thin",) + NARRATIVE or (has_figures and len(owned_figures(entry)) < 2):
            found.append(page)
    return found


def narrative_ok(page: int, entry: dict[str, Any], content_plan: dict[int, dict[str, Any]],
                 packets: dict[int, Any], judged: list[int]) -> bool:
    """Whether a page's narrative task stands up: a judgement draws on two other pages and is not next to
    another summary or judgement; a visual page names a figure its evidence contains; any of them has a
    claim of its own."""

    claim = (entry.get("claim") or "").strip()
    if not claim or any(claim == (other.get("claim") or "").strip() for number, other in content_plan.items() if number != page):
        return False
    if any(abs(page - other) <= 1 for other in judged):
        return False  # two summarising pages in a row retell each other
    role = entry.get("role")
    if role == "judgement":
        return len([number for number in entry.get("draws_on") or [] if number in content_plan and number != page]) >= 2
    if role == "visual":
        label = re.match(r"\s*(图\s*\d+)", entry.get("visual") or "")
        text = getattr(packets.get(page), "text", "") or ""
        return bool(label) and re.sub(r"\s+", "", label.group(1)) in re.sub(r"\s+", "", text)
    return True  # summary: the outline made it one
