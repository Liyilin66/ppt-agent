"""Let the model look at the rendered slide and say what is visibly wrong."""

from __future__ import annotations

import base64
from typing import Any

from ppt_agent.v2.htmldeck.prompts import REVIEW_SYSTEM


async def look(client: Any, screenshot_png: bytes, *, title: str | None) -> list[str]:
    """Problems a reader would notice, or an empty list. Never raises."""

    if not screenshot_png:
        return []
    try:
        reply = await client.complete_json(
            task="html_review",
            system=REVIEW_SYSTEM,
            user=f"这一页的标题是：{title or '（无）'}。请看截图。",
            max_output_tokens=400,
            context={"title": title},
            images=[("image/png", base64.b64encode(screenshot_png).decode("ascii"))],
        )
    except Exception:  # noqa: BLE001 - a failed review must not cost the page
        return []
    problems = reply.get("problems") if isinstance(reply, dict) else None
    if not isinstance(problems, list):
        return []
    return [str(problem).strip()[:80] for problem in problems if str(problem).strip()][:3]


MATRIX_SYSTEM = (
    "你是严格的演示设计总监。这张图是整套幻灯片的缩略图，每页左上角标着页码（p4 表示第 4 页）。"
    "请从远处看整体：哪些页的版面轮廓几乎一样（不看文字和数字，只看空间结构）？哪一页明显比其他页粗糙、"
    "拥挤或者空？只报最需要重做的，最多 4 页；整体没有明显问题就返回空列表。"
    "如果问题出在这一页用错了形式，在 form 里写出应该换成的形式；form 只能从给出的该页可用形式里选，"
    "否则不要写 form，只说怎么调整。"
    '只输出 JSON：{"pages":[{"page":页码,"problem":"问题和修改方向（40 字内）","form":"可选"}]}'
)


async def matrix(client: Any, sheet_png: bytes, *,
                 allowed: dict[int, list[str]] | None = None) -> list[dict[str, Any]]:
    """Pages to recompose after seeing the whole deck at once. Never raises.

    ``allowed`` maps a page to the forms its content can take (its current one
    first); an entry may then carry ``form``, one of those, to change to."""

    if not sheet_png:
        return []
    try:
        reply = await client.complete_json(
            task="html_matrix_review", system=MATRIX_SYSTEM,
            user="请看缩略图矩阵。" + ("每页可用的形式（第一个是现在用的）：" + "；".join(
                f"p{page}：{'、'.join(forms)}" for page, forms in sorted(allowed.items())) if allowed else ""),
            max_output_tokens=500, context={},
            images=[("image/png", base64.b64encode(sheet_png).decode("ascii"))],
        )
    except Exception:  # noqa: BLE001 - a failed review must not cost the deck
        return []
    entries = reply.get("pages") if isinstance(reply, dict) else None
    found = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("page"), int) and entry.get("problem"):
            item = {"page": entry["page"], "problem": str(entry["problem"])[:80]}
            form = entry.get("form")
            if allowed and isinstance(form, str) and form in allowed.get(entry["page"], [])[1:]:
                item["form"] = form
            found.append(item)
    return found[:4]
