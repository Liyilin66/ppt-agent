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
    "- 只有当候选项直接证明这句结论时才用它。资料里有数据不是画图的理由；候选项讲的是别的事，就选 none。\n"
    "- role 为 primary：候选项画的就是 core_metric 本身（同一个指标、同一个口径），它应当成为这一页的主视觉。\n"
    "- role 为 supporting：候选项画的是另一个指标，但它是这句结论的直接论据。这时页面的第一眼仍然是 core_metric 的数字，图只占小部分。\n"
    "- 标题强调的是指标 A，候选项画的是指标 B，而 B 只是相关、并不是论据：选 none。\n"
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
