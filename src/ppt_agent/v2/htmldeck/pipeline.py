"""One page: verified content -> HTML -> measured -> checked -> PageDesign.

The model designs; the browser measures; rules catch what must never ship
(overflow, overlap, numbers that were not in the content); the model then looks
at its own page once. A page that still fails is returned as ``None`` so the
caller can use the code-driven layout engine instead.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ppt_agent.v2.htmldeck import prompts, review, sanitize, structural, themes
from ppt_agent.v2.htmldeck.browser import Measured, Session
from ppt_agent.v2.htmldeck.checks import Defect, allowed_numbers, find_defects
from ppt_agent.v2.htmldeck.convert import ConvertError, to_page
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.ir import PageDesign, PageRole

MAX_REWRITES = 2
SOFT = frozenset({"sparse", "small_text"})  # worth one more try, not worth losing the page
DESIGN_TOKENS = 2600


@dataclass
class DesignJob:
    page_number: int
    archetype: str
    content: dict[str, Any]  # verified, reader-facing copy
    title: str | None = None
    kicker: str | None = None
    source: str | None = None
    notes: str | None = None
    section: str | None = None
    role: PageRole = "content"
    idea: str = ""


@dataclass
class PageResult:
    page_number: int
    page: PageDesign | None
    html: str = ""
    attempts: int = 0
    reviewed: bool = False
    review_problems: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    screenshot_png: bytes = b""

    @property
    def ok(self) -> bool:
        return self.page is not None


def assign_ideas(jobs: list[DesignJob]) -> None:
    """Give each job an arrangement, rotating so same-kind pages differ."""

    seen: Counter[str] = Counter()
    for job in sorted(jobs, key=lambda item: item.page_number):
        job.idea = prompts.layout_idea(job.archetype, seen[job.archetype])
        seen[job.archetype] += 1


def _plain(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _page_text(measured: Measured) -> str:
    return _plain("".join(
        run.get("t", "") for item in measured.items if item["kind"] == "text" for run in item["runs"]
    ))


def _required(job: DesignJob, measured: Measured) -> list[Defect]:
    """The page must still say what it is about and where it came from."""

    text = _page_text(measured)
    missing = []
    if job.title and _plain(job.title) not in text:
        missing.append(Defect("missing", f"页面上没有原样写出标题「{job.title}」。"))
    if job.source and _plain(job.source) not in text:
        missing.append(Defect("missing", "页脚的资料来源没有原样写出，请一字不差地放进 source。"))
    return missing


async def _measure(session: Session, theme: themes.HtmlTheme, font_css: str, body: str,
                   *, backdrop: bool = True) -> Measured:
    return await session.measure(wrap(body, theme.css, font_css, theme.backdrop if backdrop else ""))


async def design_page(
    client: Any,
    session: Session,
    theme: themes.HtmlTheme,
    font_css: str,
    job: DesignJob,
    *,
    look: bool = True,
) -> PageResult:
    allowed = allowed_numbers([job.content, job.source, job.kicker, job.title, job.page_number])
    system = prompts.system_prompt(theme)
    result = PageResult(page_number=job.page_number, page=None)
    best: tuple[str, Measured] | None = None
    previous_html: str | None = None
    problems: list[str] = []

    async def attempt() -> tuple[str, Measured, list[Defect]] | None:
        result.attempts += 1
        try:
            raw = await client.complete_text(
                task="html_page",
                system=system,
                user=prompts.user_prompt(
                    content=job.content, archetype=job.archetype, page_number=job.page_number,
                    kicker=job.kicker, source=job.source, idea=job.idea,
                    previous_html=previous_html, problems=problems,
                ),
                max_output_tokens=DESIGN_TOKENS,
                context={"job": job, "problems": list(problems)},
            )
        except Exception as error:  # noqa: BLE001 - a failed call costs this page only
            result.defects.append(f"model call failed: {error}")
            return None
        body, _rejected = sanitize.clean(raw)
        if not body:
            result.defects.append("empty reply")
            return None
        measured = await _measure(session, theme, font_css, body)
        return body, measured, find_defects(measured, allowed) + _required(job, measured)

    for _ in range(1 + MAX_REWRITES):
        outcome = await attempt()
        if outcome is None:
            break
        body, measured, defects = outcome
        hard = [defect for defect in defects if defect.kind not in SOFT]
        if not hard:
            best = (body, measured)
        if not defects:
            break
        previous_html, problems = body, [defect.message for defect in defects][:6]
        result.defects = list(problems)

    if best is None:
        return result
    result.defects = [] if not problems or best[0] != previous_html else result.defects

    if look:
        seen = await review.look(client, best[1].screenshot_png, title=job.title)
        result.reviewed = True
        result.review_problems = seen
        if seen:
            previous_html, problems = best[0], seen
            outcome = await attempt()
            if outcome is not None:
                body, measured, defects = outcome
                if not [defect for defect in defects if defect.kind not in SOFT]:
                    best = (body, measured)  # keep the revision only if it broke nothing

    body, measured = best
    try:
        page = to_page(measured, page_number=job.page_number, role=job.role, section=job.section,
                       title=job.title, notes=job.notes)
    except (ConvertError, ValueError) as error:
        result.defects.append(f"could not convert: {error}")
        return result
    result.page, result.html, result.screenshot_png = page, body, measured.screenshot_png
    return result


async def structural_page(
    session: Session,
    theme: themes.HtmlTheme,
    font_css: str,
    *,
    kind: str,
    page_number: int,
    deck_title: str,
    subtitle: str | None,
    sections: list[tuple[str, int]],
    section_index: int | None = None,
    section_title: str | None = None,
    kicker: str = "",
) -> PageResult:
    """Cover, agenda, section divider or closing page from a filled template."""

    if kind == "cover":
        body, role = structural.cover(deck_title, subtitle, [name for name, _ in sections], kicker), "cover"
    elif kind == "section_divider":
        body, role = structural.section(section_index, section_title or deck_title), "section_divider"
    elif kind == "toc":
        body, role = structural.agenda(sections, page_number), "toc"
    else:
        body, role = structural.closing(deck_title, subtitle), "closing"
    result = PageResult(page_number=page_number, page=None, html=body, attempts=0)
    measured = await _measure(session, theme, font_css, body, backdrop=kind == "toc")
    defects = find_defects(measured, None, require_fill=False)
    if any(defect.kind not in SOFT for defect in defects):
        result.defects = [defect.message for defect in defects]
        return result
    try:
        result.page = to_page(measured, page_number=page_number, role=role, section=section_title,
                              title=section_title or deck_title)
    except (ConvertError, ValueError) as error:
        result.defects.append(f"could not convert: {error}")
    result.screenshot_png = measured.screenshot_png
    return result
