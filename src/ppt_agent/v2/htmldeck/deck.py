"""Design every page of a deck in HTML, concurrently, with a checkpoint per page."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Callable

from ppt_agent.v2.htmldeck import browser, sanitize, themes
from ppt_agent.v2.htmldeck.checks import find_defects
from ppt_agent.v2.htmldeck.convert import ConvertError, to_page
from ppt_agent.v2.htmldeck.fonts import office_font_css
from ppt_agent.v2.htmldeck.pipeline import (
    SOFT,
    DesignJob,
    PageResult,
    _measure,
    assign_ideas,
    design_page,
    structural_page,
)

_HIDDEN = {"speaker_notes", "source", "archetype"}


def job_for(content: Any, *, page_number: int, section_index: int | None,
            section_title: str | None) -> DesignJob:
    """A design job from one page of verified, reader-facing content."""

    archetype = getattr(content, "archetype", "points")
    kicker = None
    if section_title:
        kicker = f"{section_index:02d} · {section_title}" if section_index else section_title
    return DesignJob(
        page_number=page_number,
        archetype=archetype,
        content=content.model_dump(mode="json", exclude_none=True, exclude=_HIDDEN),
        # A statement page shows its statement, not a heading.
        title=None if archetype == "statement" else content.title,
        kicker=kicker,
        source=content.source,
        notes=content.speaker_notes,
        section=section_title,
    )


def _fingerprint(job: DesignJob) -> str:
    """Identifies the content a saved page was designed for."""

    payload = json.dumps([job.content, job.source, job.kicker, job.title], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def _replay(session, theme, font_css, job: DesignJob, html: str) -> PageResult:
    """Rebuild a page from HTML saved by an earlier run; no model call."""

    result = PageResult(page_number=job.page_number, page=None, html=html)
    body, _ = sanitize.clean(html)
    measured = await _measure(session, theme, font_css, body)
    if any(defect.kind not in SOFT for defect in find_defects(measured, None)):
        return result
    try:
        result.page = to_page(measured, page_number=job.page_number, role=job.role,
                              section=job.section, title=job.title, notes=job.notes)
    except (ConvertError, ValueError):
        return result
    result.screenshot_png = measured.screenshot_png
    return result


async def design_deck(
    client: Any,
    theme_name: str,
    jobs: list[DesignJob],
    structural: list[dict[str, Any]],
    *,
    checkpoints: Any = None,
    concurrency: int = 6,
    look: bool = True,
    progress: Callable[[str], None] = lambda message: None,
) -> tuple[dict[int, PageResult], dict[str, Any]]:
    """Return a result per page number, and a summary for the run report.

    Raises ``browser.BrowserUnavailable`` when no browser can be started.
    """

    if not hasattr(client, "complete_text"):
        raise browser.BrowserUnavailable("this model client cannot return plain text")
    theme = themes.get(theme_name)
    font_css, real_fonts = office_font_css()
    assign_ideas(jobs)
    gate = asyncio.Semaphore(max(1, concurrency))
    results: dict[int, PageResult] = {}

    async with browser.Session(concurrency=max(2, min(concurrency, 6))) as session:

        async def one(job: DesignJob) -> None:
            name = f"html/page_{job.page_number:03d}.json"
            cached = checkpoints.load(name) if checkpoints is not None else None
            async with gate:
                if cached and cached.get("html") and cached.get("content") == _fingerprint(job):
                    result = await _replay(session, theme, font_css, job, cached["html"])
                    result.attempts = 0
                    if result.ok:
                        results[job.page_number] = result
                        return
                result = await design_page(client, session, theme, font_css, job, look=look)
            results[job.page_number] = result
            if checkpoints is not None and result.ok:
                checkpoints.save(name, {"html": result.html, "content": _fingerprint(job),
                                        "attempts": result.attempts, "idea": job.idea,
                                        "review_problems": result.review_problems})
            progress(f"[design] page {job.page_number} "
                     + ("ready" if result.ok else "fell back: " + "; ".join(result.defects[:2])))

        async def fixed(spec: dict[str, Any]) -> None:
            results[spec["page_number"]] = await structural_page(session, theme, font_css, **spec)

        await asyncio.gather(*(one(job) for job in jobs), *(fixed(spec) for spec in structural))

    designed = [job.page_number for job in jobs if results[job.page_number].ok]
    summary = {
        "theme": theme_name,
        "office_fonts": real_fonts,
        "designed_pages": len(designed),
        "fell_back_pages": sorted(job.page_number for job in jobs if not results[job.page_number].ok),
        "structural_fell_back": sorted(spec["page_number"] for spec in structural
                                       if not results[spec["page_number"]].ok),
        "model_calls": sum(results[job.page_number].attempts for job in jobs),
        "rewritten_pages": sum(results[job.page_number].attempts > 1 for job in jobs),
        "reviewed_pages": sum(results[job.page_number].reviewed for job in jobs),
        "review_flagged_pages": sum(bool(results[job.page_number].review_problems) for job in jobs),
        "ideas": {str(job.page_number): job.idea for job in jobs},
    }
    return results, summary
