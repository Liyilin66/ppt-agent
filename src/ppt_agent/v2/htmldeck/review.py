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
