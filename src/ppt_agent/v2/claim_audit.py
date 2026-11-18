"""The last look at what each page argues, once its content is final (P4-Fix7).

Earlier checks judge one thing at a time: a figure against the source, a chart against the claim, a
page against its neighbours. Recovery, overlap rewrites and removals then change pages after those
checks ran, and a page can come out with true figures that do not belong together (CNNIC 57 round
6, p21: 短视频 94.3 亿小时 beside "AI 使用目的：回答问题 76.0%", two subjects, neither supporting the
other) or a claim its figures do not carry (p28: "新增明显提速" over cumulative totals only).

Each page is read by a reviewer against its own evidence: what it claims, which figure supports
what, which comparison or causal words lack evidence on the page. A page with an unrelated fact or
an unsupported word is rewritten once with that finding; if it still fails it is reported for
review. Verdicts are cached by the page's text and evidence, so an unchanged page is not asked again.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SYSTEM = """你是严格的事实编辑，审查一页演示文稿的论点和证据是否一致。只依据给你的本页内容和本页证据原文，不用常识补充。

逐项检查：
1. 本页的核心论点是什么（标题加导语）。
2. 页面上每个数字或事实：直接支撑论点（direct）；和论点讲同一对象、作为独立观察并列（independent）；
   讲的是别的对象，不能支撑本页论点（unrelated）。两个事实都和"AI""互联网""用户"有关，不等于相互支撑。
3. 页面上的比较、增长、提升、领先、提速、加快、带动、导致、推动、因为等词：本页或本页证据里有没有支撑它的数据
   （例如"提速"需要前后两期的增量对比；"领先"需要同类的比较对象）。没有就是 unsupported。
4. 结论或建议是否超出页面证据（推断可以有，但不能被说成数据结论）。

只返回 JSON：
{"claim": "核心论点", "facts": [{"text": "页面上的事实", "relation": "direct|independent|unrelated", "why": "一句话"}],
 "unsupported": [{"text": "页面上的原话", "why": "缺什么证据"}],
 "verdict": "ok" 或 "fix",
 "instruction": "verdict 为 fix 时：具体怎么改（删掉/替换哪条事实，哪句话改成什么准确说法）；ok 时为空"}
有 unrelated 或 unsupported 时 verdict 必须是 fix。independent 本身不是问题。"""

FIELDS = ("title", "lead", "highlight", "items", "metrics", "insights", "takeaway", "statement", "conclusion",
          "categories", "values", "rows", "left", "right", "steps", "milestones", "series")


def view(content: dict[str, Any]) -> dict[str, Any]:
    """What a reader sees of the page; sources and notes left out."""

    return {key: content[key] for key in FIELDS if content.get(key) not in (None, "", [], {})}


def _key(content: dict[str, Any], evidence: str) -> str:
    text = json.dumps(view(content), ensure_ascii=False, sort_keys=True) + "\x00" + (evidence or "")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


async def audit_page(client: Any, checkpoints: Any, number: int, content: dict[str, Any], evidence: str) -> dict[str, Any]:
    name = f"typeset/claim_audit/{_key(content, evidence)}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    user = json.dumps({"page": number, "content": view(content), "evidence": (evidence or "")[:7000]}, ensure_ascii=False)
    try:
        verdict = await client.complete_json(task="claim_audit", system=SYSTEM, user=user, max_output_tokens=2500)
    except Exception as exc:  # noqa: BLE001 - an audit that cannot run is reported, never passed
        return {"verdict": "error", "error": str(exc)[:300]}
    verdict = normalise(verdict)
    if checkpoints is not None:
        checkpoints.save(name, verdict)
    return verdict


def normalise(verdict: Any) -> dict[str, Any]:
    if not isinstance(verdict, dict):
        return {"verdict": "error", "error": "not an object"}
    facts = [fact for fact in verdict.get("facts") or [] if isinstance(fact, dict)]
    unsupported = [item for item in verdict.get("unsupported") or [] if isinstance(item, dict)]
    failing = any(fact.get("relation") == "unrelated" for fact in facts) or bool(unsupported)
    return {"claim": verdict.get("claim") or "", "facts": facts, "unsupported": unsupported,
            # The rule decides, not the reviewer's own word: an unrelated fact or unsupported wording is a fix.
            "verdict": "fix" if failing else "ok",
            "instruction": (verdict.get("instruction") or "").strip() if failing else ""}


def instruction(verdict: dict[str, Any]) -> str:
    lines = ["终审发现本页的论点和证据不一致，按下面修改后重写这一页（数字仍然必须来自证据原文）："]
    for fact in verdict.get("facts") or []:
        if fact.get("relation") == "unrelated":
            lines.append(f"· 「{fact.get('text')}」讲的是别的对象，不能支撑本页论点（{fact.get('why')}）。删掉或换成证据里与论点同一对象的事实；"
                         "不要只改措辞把它留下。")
    for item in verdict.get("unsupported") or []:
        lines.append(f"· 「{item.get('text')}」缺少证据（{item.get('why')}）。有对比数据就把它放到页面上；没有就改成准确的说法。")
    if verdict.get("instruction"):
        lines.append("· 审稿建议：" + verdict["instruction"])
    return "\n".join(lines)


def summary(verdicts: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {"pages": len(verdicts),
            "ok": sorted(n for n, v in verdicts.items() if v.get("verdict") == "ok"),
            "fix": sorted(n for n, v in verdicts.items() if v.get("verdict") == "fix"),
            "error": sorted(n for n, v in verdicts.items() if v.get("verdict") == "error"),
            "findings": {str(n): {key: v.get(key) for key in ("claim", "unsupported", "instruction")}
                         | {"unrelated": [f for f in v.get("facts") or [] if f.get("relation") == "unrelated"]}
                         for n, v in sorted(verdicts.items()) if v.get("verdict") != "ok"}}
