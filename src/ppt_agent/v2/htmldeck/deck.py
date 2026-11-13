"""Design every page of a deck in HTML, concurrently, with a checkpoint per page."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Callable

from ppt_agent.v2.htmldeck import browser, layout_plan, prompts, review, sanitize, signature, themes
from ppt_agent.v2.htmldeck.browser import MeasureFailed
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


def _apply(job: DesignJob, fingerprint: layout_plan.Fingerprint) -> None:
    job.form = fingerprint.form
    job.idea = prompts.studio_idea(fingerprint.form, fingerprint.focus, fingerprint.ratio,
                                   core_visual=fingerprint.core_visual, reading_path=fingerprint.reading_path)


def _plain_html(job: DesignJob) -> str:
    """The job's facts as (figure, heading, body) rows for the plain template."""

    from ppt_agent.v2.htmldeck import structural

    content, rows = job.content, []
    for index, item in enumerate(content.get("items") or content.get("rows") or content.get("metrics")
                                 or content.get("steps") or content.get("milestones") or [], 1):
        if not isinstance(item, dict):
            continue
        figure = str(item.get("value") or item.get("date") or item.get("tag") or f"{index:02d}")
        head = str(item.get("heading") or item.get("headline") or item.get("label") or "")
        body = str(item.get("body") or item.get("detail") or item.get("note") or "")
        rows.append((figure[:10], head, body))
    if not rows:
        rows = [("01", content.get("takeaway") or job.title or "", "")]
    return structural.plain(title=job.title, kicker=job.kicker, source=job.source,
                            page_number=job.page_number, rows=rows, note=content.get("takeaway"))


