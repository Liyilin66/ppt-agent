"""Layout IR: the model plans the page's structure, the program lays it out (experimental, P4-Fix4).

The HTML engine asks the model for a finished page and then repairs what does not fit; most first
attempts fail on space (regions overlapping, text taller than its rows). Here the model describes
the page instead - nested rows and columns with width weights, and leaves that name what they show
(the title, the focal figure, an item, a list of items, the chart, a proportion bar, the closing
line) with a priority - and the program writes the HTML. Leaves sit in normal flow inside flex
containers, so they cannot overlap; a figure and its unit are one piece; charts and bars are drawn
from verified data, never from widths the model chose. The page is measured; if it overflows, gaps
close, display figures step down, and the lowest-priority leaves move to the notes, in that order.
If it still does not fit, the page is reported infeasible rather than squeezed.

Enabled with ``PPT_AGENT_LAYOUT_IR=1`` for runs begun under the P4 rules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape
from typing import Any

from ppt_agent.v2.htmldeck import charts, graphic_checks
from ppt_agent.v2.htmldeck.checks import Defect, allowed_numbers, find_defects

GAPS = {"l": 28, "m": 18, "s": 10}
STEP = {"d1": "d2", "d2": "d3", "d3": "d4", "d4": "d4", "t1": "t2", "t2": "t2"}
LEAVES = ("kicker", "title", "figure", "text", "items", "chart", "bar", "call", "lead")

IR_SYSTEM = (
    "你是演示文稿的版式设计师。不要写 HTML。为下面这一页输出页面结构（JSON），程序会按它排版、测量并生成页面。\n"
    "结构是一棵树：容器 {\"dir\":\"row\" 或 \"col\",\"weights\":[每个子节点的宽度权重，仅 row 需要],\"gap\":\"l|m|s\","
    "\"align\":\"start|center|end\",\"children\":[...]}；叶子 {\"leaf\":类型,\"ref\":引用,\"size\":字号,\"priority\":1|2|3,\"accent\":true|false}。\n"
    "叶子类型：kicker（章节标签）、title（页面标题，size t1 或 t2）、figure（大数字及其说明，ref 为 highlight 或 item:N，"
    "size d1–d4；数字和单位由程序合成一体）、text（一条事实，ref 为 item:N）、items（若干条事实排成账本，ref 为 [\"item:2\",\"item:3\"]）、"
    "chart（核对过的数据图，ref 为 chart；只有内容里有 series 时才能用）、bar（比例条，ref 为 item:N 或 highlight，只能用于占比）、"
    "call（结论条，ref 为 takeaway）、lead（导语，ref 为 lead）。\n"
    "要求：根节点是 col；第一个子节点放 kicker；标题必须出现一次；每条事实最多出现一次；焦点（focus）只有一个，用最大的字号；"
    "priority 1 是这页不能少的（标题、焦点、图表），3 是放不下时可以挪到备注的；构图要表达内容的关系"
    "（比较就并排、递进就纵向、主次就宽窄不同），不要每页都是左右两栏；宽度权重决定主次，不必对称。\n"
    '只输出 JSON：{"focus":"读者第一眼看到什么","reading_path":"视线顺序","root":{...}}'
)


@dataclass
class State:
    gap_shrink: int = 0                      # 0 as planned, 1 one step tighter, 2 tightest
    step: int = 0                            # display figures stepped down this many times
    dropped: list[str] = field(default_factory=list)  # leaf refs moved to the notes


def _refs(content: dict[str, Any]) -> dict[str, Any]:
    items = content.get("items") or []
    found: dict[str, Any] = {f"item:{index}": item for index, item in enumerate(items, 1)}
    found.update({"highlight": content.get("highlight"), "takeaway": content.get("takeaway"),
                  "lead": content.get("lead"), "chart": charts.chartable(content)})
    return found


def _split(value: str) -> tuple[str, str]:
    match = re.match(r"^([约超近]?[\d,.]+(?:[\-—→][\d,.]+)?)(.*)$", str(value or "").strip())
    return (match.group(1), match.group(2)) if match else (str(value or ""), "")


def _size(size: str, state: State) -> str:
    for _ in range(state.step):
        size = STEP.get(size, size)
    return size


def _leaf(node: dict[str, Any], job: Any, state: State, refs: dict[str, Any]) -> str:
    kind, ref = node.get("leaf"), node.get("ref")
    if isinstance(ref, str) and ref in state.dropped:
        return ""
    accent = " red" if node.get("accent") else ""
    if kind == "kicker":
        return f'<div class="kicker">{escape(job.kicker or "")}</div>' if job.kicker else ""
    if kind == "title":
        return f'<div class="{node.get("size") if node.get("size") in ("t1", "t2") else "t2"}">{escape(job.title or "")}</div>'
    if kind == "figure":
        source = refs.get(ref) or {}
        value = source.get("value")
        if not value:
            return ""
        number, unit = _split(value)
        size = _size(node.get("size") if node.get("size") in ("d1", "d2", "d3", "d4") else "d3", state)
        words = source.get("text") or source.get("heading") or ""
        body = source.get("body") if ref != "highlight" else ""
        if len(unit) <= 2:
            # "80.1%", "11.25亿": a short unit is part of the figure and keeps its type.
            number, unit = number + unit, ""
        return (f'<div class="rowx base"><div class="{size}{accent}">{escape(number)}</div>'
                + (f'<div class="unit">{escape(unit)}</div>' if unit else "") + "</div>"
                + (f'<div class="b bold">{escape(words)}</div>' if words else "")
                + (f'<div class="c">{escape(body)}</div>' if body else ""))
    if kind == "text":
        item = refs.get(ref) or {}
        if not item:
            return ""
        value = f'<div class="t3{accent}">{escape(item["value"])}</div>' if item.get("value") else ""
        return (value + f'<div class="b bold">{escape(item.get("heading") or "")}</div>'
                + (f'<div class="s">{escape(item["body"])}</div>' if item.get("body") else ""))
    if kind == "items":
        rows = []
        for one in ref if isinstance(ref, list) else []:
            item = refs.get(one) or {}
            if not item or one in state.dropped:
                continue
            rows.append(f'<div class="li"><div class="fig{"" if item.get("value") else " n"}">{escape(item.get("value") or "·")}</div>'
                        f'<div class="gap0"><div class="b bold">{escape(item.get("heading") or "")}</div>'
                        + (f'<div class="c">{escape(item["body"])}</div>' if item.get("body") else "") + "</div></div>")
        return f'<div class="ledger">{"".join(rows)}</div>' if rows else ""
    if kind == "chart":
        series = refs.get("chart")
        if not series:
            return ""
        return f'<div class="c">{escape(charts.caption(series))}</div><div class="chart" data-chart=\'{charts.attr(series)}\'></div>'
    if kind == "bar":
        source = refs.get(ref) or {}
        match = re.match(r"^([\d.]+)\s*[%％]$", str(source.get("value") or "").strip())
        if not match:
            return ""  # a bar draws a share; anything else is not drawn as one
        label = source.get("heading") or source.get("text") or ""
        return (f'<div class="c">{escape(label)} {escape(source["value"])}</div>'
                f'<div class="strip"><div class="fill{accent or " red"}" style="width:{float(match.group(1)):g}%"></div></div>')
    if kind in ("call", "lead"):
        text = refs.get(ref) or ""
        return f'<div class="{"call" if kind == "call" else "lead"}">{escape(text)}</div>' if text else ""
    return ""


def _node(node: dict[str, Any], job: Any, state: State, refs: dict[str, Any]) -> str:
    if "leaf" in node:
        return _leaf(node, job, state, refs)
    children = [child for child in node.get("children") or [] if isinstance(child, dict)]
    gap = GAPS[["l", "m", "s"][min(2, ["l", "m", "s"].index(node.get("gap", "m")) + state.gap_shrink)]] \
        if node.get("gap", "m") in GAPS else GAPS["m"]
    align = {"start": "flex-start", "center": "center", "end": "flex-end"}.get(node.get("align"), "stretch")
    rendered = [(_node(child, job, state, refs), child) for child in children]
    rendered = [(html, child) for html, child in rendered if html]
    if node.get("dir") == "row":
        weights = list(node.get("weights") or [])
        cells = []
        for index, (html, _) in enumerate(rendered):
            weight = weights[index] if index < len(weights) and isinstance(weights[index], (int, float)) else 1
            cells.append(f'<div style="flex:{max(0.2, float(weight)):g} 1 0;min-width:0;display:flex;flex-direction:column;'
                         f'gap:{gap}px;justify-content:{align}">{html}</div>')
        return f'<div style="display:flex;flex-direction:row;gap:{gap * 2}px;align-items:stretch">{"".join(cells)}</div>'
    def wrap(html: str, child: dict[str, Any]) -> str:
        if child.get("dir") == "row":
            # The body takes the height the page has to give, its columns centred in it.
            return (f'<div style="flex:1 1 auto;display:flex;flex-direction:column;justify-content:center;'
                    f'gap:{max(6, gap // 2)}px">{html}</div>')
        if child.get("leaf") == "call":
            return f'<div style="margin-top:auto;display:flex;flex-direction:column">{html}</div>'  # the close sits at the foot
        return f'<div style="display:flex;flex-direction:column;gap:{max(6, gap // 2)}px">{html}</div>'

    inner = "".join(wrap(html, child) for html, child in rendered)
    return f'<div style="flex:1 1 auto;display:flex;flex-direction:column;gap:{gap}px;justify-content:{align}">{inner}</div>'


def render(ir: dict[str, Any], job: Any, state: State) -> str:
    refs = _refs(job.content)
    body = _node(ir.get("root") or {}, job, state, refs)
    foot = (f'<div class="source">{escape(job.source or "")}</div>'
            f'<div class="pageno">{job.page_number:02d}</div>')
    return (f'<div class="page"><div class="r" style="grid-column:1/13;grid-row:1/9;display:flex;flex-direction:column">'
            f'{body}</div>{foot}</div>')


def leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if "leaf" in node:
        return [node]
    return [leaf for child in node.get("children") or [] if isinstance(child, dict) for leaf in leaves(child)]


def problems(ir: Any, job: Any) -> list[str]:
    """What makes a plan unusable before it is drawn."""

    if not isinstance(ir, dict) or not isinstance(ir.get("root"), dict):
        return ["没有 root"]
    found = leaves(ir["root"])
    kinds = [leaf.get("leaf") for leaf in found]
    issues = []
    if "title" not in kinds:
        issues.append("缺少 title 叶子")
    if "chart" in kinds and charts.chartable(job.content) is None:
        issues.append("这一页没有核对过的数据序列，不能用 chart")
    if charts.chartable(job.content) is not None and job.content.get("visual") and "chart" not in kinds:
        issues.append("这一页有核对过的数据序列，必须用 chart 叶子把它画出来")
    used = [ref for leaf in found for ref in (leaf.get("ref") if isinstance(leaf.get("ref"), list) else [leaf.get("ref")])
            if isinstance(ref, str) and ref.startswith("item:")]
    if len(used) != len(set(used)):
        issues.append("同一条事实用了不止一次")
    return issues


def reductions(ir: dict[str, Any]) -> list[State]:
    """Ways to make the page fit, gentlest first: close gaps, step display type down, move the
    lowest-priority leaves to the notes (never a priority-1 leaf)."""

    order = [State(1), State(2), State(2, 1), State(2, 2)]
    droppable = [leaf for leaf in leaves(ir["root"]) if leaf.get("priority", 2) >= 2 and leaf.get("leaf") in ("text", "items", "lead", "bar")]
    droppable.sort(key=lambda leaf: -int(leaf.get("priority", 2)))
    dropped: list[str] = []
    for leaf in droppable:
        refs = leaf["ref"] if isinstance(leaf.get("ref"), list) else [leaf.get("ref")]
        for ref in reversed(refs):
            if isinstance(ref, str):
                dropped.append(ref)
                order.append(State(2, 1, list(dropped)))
    return order


async def plan(client: Any, job: Any, previous: dict[str, Any] | None = None, issues: list[str] | None = None) -> Any:
    asked = {"content": job.content, "title": job.title, "kicker": job.kicker, "idea": job.idea,
             **({"previous_plan": previous, "problems": issues} if previous else {})}
    return await client.complete_json(task="layout_ir", system=IR_SYSTEM, user=json.dumps(asked, ensure_ascii=False),
                                      max_output_tokens=2500, context={"page": job.page_number})


async def design_page_ir(client: Any, session: Any, theme: Any, font_css: str, job: Any) -> Any:
    """A page from a model-made structure laid out by the program; the PageResult carries a trace like
    ``design_page``'s: attempt 1 is the plan as drawn, ``repaired`` means a reduction made it fit."""

    from ppt_agent.v2.htmldeck.convert import ConvertError, to_page
    from ppt_agent.v2.htmldeck.pipeline import SOFT, PageResult, _measure, _required

    result = PageResult(page_number=job.page_number, page=None)
    allowed = allowed_numbers([job.content, job.source, job.kicker, job.title, job.page_number])

    def check(body, measured):
        found = find_defects(measured, allowed, tier=str(job.content.get("tier") or "standard"), roles=True)
        return found + _required(job, measured) + graphic_checks.find(body, job.content)

    def hard(found):
        return [defect for defect in found if defect.kind not in SOFT]

    previous, issues = None, None
    for _ in range(2):  # the plan, and one re-plan with what was wrong
        result.attempts += 1
        try:
            ir = await plan(client, job, previous, issues)
        except Exception as error:  # noqa: BLE001
            result.defects.append(f"model call failed: {error}")
            continue
        wrong = problems(ir, job)
        if wrong:
            result.trace.append({"attempt": result.attempts, "kinds": ["plan"], "messages": wrong})
            previous, issues = ir, wrong
            continue
        best = None
        for index, state in enumerate([State()] + reductions(ir)):
            body = render(ir, job, state)
            measured = await _measure(session, theme, font_css, body, job=job)
            found = check(body, measured)
            if index == 0:
                result.trace.append({"attempt": result.attempts, "kinds": sorted({d.kind for d in found}),
                                     "messages": [d.message[:80] for d in found][:6], "ir": True})
            if not hard(found):
                best = (body, measured, found, state)
                break
        if best is not None:
            body, measured, found, state = best
            if state != State():
                result.trace[-1]["repaired"] = True
                result.trace[-1]["reduction"] = {"gap_shrink": state.gap_shrink, "step": state.step,
                                                 "to_notes": state.dropped}
            if state.dropped:
                result.reduced = True
            try:
                result.page = to_page(measured, page_number=job.page_number, role=job.role, section=job.section,
                                      title=job.title, notes=job.notes)
            except (ConvertError, ValueError) as error:
                result.defects.append(f"could not convert: {error}")
                return result
            result.html, result.screenshot_png = body, measured.screenshot_png
            result.aesthetic = [defect.message for defect in found]
            result.defects = []
            return result
        previous, issues = ir, [d.message for d in hard(found)][:6] + ["（以上是按你的结构排版后仍然存在的问题：换一种构图，或把次要条目设为 priority 3）"]
        result.defects = issues
    return result
