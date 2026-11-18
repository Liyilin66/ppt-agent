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

from ppt_agent.v2.htmldeck import graphic_checks, prompts, review, sanitize, structural, themes
from ppt_agent.v2.htmldeck.browser import Measured, MeasureFailed, Session
from ppt_agent.v2.htmldeck.checks import AESTHETIC, Defect, allowed_numbers, find_defects
from ppt_agent.v2.htmldeck.convert import ConvertError, to_page
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.ir import PageDesign, PageRole

MAX_REWRITES = 2  # for defects that must not ship
MAX_POLISH = 2    # further tries once only soft defects are left
# Worth another try, not worth losing the page.
SOFT = frozenset({"sparse", "small_text"}) | AESTHETIC
FIT = frozenset({"overflow", "off_canvas", "overlap", "legibility"})  # too much for the space
DESIGN_TOKENS = 2600
PRUNE = ("这一页的内容比版面装得下的多，换排法已经试过了。这一次请删减：只保留标题、焦点和最重要的三条事实"
         "（每条一句话），其余条目和次要说明整条删掉；结论只留一句。不要缩小字号。")


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
    form: str | None = None  # studio theme: the visual form the deck plan assigned
    figure_file: str | None = None  # where the page's source figure is stored, when it has one
    type_roles: bool = False  # minimum sizes by what text does (runs begun under the P4 rules)
    all_items: list[dict[str, Any]] | None = None  # the content's items before a form's limit took some
    all_notes: str | None = None


@dataclass
class PageResult:
    page_number: int
    page: PageDesign | None
    html: str = ""
    attempts: int = 0
    reviewed: bool = False
    review_problems: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    aesthetic: list[str] = field(default_factory=list)  # soft defects the final version still has
    trace: list[dict[str, Any]] = field(default_factory=list)  # defect kinds found at each attempt
    screenshot_png: bytes = b""
    templated: bool = False  # built from the plain template after the designed page failed

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
    from ppt_agent.v2.htmldeck import charts

    series = charts.chartable(job.content) if job.form == "chart" or job.content.get("visual") else None
    drawn = any(item["kind"] == "chart" for item in measured.items)
    if series is not None and not drawn:
        missing.append(Defect("missing", "这一页要放图表，但页面上没有。请把给你的 data-chart 原样放进一个 chart 元件。"))
    if series is not None and drawn and _plain(charts.caption(series)) not in text:
        missing.append(Defect("missing", f"图表上方要有一行图注（类名 c），原样写：{charts.caption(series)}"))
    figure = job.content.get("figure") if job.form == "figure" else None
    shown = any(item["kind"] == "image" for item in measured.items)
    if figure and not shown:
        missing.append(Defect("missing", "这一页的形式是原文图版，但页面上没有图。请把给你的 plate 元件原样放进页面。"))
    if figure and shown and _plain(figure["caption"]) not in text:
        missing.append(Defect("missing", f"图的正下方要有一行图注（类名 c），原样写：{figure['caption']}"))
    need = (figure or {}).get("min_width")
    picture = next((item for item in measured.items if item["kind"] == "image"), None)
    if need and picture is not None and picture["w"] < need - 4:
        missing.append(Defect("bad_chart", f"原文图只有 {picture['w']:.0f} 像素宽，图里最小的字会看不清；至少要 {need:.0f} 像素宽，"
                                           "请让它所在的区域占 7 列并给足行数，其余内容放在剩下的 5 列里。"))
    diagram = job.content.get("diagram")
    if diagram:
        # A redrawn figure carries every word of the original, in the original's order.
        from ppt_agent.v2.htmldeck.prompts import diagram_words

        lost = [word for word in diagram_words(diagram) if _plain(word) not in text]
        if lost:
            missing.append(Defect("missing", "重画的图少了原图里的这些词，请原样补上：" + "、".join(lost[:8])))
        names = [_plain(level["name"]) for level in diagram.get("levels") or []]
        tops = []
        for name in names:
            hit = next((item for item in measured.items if item["kind"] == "text" and name in _plain(
                "".join(run.get("t", "") for run in item["runs"]))), None)
            tops.append(hit["text"]["y"] if hit else None)
        if None not in tops and tops != sorted(tops):
            missing.append(Defect("unsupported", "各层的上下顺序和原图不一样。请按给出的顺序自上而下排列。"))
        if _plain(diagram["caption"]) not in text:
            missing.append(Defect("missing", f"层级图下面要有一行图注（类名 c），原样写：{diagram['caption']}"))
    return missing


def _figures(job: "DesignJob | None") -> dict[str, str] | None:
    """The job's figure as a data URI, so the browser can show it."""

    import base64
    from pathlib import Path

    figure = (job.content.get("figure") if job is not None else None) or {}
    if not figure.get("id") or not job.figure_file or not Path(job.figure_file).is_file():
        return None
    return {figure["id"]: "data:image/png;base64," + base64.b64encode(Path(job.figure_file).read_bytes()).decode("ascii")}