def contact_sheet(shots: dict[int, bytes], columns: int = 5, width: int = 380) -> bytes:
    """All pages side by side, numbered, as one PNG: the deck seen from a distance."""

    import io

    from PIL import Image, ImageDraw

    pages = [page for page in sorted(shots) if shots[page]]
    if not pages:
        return b""
    height, pad = round(width * 9 / 16), 10
    rows = (len(pages) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (width + pad) + pad, rows * (height + pad + 18) + pad), "#2a2d33")
    draw = ImageDraw.Draw(sheet)
    for index, page in enumerate(pages):
        x = pad + (index % columns) * (width + pad)
        y = pad + (index // columns) * (height + pad + 18)
        draw.text((x, y), f"p{page}", fill="#ffffff")
        sheet.paste(Image.open(io.BytesIO(shots[page])).convert("RGB").resize((width, height)), (x, y + 16))
    out = io.BytesIO()
    sheet.save(out, format="PNG")
    return out.getvalue()


def _fingerprint(job: DesignJob) -> str:
    """Identifies the content a saved page was designed for."""

    payload = json.dumps([job.content, job.source, job.kicker, job.title, job.form, job.idea if job.form else ""],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def _replay(session, theme, font_css, job: DesignJob, html: str) -> PageResult:
    """Rebuild a page from HTML saved by an earlier run; no model call."""

    result = PageResult(page_number=job.page_number, page=None, html=html)
    body, _ = sanitize.clean(html)
    try:
        measured = await _measure(session, theme, font_css, body)
    except MeasureFailed:
        return result
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
    plan: list[layout_plan.Fingerprint] = []
    contents = {job.page_number: {**job.content, "archetype": job.archetype} for job in jobs}
    if theme.vocabulary == 2:
        # Compose the whole deck first: every page gets a form and a fingerprint,
        # checked against all the pages before it.
        saved = checkpoints.load("html/layout_plan.json") if checkpoints is not None else None
        if saved and [item["page"] for item in saved] == sorted(contents) and all(
                item["form"] in layout_plan.candidates(contents[item["page"]]) for item in saved):
            plan = [layout_plan.Fingerprint(**item) for item in saved]
        else:
            plan = await layout_plan.plan_layouts(client, contents)
            if checkpoints is not None:
                checkpoints.save("html/layout_plan.json", layout_plan.as_dicts(plan))
        by_page = {item.page: item for item in plan}
        for job in jobs:
            _apply(job, by_page[job.page_number])
        progress(f"[design] layout plan: {len({item.form for item in plan})} forms over {len(plan)} pages")
    else:
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
            if not result.ok and theme.vocabulary == 2:
                # Rather than a page from the other layout engine, a plain page in this theme.
                template = await _replay(session, theme, font_css, job, _plain_html(job))
                if not template.ok:
                    template = await _replay(session, theme, font_css, job,
                                             _plain_html(job).replace('class="page', 'class="page compact', 1))
                if template.ok:
                    template.attempts, template.defects = result.attempts, result.defects
                    template.templated = True
                    result = template
            results[job.page_number] = result
            if checkpoints is not None and result.ok and not result.templated:
                checkpoints.save(name, {"html": result.html, "content": _fingerprint(job),
                                        "attempts": result.attempts, "idea": job.idea,
                                        "review_problems": result.review_problems})
            if checkpoints is not None and not result.ok:
                checkpoints.save(f"html/failed_{job.page_number:03d}.json",
                                 {"html": result.html, "defects": result.defects, "idea": job.idea})
            progress(f"[design] page {job.page_number} "
                     + ("ready" if result.ok else "fell back: " + "; ".join(result.defects[:2])))

        async def fixed(spec: dict[str, Any]) -> None:
            results[spec["page_number"]] = await structural_page(session, theme, font_css, **spec)

        await asyncio.gather(*(one(job) for job in jobs), *(fixed(spec) for spec in structural))

        redesigned: list[dict[str, Any]] = []
        if theme.vocabulary == 2:
            by_page = {job.page_number: job for job in jobs}

            def outlines() -> dict[int, str]:
                return {number: signature.silhouette(result.page) for number, result in results.items()
                        if number in by_page and result.ok}

            async def redo(job: DesignJob, why: str, *, new_form: bool) -> None:
                current = next(item for item in plan if item.page == job.page_number)
                if new_form:
                    nearby = {item.form for item in plan if 0 < abs(item.page - job.page_number) <= 3}
                    options = [form for form in layout_plan.candidates(contents[job.page_number])
                               if form != current.form and form not in nearby]
                    if not options:
                        return
                    current = layout_plan.Fingerprint(page=job.page_number, form=options[0],
                                                      focus="right" if current.focus == "left" else "left")
                    plan[plan.index(next(item for item in plan if item.page == job.page_number))] = current
                    _apply(job, current)
                else:
                    job.idea += "审稿意见（请据此调整）：" + why
                before = results[job.page_number]
                async with gate:
                    after = await design_page(client, session, theme, font_css, job, look=False)
                if after.ok:
                    results[job.page_number] = after
                    if checkpoints is not None:
                        checkpoints.save(f"html/page_{job.page_number:03d}.json",
                                         {"html": after.html, "content": _fingerprint(job),
                                          "attempts": before.attempts + after.attempts, "idea": job.idea,
                                          "review_problems": [why]})
                redesigned.append({"page": job.page_number, "why": why, "kept": after.ok, "form": job.form})
                progress(f"[design] page {job.page_number} redesigned ({why[:40]})")

            # Pages whose outlines nearly coincide, anywhere in the deck: recompose the later one.
            pairs = signature.lookalikes(outlines())
            later_pages = sorted({later for _, later, _ in pairs})[: max(2, len(jobs) // 5)]
            await asyncio.gather(*(redo(by_page[number], "和另一页的版面轮廓几乎相同", new_form=True)
                                   for number in later_pages))
            if look:
                # The whole deck at a glance: which pages look alike, which one is weakest.
                sheet = contact_sheet({number: result.screenshot_png for number, result in results.items()
                                       if number in by_page})
                for entry in await review.matrix(client, sheet):
                    job = by_page.get(entry["page"])
                    if job is not None:
                        await redo(job, entry["problem"], new_form=False)
            if checkpoints is not None:
                checkpoints.save("html/layout_plan.json", layout_plan.as_dicts(plan))

    designed = [job.page_number for job in jobs
                if results[job.page_number].ok and not results[job.page_number].templated]
    summary = {
        "theme": theme_name,
        "office_fonts": real_fonts,
        "designed_pages": len(designed),
        "fell_back_pages": sorted(job.page_number for job in jobs if not results[job.page_number].ok),
        "templated_pages": sorted(job.page_number for job in jobs if results[job.page_number].templated),
        "structural_fell_back": sorted(spec["page_number"] for spec in structural
                                       if not results[spec["page_number"]].ok),
        "model_calls": sum(results[job.page_number].attempts for job in jobs),
        "rewritten_pages": sum(results[job.page_number].attempts > 1 for job in jobs),
        "reviewed_pages": sum(results[job.page_number].reviewed for job in jobs),
        "review_flagged_pages": sum(bool(results[job.page_number].review_problems) for job in jobs),
        "ideas": {str(job.page_number): job.idea for job in jobs},
    }
    if theme.vocabulary == 2:
        final = {job.page_number: signature.silhouette(results[job.page_number].page)
                 for job in jobs if results[job.page_number].ok}
        summary.update(
            layout_plan=layout_plan.audit(plan),
            redesigned=redesigned,
            silhouettes=final,
            lookalike_pairs=[list(pair) for pair in signature.lookalikes(final)],
        )
    return results, summary
