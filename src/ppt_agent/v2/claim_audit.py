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
import re
from typing import Any

SYSTEM = """你是严格的事实编辑，审查一页演示文稿的论点和证据是否一致。只依据给你的本页内容和本页证据原文，不用常识补充。

逐项检查：
1. 本页的核心论点是什么（标题加导语）。
2. 页面上每个数字或事实：直接支撑论点（direct）；和论点讲同一对象、作为独立观察并列（independent）；
   讲的是别的对象，不能支撑本页论点（unrelated）。两个事实都和"AI""互联网""用户"有关，不等于相互支撑。
3. 页面上的比较、增长、提升、领先、提速、加快、带动、导致、推动、因为等词：本页或本页证据里有没有支撑它的数据
   （例如"提速"需要前后两期的增量对比；"领先"需要同类的比较对象）。没有就是 unsupported。
4. 结论或建议是否超出页面证据（推断可以有，但不能被说成数据结论）。

5. 标题或导语是否把两个不同对象的结论拼在一起（例如"短视频拿走最多新增注意力，AI需求仍以问答为主"：
   短视频时长和 AI 使用目的是两件事，互不支撑）。拼在一起的那句列进 mixed，各自对象的事实都标 unrelated 于另一个。

不算问题（不要列出）：评价性形容词（大、超大、较大、明显、突出）、建议和展望（建议、应、可、值得关注）、
"仍""持续"这类说法在有当期数据时的用法。只列出把数据关系说错或说过头的地方：说了提速/加快/领先/最高/增长/下降/带动/导致，
而本页和证据里没有相应的对比数据或因果说明。

只返回 JSON：
{"claim": "核心论点", "facts": [{"text": "页面上的事实（原话）", "relation": "direct|independent|unrelated", "why": "一句话"}],
 "unsupported": [{"text": "页面上的原话", "why": "缺什么证据"}],
 "mixed": [{"text": "标题或导语里拼接两个对象的原话", "why": "哪两个对象"}],
 "verdict": "ok" 或 "fix",
 "instruction": "verdict 为 fix 时：具体怎么改（删掉/替换哪条事实，哪句话改成什么准确说法）；ok 时为空"}
有 unrelated 或 unsupported 时 verdict 必须是 fix。independent 本身不是问题。"""

import asyncio as _asyncio

_GATE = _asyncio.Semaphore(6)  # thirty reviews at once came back with errors on twelve

FIELDS = ("title", "lead", "highlight", "items", "metrics", "insights", "takeaway", "statement", "conclusion",
          "categories", "values", "rows", "left", "right", "steps", "milestones", "series")


def view(content: dict[str, Any]) -> dict[str, Any]:
    """What a reader sees of the page; sources and notes left out."""

    return {key: content[key] for key in FIELDS if content.get(key) not in (None, "", [], {})}


def _key(content: dict[str, Any], evidence: str) -> str:
    text = VERSION + json.dumps(view(content), ensure_ascii=False, sort_keys=True) + "\x00" + (evidence or "")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


async def audit_page(client: Any, checkpoints: Any, number: int, content: dict[str, Any], evidence: str) -> dict[str, Any]:
    name = f"typeset/claim_audit/{_key(content, evidence)}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    user = json.dumps({"page": number, "content": view(content), "evidence": (evidence or "")[:7000]}, ensure_ascii=False)
    import asyncio

    for attempt in range(3):
        try:
            async with _GATE:
                verdict = await client.complete_json(task="claim_audit", system=SYSTEM, user=user, max_output_tokens=2500)
            break
        except Exception as exc:  # noqa: BLE001 - an audit that cannot run is reported, never passed
            if attempt == 2:
                return {"verdict": "error", "error": str(exc)[:300]}
            await asyncio.sleep(4 * (attempt + 1))
    verdict = normalise(verdict)
    if checkpoints is not None:
        checkpoints.save(name, verdict)
    return verdict


def normalise(verdict: Any) -> dict[str, Any]:
    if not isinstance(verdict, dict):
        return {"verdict": "error", "error": "not an object"}
    facts = [fact for fact in verdict.get("facts") or [] if isinstance(fact, dict)]
    unsupported = [item for item in verdict.get("unsupported") or [] if isinstance(item, dict)]
    mixed = [item for item in verdict.get("mixed") or [] if isinstance(item, dict) and item.get("text")]
    failing = any(fact.get("relation") == "unrelated" for fact in facts) or bool(unsupported) or bool(mixed)
    return {"claim": verdict.get("claim") or "", "facts": facts, "unsupported": unsupported, "mixed": mixed,
            # The rule decides, not the reviewer's own word: an unrelated fact or unsupported wording is a fix.
            "verdict": "fix" if failing else "ok",
            "instruction": (verdict.get("instruction") or "").strip() if failing else ""}


