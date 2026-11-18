"""Decide what a page shows, from what the page claims.

Data that could be drawn is not a reason to draw it. For each page that has
something available - a verified series, a figure of the source, a figure that
can be redrawn - the page's claim and the measure it rests on are named first,
and only then is it decided whether any of those things shows that measure.
The model judges; the rules keep it to what was offered and to one use each.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

BRIEF_SYSTEM = (
    "你是演示文稿的编辑。下面是一页的内容，以及资料里可以放到这一页上的东西（核对过的数据序列、资料原图、可按原样重画的图）。"
    "请先说清这一页的论点和它依赖的核心指标，再决定这一页要不要用其中一样，用哪一样。\n"
    "规则：\n"
    "- claim：这一页要读者记住的一句结论。core_metric：这句结论靠哪个指标成立（例如「全球AI投融资金额」），没有数字指标就写它讲的对象。\n"
    "- 三种结果，按这个顺序判断：\n"
    "  primary（主视觉）：候选项画的就是 core_metric 本身，它应当成为这一页的主视觉。\n"
    "  supporting（辅助论据）：候选项画的是另一个指标，但它真实、明确地支持这句结论——典型的情况是这一页的条目里已经把它"
    "当作论据写出来了（它的数值出现在 items 里），或者它是结论成立的原因、表现之一。这时用它，页面的第一眼仍然是 core_metric 的数字，"
    "图只占小部分。不要因为它和 core_metric 不是同一个指标就拒绝。\n"
    "  none（不使用）：候选项和这句结论没有论证关系，只是话题相近，或者页面内容里根本没有提到它。资料里有数据不是画图的理由。\n"
    "- 同时有几个候选项时只选最能证明结论的一个。可以重画的图优先于原图；看不清的原图不会出现在候选里。\n"
    "- focus：读者第一眼应该看到的那个数字或图形（从这一页的内容里选，不要新造）。\n"
    '只输出 JSON：{"claim":"","core_metric":"","use":"候选项的 id 或 none","role":"primary 或 supporting","focus":"","reason":"不超过40个字"}'
)


def _digest(content: dict[str, Any]) -> dict[str, Any]:
    return {"title": content.get("title"), "highlight": content.get("highlight"), "lead": content.get("lead"),
            "takeaway": content.get("takeaway"), "relation": content.get("relation"),
            "items": [{key: item.get(key) for key in ("heading", "value", "body") if item.get(key)}
                      for item in content.get("items") or []]}


async def decide(client: Any, checkpoints: Any, page: int, content: dict[str, Any],
                 candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """{"claim", "core_metric", "use", "role", "focus", "reason"}; ``use`` is a candidate id or "none"."""

    nothing = {"claim": content.get("title") or "", "core_metric": "", "use": "none", "role": "primary", "focus": "", "reason": ""}
    if not candidates:
        return nothing
    offered = [{key: value for key, value in candidate.items() if key != "payload"} for candidate in candidates]
    asked = json.dumps({"page": _digest(content), "candidates": offered}, ensure_ascii=False, sort_keys=True)
    name = f"briefs/page_{page:03d}_{hashlib.sha1(asked.encode('utf-8')).hexdigest()[:10]}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    try:
        reply = await client.complete_json(task="visual_brief", system=BRIEF_SYSTEM, user=asked,
                                           max_output_tokens=600, context={"page": page})
    except Exception as error:  # noqa: BLE001 - a page without a visual is still a page
        return {**nothing, "reason": "未能判断：" + str(error)[:60]}
    if not isinstance(reply, dict):
        return nothing
    use = str(reply.get("use") or "none")
    if use not in {candidate["id"] for candidate in candidates}:
        use = "none"  # only what was offered may be used
    result = {"claim": str(reply.get("claim") or content.get("title") or "")[:80],
              "core_metric": str(reply.get("core_metric") or "")[:40], "use": use,
              "role": "supporting" if reply.get("role") == "supporting" else "primary",
              "focus": str(reply.get("focus") or "")[:40], "reason": str(reply.get("reason") or "")[:80]}
    if checkpoints is not None:
        checkpoints.save(name, result)
    return result


SUPPORT_SYSTEM = (
    "你是审稿人，专门找图表和论点之间的脱节。下面是一页幻灯片的论点和页面内容，以及准备放在这一页上的一组数据或一张图。"
    "判断它是不是这句论点的证据：读者看了它，论点会更可信——它衡量的就是论点说的那件事，或者是那件事的组成部分、"
    "原因或结果，而且这层关系在页面内容里写明了。同一章节、同一话题、同一份报告里的另一组数据，不是证据；"
    "只是页面某一条附带提到它，也不够。拿不准时判 false。"
    '只输出 JSON：{"supports":true 或 false,"why":"不超过40个字"}'
)


async def supports(client: Any, checkpoints: Any, page: int, content: dict[str, Any],
                   candidate: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    """A second, separate judgement that the chosen visual evidences the claim (CAICT p11: a chart of
    PyTorch's share beside "54 days, 466 interruptions" was chosen as supporting)."""

    shown = {key: value for key, value in candidate.items() if key not in ("payload", "id")}
    asked = json.dumps({"claim": brief.get("claim"), "page": _digest(content), "visual": shown},
                       ensure_ascii=False, sort_keys=True)
    name = f"briefs/support_{page:03d}_{hashlib.sha1(asked.encode('utf-8')).hexdigest()[:10]}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    try:
        reply = await client.complete_json(task="visual_support", system=SUPPORT_SYSTEM, user=asked,
                                           max_output_tokens=300, context={"page": page})
    except Exception as error:  # noqa: BLE001 - unasked is not supported
        return {"supports": False, "why": "未能核对：" + str(error)[:40]}
    result = {"supports": bool(reply.get("supports")) if isinstance(reply, dict) else False,
              "why": str((reply or {}).get("why") or "")[:60] if isinstance(reply, dict) else ""}
    if checkpoints is not None:
        checkpoints.save(name, result)
    return result


def settle(briefs: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Each thing is shown once in a deck: where it is the main visual before
    where it only supports, and on the earlier page when that does not decide it."""

    taken: set[str] = set()
    settled = {page: dict(brief) for page, brief in briefs.items()}
    for page in sorted(settled, key=lambda number: (settled[number]["role"] != "primary", number)):
        use = settled[page]["use"]
        if use == "none":
            continue
        if use in taken:
            settled[page].update(use="none", reason="同一份数据或图已经用在前面的页上")
        else:
            taken.add(use)
    return settled


RELEVANCE_SYSTEM = (
    "你是审稿人。下面是一页幻灯片的论点和它的条目。逐条判断：这一条是不是这句论点的证据或必要说明？"
    "只是同一章节、同一份报告里的另一组数据，或者和论点没有论证关系、只是把版面填满的条目，判为不相关。"
    "拿不准时判为相关。"
    '只输出 JSON：{"unrelated":[条目序号，从 1 开始],"why":"不超过40个字"}'
)


async def unrelated_items(client: Any, checkpoints: Any, page: int, content: dict[str, Any], claim: str) -> dict[str, Any]:
    """Items that do not evidence the page's claim (CNNIC 57 p23: the gender split under a page about AI
    apps as the main entry). Cached by the page's text."""

    items = [{key: item.get(key) for key in ("heading", "value", "body") if item.get(key)}
             for item in content.get("items") or []]
    if len(items) < 2:
        return {"unrelated": [], "why": ""}
    asked = json.dumps({"claim": claim or content.get("title"), "title": content.get("title"),
                        "highlight": content.get("highlight"), "items": items}, ensure_ascii=False, sort_keys=True)
    name = f"briefs/relevance_{page:03d}_{hashlib.sha1(asked.encode('utf-8')).hexdigest()[:10]}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    try:
        reply = await client.complete_json(task="item_relevance", system=RELEVANCE_SYSTEM, user=asked,
                                           max_output_tokens=300, context={"page": page})
    except Exception as error:  # noqa: BLE001 - unjudged items stay; the failure is recorded
        return {"unrelated": [], "why": "未能核对：" + str(error)[:40], "failed": True}
    picked = reply.get("unrelated") if isinstance(reply, dict) else None
    result = {"unrelated": sorted({index for index in picked or [] if isinstance(index, int) and 1 <= index <= len(items)}),
              "why": str((reply or {}).get("why") or "")[:60] if isinstance(reply, dict) else ""}
    if len(result["unrelated"]) >= len(items):
        result["unrelated"] = result["unrelated"][:-1]  # a page keeps at least one item
    if checkpoints is not None:
        checkpoints.save(name, result)
    return result