async def _measure(session: Session, theme: themes.HtmlTheme, font_css: str, body: str,
                   *, backdrop: bool = True, job: "DesignJob | None" = None) -> Measured:
    return await session.measure(wrap(body, theme.css, font_css, theme.backdrop if backdrop else "",
                                      figures=_figures(job)))


async def design_page(
    client: Any,
    session: Session,
    theme: themes.HtmlTheme,
    font_css: str,
    job: DesignJob,
    *,
    look: bool = True,
    seed_html: str | None = None,
    seed_problems: list[str] | None = None,
) -> PageResult:
    """``seed_html`` with ``seed_problems`` starts from an existing page and asks
    for those problems to be fixed, instead of designing from nothing."""

    allowed = allowed_numbers([job.content, job.source, job.kicker, job.title, job.page_number])
    studio = theme.vocabulary == 2
    tier = str(job.content.get("tier") or "standard") if studio else None

    def check(body: str, measured: Measured) -> list[Defect]:
        found = find_defects(measured, allowed, tier=tier, roles=job.type_roles) + _required(job, measured)
        return found + (graphic_checks.find(body, job.content) if studio else [])

    system = prompts.system_prompt(theme)
    result = PageResult(page_number=job.page_number, page=None)
    best: tuple[str, Measured, list[Defect]] | None = None
    previous_html: str | None = seed_html if seed_problems else None
    problems: list[str] = list(seed_problems or []) if seed_html else []

    async def attempt() -> tuple[str, Measured, list[Defect]] | None:
        result.attempts += 1
        try:
            raw = await client.complete_text(
                task="html_page",
                system=system,
                user=prompts.user_prompt(
                    content=job.content, archetype=job.archetype, page_number=job.page_number,
                    kicker=job.kicker, source=job.source, idea=job.idea,
                    example_html=themes.studio_example(job.form) if job.form else None,
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
        try:
            measured = await _measure(session, theme, font_css, body, job=job)
        except MeasureFailed as error:
            result.defects.append(f"browser failed: {error}")
            return None
        defects = check(body, measured)
        result.trace.append({"attempt": result.attempts,
                             "kinds": sorted({defect.kind for defect in defects}),
                             "messages": [defect.message[:80] for defect in defects][:6]})
        if not any(defect.kind in FIT for defect in defects):
            return body, measured, defects
        # A few pixels too tall or too wide: repaired by geometry, measured again, at no model cost.
        # Room from empty rows first, then less air, then a smaller display figure; never smaller body text.
        from ppt_agent.v2.htmldeck import autofix

        def hard(found):
            return [defect for defect in found if defect.kind not in SOFT]

        best_here = (body, measured, defects)
        grown = autofix.grow(body)
        candidates = [grown, autofix.grow(grown)]
        if 'class="page compact' not in body:
            candidates += [variant.replace('class="page', 'class="page compact', 1) for variant in (body, *candidates)]
        smaller = [autofix.step_down(variant) for variant in candidates]
        candidates += smaller + [autofix.step_down(variant) for variant in smaller]
        for variant in dict.fromkeys(candidate for candidate in candidates if candidate != body):
            try:
                remeasured = await _measure(session, theme, font_css, variant, job=job)
            except MeasureFailed:
                continue
            again = check(variant, remeasured)
            if len(hard(again)) < len(hard(best_here[2])):
                best_here = (variant, remeasured, again)
            if not hard(again):
                result.trace[-1]["repaired"] = True
                return variant, remeasured, again
        return best_here

    rewrites, polish = MAX_REWRITES, MAX_POLISH
    while True:
        outcome = await attempt()
        if outcome is None:
            break
        body, measured, defects = outcome
        hard = [defect for defect in defects if defect.kind not in SOFT]
        if not hard and (best is None or len(defects) < len(best[2])):
            best = (body, measured, defects)  # of the pages that may ship, the one with least left to fix
        if not defects:
            break
        if hard:
            if not rewrites:
                break
            rewrites -= 1
        else:
            if not polish:
                break
            polish -= 1
        previous_html, problems = body, [defect.message for defect in defects][:6]
        result.defects = list(problems)
        if hard and not rewrites and any(defect.kind in FIT for defect in hard):
            # Last try, and it still does not fit: rearranging has failed, so cut.
            problems.insert(0, PRUNE)

    if best is None:
        result.html = previous_html or ""  # kept for diagnosis; the page itself is not used
        return result
    result.defects = []

    if look:
        seen = await review.look(client, best[1].screenshot_png, title=job.title)
        result.reviewed = True
        result.review_problems = seen
        if seen:
            previous_html, problems = best[0], seen
            outcome = await attempt()
            if outcome is not None:
                body, measured, defects = outcome
                # Keep the revision only if it broke nothing and left the page no rougher.
                if not [defect for defect in defects if defect.kind not in SOFT] and len(defects) <= len(best[2]):
                    best = (body, measured, defects)

    body, measured, left = best
    result.aesthetic = [defect.message for defect in left]
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
    try:
        measured = await _measure(session, theme, font_css, body, backdrop=kind == "toc")
    except MeasureFailed as error:
        result.defects.append(f"browser failed: {error}")
        return result
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