MEASURED_CLAIM = (r"提速|加速|加快|放缓|领先|领跑|居首|首位|第一|最高|最快|最大|最多|最低|翻倍|倍增|超过|高于|低于|"
                  r"回升|反弹|下降|下滑|增速|增幅|带动|导致|由于|因为|拉动|驱动")
CLAIM_FIELDS = ("title", "lead", "highlight", "statement", "takeaway")  # where the page states what it argues
VERSION = "4"  # the reviewer's brief; a change asks every page again


def _squash(text: Any) -> str:
    return "".join(str(text or "").split())


def _flat(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flat(part) for part in value.values())
    if isinstance(value, list):
        return " ".join(_flat(part) for part in value)
    return str(value or "")


def _figures(text: str) -> set[str]:
    """The figures of a text, without the dates every line carries ("2025年12月")."""

    found = set(re.findall(r"\d+(?:\.\d+)?", text))
    return {n for n in found if not (re.fullmatch(r"(19|20)\d\d", n) or (n.isdigit() and int(n) <= 12))}


def _score(text: str, value: Any) -> float:
    """How well a finding quotes this part of the page: 1 when one holds the other, else the share of the
    finding's word pairs found in it - provided the finding's figures are all there."""

    a, b = _squash(text), _squash(_flat(value))
    if not a or not b:
        return 0.0
    if a in b or (len(b) >= 6 and b in a):
        return 1.0
    figures = _figures(a)
    if figures and not figures <= _figures(b):
        return 0.0
    pairs = [a[index:index + 2] for index in range(len(a) - 1)]
    share = sum(pair in b for pair in pairs) / max(1, len(pairs))
    return share if (figures or share >= 0.6) and share >= 0.3 else 0.0


def _in(text: str, value: Any) -> bool:
    return _score(text, value) > 0


def _where(text: str, claim: dict[str, Any], items: list[Any]) -> tuple[str, int | None]:
    """The part of the page a finding quotes: ("claim", None), ("item", index) or ("", None)."""

    places = [(_score(text, value), "claim", None) for value in claim.values()]
    places += [(_score(text, item), "item", index) for index, item in enumerate(items)]
    best = max(places, key=lambda place: place[0], default=(0.0, "", None))
    return (best[1], best[2]) if best[0] > 0 else ("", None)


