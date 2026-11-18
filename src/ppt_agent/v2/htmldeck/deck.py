"""Design every page of a deck in HTML, concurrently, with a checkpoint per page."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import hashlib
import json
from typing import Any, Callable

from ppt_agent.v2.htmldeck import browser, composition, text_budget, graphic_checks, layout_plan, prompts, review, sanitize, signature, themes
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

OTHER_FORMS = 2  # other compositions to try before a plain page
_HIDDEN = {"speaker_notes", "source", "archetype"}
_OPTIONAL = ("series", "figure", "diagram", "visual")
ACCEPTED = "html/accepted.json"  # pages a person has signed off: {"pages": [4, 5, ...]}


def accept(checkpoints: Any, pages: list[int], note: str = "") -> list[int]:
    """Mark saved pages as signed off. Later runs replay them as they are: no
    recheck against newer rules, no repair, no redesign by the deck review.
    A page whose content changes is a different page and is designed again."""

    saved = checkpoints.load(ACCEPTED) or {}
    merged = sorted(set(saved.get("pages", [])) | set(pages))
    checkpoints.save(ACCEPTED, {"pages": merged, "note": note or saved.get("note", "")})
    return merged


def job_for(content: Any, *, page_number: int, section_index: int | None,
            section_title: str | None, assets_dir: Any = None) -> DesignJob:
    """A design job from one page of verified, reader-facing content."""

    archetype = getattr(content, "archetype", "points")
    kicker = None
    if section_title:
        kicker = f"{section_index:02d} · {section_title}" if section_index else section_title
    data = content.model_dump(mode="json", exclude_none=True, exclude=_HIDDEN)
    for name in _OPTIONAL:
        if not data.get(name):
            data.pop(name, None)  # a page without them reads exactly as it did before they existed
    for series in data.get("series") or []:
        for point in series["points"]:
            point.pop("quote", None)  # the proof stays with the content; the page shows the value
        if series.get("coverage") == "unknown" and not series.get("of_total") and not series.get("selection"):
            for name in ("coverage", "of_total", "selection", "measure"):
                series.pop(name, None)  # a series read before coverage was judged reads as it did
    return DesignJob(
        page_number=page_number,
        archetype=archetype,
        content=data,
        # A statement page shows its statement, not a heading.
        title=None if archetype == "statement" else content.title,
        kicker=kicker,
        source=content.source,
        notes=content.speaker_notes,
        section=section_title,
        figure_file=str(assets_dir / data["figure"]["id"]) if assets_dir is not None and data.get("figure") else None,
    )


# How many facts a form can carry as items, beside its main visual (P3: most first attempts that failed
# overflowed). The items are in order of importance; those past the limit go to the speaker notes.
FORM_ITEMS = {"poster": 3, "units": 3, "strip": 3, "statement": 2, "columns": 3, "split": 4, "ledger": 5,
              "margin": 4, "matrix": 4, "stairs": 5, "flow": 5, "timeline": 5, "figure": 2, "table": 3}
CHART_ITEMS = {"side": 3, "inset": 3, "full": 2, "band": 3}


def _fit_items(job: DesignJob, fingerprint: layout_plan.Fingerprint) -> None:
    if job.all_items is None:
        job.all_items, job.all_notes = list(job.content.get("items") or []), job.notes
    limit = (CHART_ITEMS.get(fingerprint.arrangement or "side", 3) if fingerprint.form == "chart"
             else FORM_ITEMS.get(fingerprint.form))
    items = job.all_items
    if limit is None or len(items) <= limit or "items" not in job.content:
        job.content = {**job.content, "items": items} if "items" in job.content else job.content
        job.notes = job.all_notes
        return
    kept, moved = items[:limit], items[limit:]
    job.content = {**job.content, "items": kept}
    extra = "；".join(" ".join(str(item.get(key)) for key in ("heading", "value", "body") if item.get(key))
                     for item in moved)
    job.notes = ((job.all_notes + "\n") if job.all_notes else "") + "页面未展开的要点：" + extra


def _apply(job: DesignJob, fingerprint: layout_plan.Fingerprint) -> None:
    if job.type_roles:
        _fit_items(job, fingerprint)  # runs begun under the P4 rules only: saved pages keep their content
    job.form = fingerprint.form
    job.idea = prompts.studio_idea(fingerprint.form, fingerprint.focus, fingerprint.ratio,
                                   core_visual=fingerprint.core_visual, reading_path=fingerprint.reading_path,
                                   arrangement=fingerprint.arrangement)
    if job.type_roles:
        # The space first: what the form's text area holds, told before the page is written.
        plan = text_budget.budget(job.content, fingerprint.form, fingerprint.arrangement)
        if plan is not None:
            job.idea += text_budget.words(plan)


def _plain_rows(job: DesignJob) -> list[tuple[str, str, str]]:
    """The job's facts as (figure, heading, body). A figure is the fact's own
    value; a date is not a figure, and goes with the words."""

    content, rows = job.content, []
    for item in (content.get("items") or content.get("rows") or content.get("metrics")
                 or content.get("steps") or content.get("milestones") or []):
        if not isinstance(item, dict):
            continue
        figure = str(item.get("value") or item.get("tag") or "")
        head = str(item.get("heading") or item.get("headline") or item.get("label") or "")
        body = str(item.get("body") or item.get("detail") or item.get("note") or "")
        date = str(item.get("date") or "")
        if date and date not in head and date not in body:
            head = f"{date} · {head}" if head else date
        rows.append((figure[:12], head, body))
    return rows or [("", content.get("takeaway") or job.title or "", "")]


def _plain_variant(job: DesignJob, nearby: set[str]) -> str:
    """Which plain page suits this content, avoiding what nearby pages already use."""

    rows = _plain_rows(job)
    highlight = (job.content.get("highlight") or {}).get("value") or ""
    fits = []
    if highlight and any(char.isdigit() for char in highlight) and len(highlight) <= 10:
        fits.append("poster")
    if 2 <= len(rows) <= 4:
        fits.append("columns")
    fits.append("ledger")
    return next((variant for variant in fits if variant not in nearby), fits[0])


def _plain_html(job: DesignJob, variant: str = "ledger") -> str:
    from ppt_agent.v2.htmldeck import structural

    content = job.content
    highlight = content.get("highlight") or {}
    return structural.plain(
        title=job.title, kicker=job.kicker, source=job.source, page_number=job.page_number,
        rows=_plain_rows(job), note=content.get("takeaway"), variant=variant,
        highlight=(str(highlight["value"]), str(highlight.get("text") or "")) if highlight.get("value") else None)


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


def _fingerprint(job: DesignJob, idea: str | None = None) -> str:
    """Identifies the content and form a saved page was designed for.

    ``idea`` is the brief the saved page was written to, which may carry a
    reviewer's note the fresh job does not have."""

    payload = json.dumps([job.content, job.source, job.kicker, job.title, job.form,
                          (job.idea if idea is None else idea) if job.form else ""],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def _replay(session, theme, font_css, job: DesignJob, html: str, *, recheck: bool = False,
                  trusted: bool = False) -> PageResult:
    """Rebuild a page from HTML saved by an earlier run; no model call.

    A page that no longer fits comes back not ok with what is wrong in
    ``defects``. With ``recheck`` a page that still fits is also held to the
    current aesthetic and diagram rules: what it fails goes in ``aesthetic``."""

    result = PageResult(page_number=job.page_number, page=None, html=html)
    body, _ = sanitize.clean(html)
    try:
        measured = await _measure(session, theme, font_css, body, job=job)
    except MeasureFailed:
        return result
    tier = str(job.content.get("tier") or "standard") if recheck else None
    found = find_defects(measured, None, tier=tier, roles=job.type_roles and not trusted) + (
        graphic_checks.find(body, job.content) if recheck else [])
    hard = [defect.message for defect in found if defect.kind not in SOFT]
    if hard and not trusted:  # a signed-off page is not judged by rules written after it was signed
        result.defects = hard
        return result
    try:
        result.page = to_page(measured, page_number=job.page_number, role=job.role,
                              section=job.section, title=job.title, notes=job.notes)
    except (ConvertError, ValueError):
        return result
    result.aesthetic = [defect.message for defect in found] if recheck else []
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
    extra_css: str = "",
) -> tuple[dict[int, PageResult], dict[str, Any]]:
    """Return a result per page number, and a summary for the run report.

    Raises ``browser.BrowserUnavailable`` when no browser can be started.
    """

    if not hasattr(client, "complete_text"):
        raise browser.BrowserUnavailable("this model client cannot return plain text")
    theme = themes.get(theme_name)
    if extra_css:
        theme = dataclasses.replace(theme, css=theme.css + extra_css)  # rules the run was begun under
    font_css, real_fonts = office_font_css()
    plan: list[layout_plan.Fingerprint] = []
    accepted = set((checkpoints.load(ACCEPTED) or {}).get("pages", [])) if checkpoints is not None else set()
    held: set[int] = set()  # accepted pages replayed untouched in this run
    contents = {job.page_number: {**job.content, "archetype": job.archetype} for job in jobs}
    if theme.vocabulary == 2:
        # Compose the whole deck first: every page gets a form and a fingerprint,
        # checked against all the pages before it.
        saved = checkpoints.load("html/layout_plan.json") if checkpoints is not None else None
        if saved and [item["page"] for item in saved] == sorted(contents):
            # Keep the saved compositions; re-plan only pages whose form no longer suits the content.
            before = [layout_plan.Fingerprint(**item) for item in saved]
            plan = layout_plan.repair(before, contents)
            for index, old in enumerate(before):
                # A signed-off page keeps the composition it was designed to, while its content stands.
                job = next((job for job in jobs if job.page_number == old.page), None)
                cached = checkpoints.load(f"html/page_{old.page:03d}.json") if old.page in accepted else None
                if job is None or not cached or plan[index] == old:
                    continue
                _apply(job, old)
                if cached.get("content") in (_fingerprint(job), _fingerprint(job, cached.get("idea"))):
                    plan[index] = old
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
    plain_used: dict[int, str] = {}

    def _other_forms(page_number: int) -> list[str]:
        """Forms this page could also take, leaving out its own and its neighbours'."""

        current = next((item for item in plan if item.page == page_number), None)
        if current is None:
            return []
        nearby = {item.form for item in plan if 0 < abs(item.page - page_number) <= 3}
        return [form for form in layout_plan.candidates(contents[page_number])
                if form != current.form and form not in nearby]

    def _recompose(job: DesignJob, form: str) -> None:
        index = next(i for i, item in enumerate(plan) if item.page == job.page_number)
        focus = "right" if plan[index].focus == "left" else "left"
        plan[index] = layout_plan.Fingerprint(page=job.page_number, form=form, focus=focus)
        _apply(job, plan[index])

    async with browser.Session(concurrency=max(2, min(concurrency, 6))) as session:

        async def one(job: DesignJob) -> None:
            name = f"html/page_{job.page_number:03d}.json"
            cached = checkpoints.load(name) if checkpoints is not None else None
            async with gate:
                result = None
                if cached and cached.get("html") and cached.get("content") in (
                        _fingerprint(job), _fingerprint(job, cached.get("idea"))):
                    job.idea = cached.get("idea") or job.idea
                    studio = theme.vocabulary == 2
                    if job.page_number in accepted:
                        saved = await _replay(session, theme, font_css, job, cached["html"], trusted=True)
                        saved.attempts = 0
                        if saved.ok:
                            results[job.page_number] = saved
                            held.add(job.page_number)
                            return
                    saved = await _replay(session, theme, font_css, job, cached["html"], recheck=studio)
                    saved.attempts = 0
                    if saved.ok and not saved.aesthetic:
                        results[job.page_number] = saved
                        return
                    if studio and (saved.aesthetic or saved.defects):
                        # The saved page fails rules written since: repair it where it stands
                        # rather than design the page again from nothing.
                        result = await design_page(
                            client, session, theme, font_css, job, look=False, seed_html=cached["html"],
                            seed_problems=(saved.defects or saved.aesthetic)[:6])
                        if saved.ok and not (result.ok and len(result.aesthetic) < len(saved.aesthetic)):
                            results[job.page_number] = saved  # the repair was no better: keep what was there
                            return
                        progress(f"[design] page {job.page_number} repaired from its saved version")
                    elif saved.ok:
                        results[job.page_number] = saved
                        return
                if result is None or not result.ok:
                    if job.type_roles and os.environ.get("PPT_AGENT_LAYOUT_IR") == "1" and job.role == "content":
                        # Experimental: the model plans the structure and the program lays it out.
                        from ppt_agent.v2.htmldeck.layout_ir import design_page_ir
                        result = await design_page_ir(client, session, theme, font_css, job)
                    else:
                        result = await design_page(client, session, theme, font_css, job, look=look)
            if not result.ok and theme.vocabulary == 2:
                failed = result
                if checkpoints is not None:
                    checkpoints.save(f"html/failed_{job.page_number:03d}.json",
                                     {"html": result.html, "defects": result.defects, "idea": job.idea})
                if job.form == "chart":
                    # The page's verified data is kept: try the chart arranged another way, the
                    # side-by-side arrangement last, before any form without the chart.
                    index = next(i for i, item in enumerate(plan) if item.page == job.page_number)
                    tried = plan[index].arrangement or "side"
                    others = [option for option in layout_plan.arrangements_for(contents[job.page_number])
                              if option != tried]
                    for option in sorted(others, key=lambda option: option == "side")[:2]:
                        plan[index] = dataclasses.replace(plan[index], arrangement=option)
                        _apply(job, plan[index])
                        async with gate:
                            again = await design_page(client, session, theme, font_css, job, look=look)
                        again.attempts += result.attempts
                        result = again
                        if result.ok:
                            break
                # The content did not fit this form: compose it another way before giving up on design.
                for form in ([] if result.ok else _other_forms(job.page_number)[:OTHER_FORMS]):
                    _recompose(job, form)
                    async with gate:
                        again = await design_page(client, session, theme, font_css, job, look=look)
                    again.attempts += result.attempts
                    result = again
                    if result.ok:
                        break
            if not result.ok and theme.vocabulary == 2 and job.type_roles and len(job.content.get("items") or []) > 2:
                # Less on the page before a plain one: the two most important items stay, the rest go to
                # the notes, and the page is designed once more in its planned form.
                index = next(i for i, item in enumerate(plan) if item.page == job.page_number)
                keep_items, keep_notes = job.all_items, job.all_notes
                full = list(keep_items if keep_items is not None else job.content.get("items") or [])
                job.all_items = full[:2]
                job.all_notes = ((job.notes + "\n") if job.notes else "") + "页面未展开的要点：" + "；".join(
                    " ".join(str(item.get(key)) for key in ("heading", "value", "body") if item.get(key))
                    for item in full[2:])
                _apply(job, plan[index])
                async with gate:
                    again = await design_page(client, session, theme, font_css, job, look=look)
                again.attempts += result.attempts
                if again.ok:
                    again.reduced = True
                    result = again
                else:
                    job.all_items, job.all_notes = keep_items, keep_notes
                    _apply(job, plan[index])
            if not result.ok and theme.vocabulary == 2:
                # Rather than a page from the other layout engine, a plain page in this theme,
                # arranged unlike the plain pages near it. It is not counted a success: it waits for a person.
                nearby = {name for number, name in plain_used.items() if 0 < abs(number - job.page_number) <= 3}
                variant = _plain_variant(job, nearby)
                plain_used[job.page_number] = variant
                async with gate:
                    template = await _replay(session, theme, font_css, job, _plain_html(job, variant))
                    for body in (_plain_html(job, variant).replace('class="page', 'class="page compact', 1),
                                 _plain_html(job, "ledger").replace('class="page', 'class="page compact', 1)):
                        if not template.ok:
                            template = await _replay(session, theme, font_css, job, body)
                if template.ok:
                    template.attempts, template.defects = result.attempts, failed.defects
                    template.templated = True
                    result = template
                    if checkpoints is not None and cached:
                        # The saved design no longer stands; do not start the next run from it.
                        checkpoints.save(name, {"html": "", "content": "", "idea": job.idea})
            results[job.page_number] = result
            if checkpoints is not None and result.ok and not result.templated:
                checkpoints.save(name, {"html": result.html, "content": _fingerprint(job),
                                        "attempts": result.attempts, "idea": job.idea,
                                        "review_problems": result.review_problems, "trace": result.trace})
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

            async def redo(job: DesignJob, why: str, *, new_form: bool, form: str | None = None) -> None:
                if job.page_number in held:
                    return  # signed off: not the review's to change
                options = _other_forms(job.page_number)
                if form is not None and form in options:
                    options = [form]  # the reviewer named a form this content can take
                elif not new_form:
                    options = []
                if options:
                    _recompose(job, options[0])
                    job.idea += "审稿意见：" + why
                elif new_form:
                    return
                else:
                    job.idea += "审稿意见（请据此调整，形式不变）：" + why
                before = results[job.page_number]
                async with gate:
                    after = await design_page(client, session, theme, font_css, job, look=look)
                # A redesign replaces the page only if it is no rougher than what it replaces.
                kept = after.ok and (not before.ok or before.templated or len(after.aesthetic) <= len(before.aesthetic))
                if kept:
                    results[job.page_number] = after
                    if checkpoints is not None:
                        checkpoints.save(f"html/page_{job.page_number:03d}.json",
                                         {"html": after.html, "content": _fingerprint(job),
                                          "attempts": before.attempts + after.attempts, "idea": job.idea,
                                          "review_problems": [why]})
                redesigned.append({"page": job.page_number, "why": why, "kept": kept, "form": job.form})
                progress(f"[design] page {job.page_number} redesigned ({why[:40]})")

            # Pages whose outlines nearly coincide, anywhere in the deck: recompose the later one.
            pairs = signature.lookalikes(outlines())
            # A page built around its chart or figure looks like other such pages, and should: what it
            # shows was decided by its content, and is not recomposed for looking like another.
            later_pages = sorted({later for _, later, _ in pairs
                                  if not by_page[later].content.get("visual")})[: max(2, len(jobs) // 5)]
            await asyncio.gather(*(redo(by_page[number], "和另一页的版面轮廓几乎相同", new_form=True)
                                   for number in later_pages))

            # Composition, not chart type: pages near each other divided and weighted alike, charts included.
            def compositions() -> dict[int, composition.Composition]:
                return {number: composition.of(result.page) for number, result in results.items()
                        if number in by_page and result.ok and not result.templated}

            async def rearrange(job: DesignJob, partner: int) -> None:
                index = next(i for i, item in enumerate(plan) if item.page == job.page_number)
                current = plan[index]
                theirs = next((item.arrangement for item in plan if item.page == partner), "")
                def drawn(page_job: DesignJob | None, planned: str) -> str:
                    # A page replayed from an earlier run was drawn to the idea it was saved with.
                    if page_job is None:
                        return planned or "side"
                    return next((name for name, words in prompts.ARRANGEMENT_WORDS.items() if words in page_job.idea),
                                "side")

                near = [number for number in by_page if number != job.page_number
                        and abs(number - job.page_number) <= composition.WINDOW and results[number].ok]
                taken = {drawn(job, current.arrangement), drawn(by_page.get(partner), theirs)}
                taken |= {drawn(by_page[number], "") for number in near if by_page[number].form == "chart"}
                possible = layout_plan.arrangements_for(contents[job.page_number])
                options = [option for option in possible if option not in taken]
                # When the neighbours use up the rest, an arrangement only the clashing page lacks; the
                # measured distance below still decides whether it is kept.
                options += [option for option in possible if option not in options
                            and option not in (drawn(job, current.arrangement), drawn(by_page.get(partner), theirs))]
                if not options:
                    return
                before = results[job.page_number]
                def nearest(page) -> float:
                    # Turning away from one neighbour must not turn into another.
                    return min([composition.distance(composition.of(page), composition.of(results[number].page))
                                for number in near] or [1.0])

                gap_before = nearest(before.page)
                tried, kept, after, gap_after = [], False, before, 0.0
                for option in options[:2]:  # a second arrangement if the first cannot be drawn
                    tried.append(option)
                    plan[index] = dataclasses.replace(current, arrangement=option)
                    _apply(job, plan[index])
                    async with gate:
                        after = await design_page(client, session, theme, font_css, job, look=look)
                    gap_after = nearest(after.page) if after.ok else 0.0
                    kept = after.ok and gap_after > gap_before and len(after.aesthetic) <= len(before.aesthetic)
                    if kept:
                        break
                    if checkpoints is not None and not after.ok:
                        checkpoints.save(f"html/failed_{job.page_number:03d}_{option}.json",
                                         {"html": after.html, "defects": after.defects, "idea": job.idea})
                if kept:
                    results[job.page_number] = after
                    if checkpoints is not None:
                        checkpoints.save(f"html/page_{job.page_number:03d}.json",
                                         {"html": after.html, "content": _fingerprint(job),
                                          "attempts": before.attempts + after.attempts, "idea": job.idea,
                                          "review_problems": [f"和第 {partner} 页构图相同"]})
                else:
                    plan[index] = current
                    _apply(job, current)
                redesigned.append({"page": job.page_number, "why": f"和第 {partner} 页构图相同", "kept": kept,
                                   "form": job.form, "arrangement": tried,
                                   "distance": [gap_before, round(gap_after, 3)]})
                progress(f"[design] page {job.page_number} recomposed ({'/'.join(tried)}, kept={kept})")

            done = {entry["page"] for entry in redesigned}
            targets: dict[int, int] = {}
            for earlier, later, _ in composition.close_pairs(compositions()):
                page, partner = (later, earlier) if later not in held else (earlier, later)
                if page in held or page in done or page in targets:
                    continue
                if by_page[page].form != "chart" and by_page[page].content.get("visual"):
                    continue  # a page built around its source figure keeps its form; nothing else to turn
                targets[page] = partner
            chosen = dict(list(targets.items())[: max(2, len(jobs) // 5)])
            await asyncio.gather(*(
                rearrange(by_page[page], partner) if by_page[page].form == "chart"
                else redo(by_page[page], f"和第 {partner} 页的构图（空间分割、重心、比例）几乎相同", new_form=True)
                for page, partner in chosen.items()))
            if look:
                # The whole deck at a glance: which pages look alike, which one is weakest.
                sheet = contact_sheet({number: result.screenshot_png for number, result in results.items()
                                       if number in by_page})
                allowed = {number: [item.form for item in plan if item.page == number] + _other_forms(number)
                           for number in by_page}
                for entry in await review.matrix(client, sheet, allowed=allowed):
                    job = by_page.get(entry["page"])
                    if job is not None:
                        await redo(job, entry["problem"], new_form=False, form=entry.get("form"))
            if checkpoints is not None:
                checkpoints.save("html/layout_plan.json", layout_plan.as_dicts(plan))

    remade = sorted(accepted & {job.page_number for job in jobs} - held)
    if remade and checkpoints is not None:
        # Its content changed, so it was designed again: what was signed off is no longer what is there.
        saved = checkpoints.load(ACCEPTED) or {}
        checkpoints.save(ACCEPTED, {**saved, "pages": [page for page in saved.get("pages", []) if page not in remade]})
    designed = [job.page_number for job in jobs
                if results[job.page_number].ok and not results[job.page_number].templated]
    summary = {
        "theme": theme_name,
        "office_fonts": real_fonts,
        "designed_pages": len(designed),
        "accepted_pages": sorted(held),
        "no_longer_accepted": remade,
        "fell_back_pages": sorted(job.page_number for job in jobs if not results[job.page_number].ok),
        "templated_pages": sorted(job.page_number for job in jobs if results[job.page_number].templated),
        "pending_review_pages": sorted(job.page_number for job in jobs
                                       if results[job.page_number].templated or not results[job.page_number].ok),
        "reduced_pages": sorted(job.page_number for job in jobs if getattr(results[job.page_number], "reduced", False)),
        "structural_fell_back": sorted(spec["page_number"] for spec in structural
                                       if not results[spec["page_number"]].ok),
        "model_calls": sum(results[job.page_number].attempts for job in jobs),
        "plain_variants": {str(number): name for number, name in sorted(plain_used.items())},
        "aesthetic_left": {str(job.page_number): results[job.page_number].aesthetic for job in jobs
                           if results[job.page_number].aesthetic},
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
            composition=composition.rhythm({job.page_number: composition.of(results[job.page_number].page)
                                            for job in jobs if results[job.page_number].ok}),
        )
    return results, summary


def renumber(page: Any, shown: int) -> Any:
    """The page with the number it shows in the footer set to ``shown`` (pages were removed before it)."""

    from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH

    old = f"{page.page_number:02d}"
    elements = []
    for element in page.elements:
        if (getattr(element, "type", None) == "rich_text" and element.frame.y > CANVAS_HEIGHT * 0.85
                and element.frame.x > CANVAS_WIDTH * 0.6 and "".join(run.text for run in element.runs).strip() == old):
            runs = [element.runs[0].model_copy(update={"text": f"{shown:02d}"})]
            element = element.model_copy(update={"runs": runs})
        elements.append(element)
    return page.model_copy(update={"elements": elements, "page_number": shown})
