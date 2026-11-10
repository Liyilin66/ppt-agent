"""Plan every page's composition before any page is designed.

Each page gets a *fingerprint*: which visual form carries it, where the eye
lands, and how the space is divided. Fingerprints are compared against every
page already planned - not just the neighbours - so the same structure cannot
come round again merely because the figures changed. The model proposes; the
rules here have the last word.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from ppt_agent.v2.htmldeck.themes import STUDIO_FORMS

FOCUS = ("left", "right")
RATIO = ("even", "wide", "narrow")  # how much of the width the main visual takes
SAME_FORM_GAP = 3        # pages between two uses of one form
SAME_STRUCTURE_GAP = 8   # pages between two uses of one form + focus + ratio
MAX_FORM_SHARE = 0.16

# What each form shows well, and what it needs.
FORMS_FOR: dict[str, tuple[str, ...]] = {
    "single": ("poster", "units", "margin", "statement"),
    "comparison": ("columns", "split", "chart", "ledger", "table"),
    "trend": ("strip", "chart", "margin"),
    "proportion": ("strip", "units", "poster"),
    "ranking": ("chart", "ledger", "columns"),
    "sequence": ("stairs", "flow"),
    "timeline": ("timeline", "ledger"),
    "structure": ("matrix", "columns", "ledger"),
    "cause": ("flow", "margin", "split"),
    "cases": ("ledger", "table", "margin", "columns"),
}
TIER_FORMS = {
    "visual": {"poster", "units", "statement", "strip", "columns", "split"},
    "deep": {"ledger", "table", "chart", "matrix", "margin", "timeline", "flow"},
}
SYMMETRIC = {"timeline", "columns", "table", "stairs"}  # no left/right version


@dataclass(frozen=True)
class Fingerprint:
    page: int
    form: str
    focus: str = "left"
    ratio: str = "even"
    core_visual: str = ""   # what the reader sees first
    reading_path: str = ""  # the order the eye is led through
    reason: str = ""

    @property
    def structure(self) -> tuple[str, str, str]:
        return self.form, self.focus, self.ratio


def _percent(value: str | None) -> float | None:
    text = (value or "").replace("％", "%").strip()
    if not text.endswith("%"):
        return None
    try:
        return float(text.rstrip("%+").lstrip("约超近"))
    except ValueError:
        return None


def candidates(content: dict[str, Any]) -> list[str]:
    """Forms that can honestly draw this page, best fit first."""

    archetype = content.get("archetype")
    if archetype == "summary":
        return ["ledger", "matrix", "columns"]
    if archetype == "roadmap":
        return ["stairs", "table", "flow"]
    if archetype != "rich":
        return ["ledger", "margin", "matrix", "columns"]
    relation, tier = content.get("relation", "cases"), content.get("tier", "standard")
    items = content.get("items") or []
    highlight = (content.get("highlight") or {}).get("value")
    series = content.get("categories") or []
    rows = content.get("rows") or []
    dated = sum(bool(item.get("date")) for item in items)

    def usable(form: str) -> bool:
        if form == "chart":
            return len(series) >= 2
        if form == "table":
            return len(rows) >= 2
        if form == "poster":
            return bool(highlight)
        if form == "units":
            percent = _percent(highlight)
            return percent is not None and 0 < percent <= 100
        if form == "strip":
            shares = [_percent(item.get("value")) for item in items] + [_percent(highlight)]
            return sum(1 for share in shares if share is not None and share <= 100) >= 1
        if form == "timeline":
            return dated >= 3 and len(items) <= 5
        if form == "statement":
            return not highlight and len(items) <= 3
        if form in ("stairs", "flow"):
            return 3 <= len(items) <= 5
        if form == "matrix":
            return len(items) == 4
        if form in ("columns", "split"):
            return 2 <= len(items) <= (3 if form == "columns" else 4)
        return True

    ordered = [form for form in FORMS_FOR.get(relation, FORMS_FOR["cases"]) if usable(form)]
    if series and "chart" not in ordered:
        ordered.insert(0, "chart")  # drawn data beats the same numbers in prose
    if rows and "table" not in ordered:
        ordered.append("table")
    preferred = TIER_FORMS.get(tier)
    if preferred:
        ordered.sort(key=lambda form: form not in preferred)
    for fallback in ("ledger", "margin"):
        if fallback not in ordered:
            ordered.append(fallback)
    return ordered


def conflict(candidate: Fingerprint, earlier: list[Fingerprint], total: int, *, strictness: int = 2) -> str | None:
    """Why this fingerprint repeats an earlier page, or ``None``.

    ``strictness`` 2 applies every rule; 1 drops the deck-wide share cap; 0 keeps
    only "not the same form as a page just before it".
    """

    for other in earlier:
        gap = candidate.page - other.page
        if other.form == candidate.form and gap <= SAME_FORM_GAP:
            return f"第 {other.page} 页刚用过同一种形式"
        if strictness >= 1 and other.structure == candidate.structure and gap <= SAME_STRUCTURE_GAP:
            return f"和第 {other.page} 页的空间结构相同"
    used = sum(other.form == candidate.form for other in earlier)
    if strictness >= 2 and total >= 8 and (used + 1) / total > MAX_FORM_SHARE:
        return "这种形式在整套里用得太多"
    return None


def resolve(proposed: dict[int, dict[str, Any]], contents: dict[int, dict[str, Any]]) -> list[Fingerprint]:
    """Turn proposals into a plan no page of which repeats another's structure."""

    total = len(contents)
    plan: list[Fingerprint] = []
    for page in sorted(contents):
        allowed = candidates(contents[page])
        wish = proposed.get(page) or {}
        first = wish.get("form") if wish.get("form") in allowed else allowed[0]
        forms = [first] + [form for form in allowed if form != first]
        usage = Counter(item.form for item in plan)
        forms[1:] = sorted(forms[1:], key=lambda form: usage[form])
        def options():
            for form in forms:
                focuses = [wish.get("focus")] if form == first and wish.get("focus") in FOCUS else []
                focuses += [focus for focus in FOCUS if focus not in focuses]
                if form in SYMMETRIC:
                    focuses = ["left"]
                for focus in focuses:
                    for ratio in RATIO if form not in SYMMETRIC else ("even",):
                        yield Fingerprint(
                            page=page, form=form, focus=focus, ratio=ratio,
                            core_visual=str(wish.get("core_visual") or "")[:60] if form == first else "",
                            reading_path=str(wish.get("reading_path") or "")[:60] if form == first else "",
                            reason=str(wish.get("reason") or "")[:80] if form == first else "",
                        )

        chosen: Fingerprint | None = None
        # Relax one rule at a time rather than give up on all of them at once.
        for strictness in (2, 1, 0):
            chosen = next((option for option in options()
                           if conflict(option, plan, total, strictness=strictness) is None), None)
            if chosen is not None:
                break
        if chosen is None:  # every allowed form was used a page or two ago: take the one used longest ago
            chosen = max(options(), key=lambda option: min(
                [page - item.page for item in plan if item.form == option.form] or [999]))
        plan.append(chosen)
    return plan