def decide(verdict: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """What a finding means for the page. Only the claim is rewritten: an unrelated fact or an unsupported
    comparison in the title, lead or focal figure. An unrelated fact among the items moves to the notes
    (at least two items stay). Anything else is reported, not acted on - wording is not worth a new page."""

    claim = {field: content.get(field) for field in CLAIM_FIELDS if content.get(field)}
    items = content.get("items") or []
    rewrite, move, minor = [], [], []
    for fact in verdict.get("facts") or []:
        if fact.get("relation") != "unrelated":
            continue
        place, index = _where(fact.get("text"), claim, items)
        if place == "claim":
            rewrite.append({"unrelated": fact})
        elif place == "item":
            move.append(index)
        else:
            minor.append({"unrelated": fact})
    for item in verdict.get("unsupported") or []:
        # In the claim, only a comparison, rank, speed or cause the data must carry ("明显提速", "增长最快",
        # "带动") is rewritten; an interpretive headline ("夯实底座", "转向场景") is the page's reading of
        # its data and is reported, not rewritten.
        measured = re.search(MEASURED_CLAIM, str(item.get("text") or ""))
        place, _ = _where(item.get("text"), claim, items)
        (rewrite if place == "claim" and measured else minor).append({"unsupported": item})
    for item in verdict.get("mixed") or []:
        # A claim joining two subjects is rewritten wherever it stands in the claim.
        place, _ = _where(item.get("text"), claim, items)
        (rewrite if place == "claim" else minor).append({"mixed": item})
    move = sorted(set(move))
    while move and len(items) - len(move) < 2:
        minor.append({"unrelated_kept": items[move.pop()]})
    return {"rewrite": rewrite, "move_items": move, "minor": minor}


def instruction(decision: dict[str, Any], taken: list[str] | None = None) -> str:
    lines = ["终审发现本页的论点（标题、导语或焦点数字）和证据不一致，按下面修改后重写这一页（数字仍然必须来自证据原文）："]
    for finding in decision.get("rewrite") or []:
        if "unrelated" in finding:
            fact = finding["unrelated"]
            lines.append(f"· 「{fact.get('text')}」讲的是别的对象，不能支撑本页论点（{fact.get('why')}）。"
                         "换成证据里与论点同一对象的事实；不要只改措辞把它留下。")
        elif "mixed" in finding:
            item = finding["mixed"]
            lines.append(f"· 「{item.get('text')}」把两个对象拼成一个论点（{item.get('why')}）。本页只讲一个对象："
                         "保留和标题同一对象的事实，另一个对象的事实删掉；不要只改措辞把它留下。")
        else:
            item = finding["unsupported"]
            lines.append(f"· 「{item.get('text')}」缺少证据（{item.get('why')}）。有对比数据就把它放到页面上；没有就改成准确的说法。")
    if taken:
        lines.append("· 这些数字别的页已经讲过，本页不要用来补位：" + "、".join(taken[:20]) + "。")
    lines.append("本页的主题（标题讲的对象）不变，不要改讲证据里别的话题；其他条目和数字没有问题，保持不变。")
    return "\n".join(lines)


def summary(verdicts: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {"pages": len(verdicts),
            "ok": sorted(n for n, v in verdicts.items() if v.get("verdict") == "ok"),
            "fix": sorted(n for n, v in verdicts.items() if v.get("verdict") == "fix"),
            "error": sorted(n for n, v in verdicts.items() if v.get("verdict") == "error"),
            "error_messages": {str(n): v.get("error") for n, v in verdicts.items() if v.get("verdict") == "error"},
            "findings": {str(n): {key: v.get(key) for key in ("claim", "unsupported", "instruction")}
                         | {"unrelated": [f for f in v.get("facts") or [] if f.get("relation") == "unrelated"]}
                         for n, v in sorted(verdicts.items()) if v.get("verdict") != "ok"}}


REPHRASE = """你是严格的事实编辑。这一页的论点（标题、导语、结论句）被审稿指出了问题。只改标题、导语和结论句，让它们准确表述页面上已有的事实：
- 只能用页面上已经出现的数字和事实，不新增任何数字，不改讲别的话题，标题讲的对象不变；
- 删去没有数据支撑的比较、增速、排名、因果说法，或改成页面数据能直接支持的说法；
- 不要把两个对象拼成一个论点：标题讲一个对象；
- 结论句可以是基于页面事实的判断或建议，但不能提到页面上没有数据的对象；
- 标题不超过 26 个字，导语不超过 40 个字，结论句不超过 60 个字。
只返回 JSON：{"title": "...", "lead": "...", "takeaway": "..."}"""


def figures_of(text: Any) -> set[str]:
    return {re.sub(r"\s+", "", match) for match in re.findall(r"\d+(?:\.\d+)?\s*(?:%|％|亿|万|个|款|人|倍|小时|GB)?", _flat(text))}


def _bare(figures: set[str]) -> set[str]:
    return {re.match(r"\d+(?:\.\d+)?", figure).group() for figure in figures if re.match(r"\d+(?:\.\d+)?", figure)}


async def rephrase(client: Any, checkpoints: Any, number: int, content: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """REPHRASE_CLAIM: a new title and lead for the findings, using only the page's own figures.
    Cached by its inputs, so a replay gets the same words. Returns {} when the proposal adds a figure."""

    body = {key: value for key, value in view(content).items() if key not in ("title", "lead", "takeaway")}
    findings = instruction(decision)
    key = hashlib.sha1((VERSION + json.dumps([view(content), findings], ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:16]
    name = f"typeset/claim_repair/{key}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is None:
        try:
            saved = await client.complete_json(task="claim_rephrase", system=REPHRASE, max_output_tokens=800,
                                               user=json.dumps({"page": number, "title": content.get("title"),
                                                                "lead": content.get("lead"), "takeaway": content.get("takeaway"),
                                                                "page_facts": body,
                                                                "findings": findings}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            saved = {"error": str(exc)[:300]}
        if checkpoints is not None:
            checkpoints.save(name, saved)
    title, lead = str(saved.get("title") or "").strip(), str(saved.get("lead") or "").strip()
    takeaway = str(saved.get("takeaway") or "").strip()
    if not title:
        return {}
    allowed = _bare(figures_of(body)) | _bare(figures_of(content.get("highlight")))
    if not _bare(figures_of([title, lead, takeaway])) <= allowed:
        return {}  # a new figure: not a rephrasing
    proposal = {"title": title[:40], "lead": lead[:60] or None}
    if takeaway:
        proposal["takeaway"] = takeaway[:70]
    return proposal


def content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(view(content), ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