PLAN_SYSTEM = (
    "你是一份演示文稿的艺术指导。下面是整套内容页的摘要。请为每一页选一种视觉形式，并说明读者第一眼看什么、"
    "视线怎么走。要求：形式必须从该页的 allowed 里选；相邻三页不能用同一种形式；整套要有节奏，"
    "大数字页、图解页、信息密集页交替出现，不要连续三页的视觉重心都在同一侧。"
    "图形要表达内容，而不是为了不同而不同。"
    '只输出 JSON：{"pages":[{"page":页码,"form":"形式名","focus":"left 或 right",'
    '"core_visual":"第一眼看到什么（20 字内）","reading_path":"视线顺序（20 字内）","reason":"为什么这样画（30 字内）"}]}\n'
    "可用的形式：\n" + "\n".join(f"- {name}：{text}" for name, text in STUDIO_FORMS.items())
)


async def plan_layouts(client: Any, contents: dict[int, dict[str, Any]]) -> list[Fingerprint]:
    """Ask the model for a deck-wide plan, then enforce diversity in code."""

    digest = [
        {"page": page, "title": content.get("title"), "relation": content.get("relation", content.get("archetype")),
         "tier": content.get("tier", "standard"), "items": len(content.get("items") or content.get("rows") or []),
         "focal_figure": (content.get("highlight") or {}).get("value"),
         "has_series": bool(content.get("categories")), "has_table": bool(content.get("headers")),
         "allowed": candidates(content)}
        for page, content in sorted(contents.items())
    ]
    proposed: dict[int, dict[str, Any]] = {}
    for start in range(0, len(digest), 30):
        batch = digest[start:start + 30]
        try:
            reply = await client.complete_json(
                task="layout_plan", system=PLAN_SYSTEM,
                user="已经规划过的页：" + json.dumps(
                    [{"page": page, "form": item.get("form")} for page, item in sorted(proposed.items())],
                    ensure_ascii=False)
                + "\n请规划这些页：" + json.dumps(batch, ensure_ascii=False),
                max_output_tokens=3000, context={"pages": batch},
            )
        except Exception:  # noqa: BLE001 - the rules below can plan alone
            continue
        for entry in (reply.get("pages") if isinstance(reply, dict) else None) or []:
            if isinstance(entry, dict) and isinstance(entry.get("page"), int):
                proposed[entry["page"]] = entry
    return resolve(proposed, contents)


def audit(plan: list[Fingerprint]) -> dict[str, Any]:
    forms = Counter(item.form for item in plan)
    total = max(len(plan), 1)
    repeats = []
    for index, item in enumerate(plan):
        why = conflict(item, plan[:index], total)
        if why:
            repeats.append({"page": item.page, "why": why})
    return {"pages": len(plan), "forms_used": len(forms), "form_counts": dict(forms.most_common()),
            "largest_form_share": round(max(forms.values()) / total, 3) if forms else 0.0,
            "distinct_structures": len({item.structure for item in plan}), "repeats": repeats}


def to_markdown(plan: list[Fingerprint], titles: dict[int, str]) -> str:
    lines = ["| 页 | 标题 | 形式 | 重心 | 版面比例 | 第一眼 | 视线 | 理由 |", "|---|---|---|---|---|---|---|---|"]
    for item in plan:
        lines.append(
            f"| {item.page} | {titles.get(item.page, '')} | {item.form} | "
            f"{'左' if item.focus == 'left' else '右'} | {item.ratio} | {item.core_visual} | "
            f"{item.reading_path} | {item.reason} |")
    return "\n".join(lines) + "\n"


def as_dicts(plan: list[Fingerprint]) -> list[dict[str, Any]]:
    return [asdict(item) for item in plan]
