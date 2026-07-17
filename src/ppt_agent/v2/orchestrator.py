"""The v2 build orchestrator: prompt/docs -> 100-page editable PPTX in one run.

Stage graph (each stage checkpoints to disk, so --resume skips finished work):

  intake -> brief -> theme -> outline/skeleton -> page briefs (parallel per
  section) -> page designs (parallel per page, semaphore-bounded) -> QA +
  one repair round -> assemble -> render

Long-deck resilience rules:
- one page = one small model call (survives 120s proxy read timeouts),
- a failed page degrades to a deterministic archetype page, never a hole,
- a blown budget switches remaining pages to archetypes instead of aborting.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field, ValidationError

from ppt_agent.models import StrictModel
from ppt_agent.v2 import prompts
from ppt_agent.v2.anchors import (
    anchor_variant_seed,
    build_closing_page,
    build_cover_page,
    build_section_divider,
    build_toc_pages,
)
from ppt_agent.v2.design import (
    BUILTIN_THEMES,
    ThemeSpec,
    get_builtin_theme,
    normalize_theme,
    relative_luminance,
)
from ppt_agent.v2.fallback import design_fallback_page
from ppt_agent.v2.intake import IntakeResult, ingest_sources
from ppt_agent.v2.ir import (
    ChartItem,
    DeckDesign,
    PageDesign,
    TableItem,
    TextItem,
    normalize_page_payload,
)
from ppt_agent.v2.planning import (
    MAX_PAGES,
    MIN_PAGES,
    ContentBrief,
    DeckOutline,
    DeckSkeleton,
    PageBrief,
    PageSlot,
    build_skeleton,
    parse_page_briefs,
    reconcile_page_briefs,
    section_start_pages,
)
from ppt_agent.v2.providers import BudgetExceededError, LLMClient
from ppt_agent.v2.qa import DeckQASummary, PageQAResult, review_page, summarize
from ppt_agent.v2.render import render_deck
from ppt_agent.v2.search import SearchProvider, format_search_digest


Progress = Callable[[str], None]

PageStatus = Literal["anchor", "model", "repaired", "fallback"]


class BuildRequest(StrictModel):
    prompt: str = Field(..., min_length=1)
    page_count: int = Field(default=100, ge=MIN_PAGES, le=MAX_PAGES)
    language: str | None = Field(
        default=None, description="Force the slide language; default follows the prompt."
    )
    source_paths: list[str] = Field(default_factory=list)
    enable_search: bool = False
    theme: str = Field(
        default="auto",
        description="'auto' lets the model design a palette; otherwise a builtin name.",
    )
    output_dir: str = Field(..., min_length=1)
    deck_name: str = "deck"
    resume: bool = False
    concurrency: int = Field(default=8, ge=1, le=32)
    budget_usd: float | None = Field(default=15.0, gt=0)
    repair_rounds: int = Field(default=1, ge=0, le=2)
    qa_gate: Literal["strict", "lenient"] = Field(
        default="strict",
        description=(
            "strict: a page still failing QA after repair is replaced by an "
            "archetype page (no broken page ever ships). lenient: keep the "
            "model's page and mark the run completed_with_qa_errors."
        ),
    )


class PageOutcome(StrictModel):
    page_number: int
    status: PageStatus
    model_attempts: int = 0
    error_issues: int = 0
    warning_issues: int = 0
    note: str | None = None


class BuildResult(StrictModel):
    status: Literal[
        "succeeded",
        "succeeded_with_fallbacks",
        "completed_with_qa_errors",
        "quality_gate_failed",
    ]
    pptx_path: str | None
    deck_design_path: str
    qa_report_path: str
    run_report_path: str
    page_count: int
    model_pages: int
    repaired_pages: int
    fallback_pages: int
    usage: dict[str, Any]
    stage_seconds: dict[str, float]


class _Checkpoints:
    """JSON-file checkpoint store under <output_dir>/checkpoints."""

    def __init__(self, root: Path, *, resume: bool) -> None:
        self.root = root
        self.resume = resume
        (root / "pages").mkdir(parents=True, exist_ok=True)

    def load(self, name: str) -> Any | None:
        if not self.resume:
            return None
        path = self.root / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def save(self, name: str, payload: Any) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


async def _run_brief_stage(
    request: BuildRequest,
    client: LLMClient,
    checkpoints: _Checkpoints,
    intake: IntakeResult,
    search_digest: str | None,
) -> ContentBrief:
    cached = checkpoints.load("brief.json")
    if cached is not None:
        return ContentBrief.model_validate(cached)
    payload = await client.complete_json(
        task="brief",
        system=prompts.BRIEF_SYSTEM,
        user=prompts.build_brief_user_prompt(
            request.prompt,
            page_count=request.page_count,
            source_digest=intake.digest or None,
            search_digest=search_digest,
        ),
        context={"user_prompt": request.prompt, "page_count": request.page_count},
    )
    brief = ContentBrief.model_validate(payload)
    updates: dict[str, Any] = {}
    if request.language:
        updates["language"] = request.language
    if intake.digest or search_digest:
        combined = "\n\n".join(part for part in (intake.digest, search_digest) if part)
        updates["source_digest"] = combined[:24000]
    if updates:
        brief = brief.model_copy(update=updates)
    checkpoints.save("brief.json", brief.model_dump(mode="json"))
    return brief


async def _run_theme_stage(
    request: BuildRequest, client: LLMClient, checkpoints: _Checkpoints, brief: ContentBrief
) -> ThemeSpec:
    cached = checkpoints.load("theme.json")
    if cached is not None:
        return ThemeSpec.model_validate(cached)
    if request.theme != "auto":
        theme = get_builtin_theme(request.theme)
    else:
        try:
            payload = await client.complete_json(
                task="theme",
                system=prompts.THEME_SYSTEM,
                user=prompts.build_theme_user_prompt(brief),
                context={"brief": brief.model_dump(mode="json")},
            )
            theme = normalize_theme(ThemeSpec.model_validate(payload))
        except (ValidationError, ValueError):
            theme = get_builtin_theme(_pick_builtin_for(brief))
    checkpoints.save("theme.json", theme.model_dump(mode="json"))
    return theme


def _pick_builtin_for(brief: ContentBrief) -> str:
    text = f"{brief.topic} {brief.tone}".lower()
    if any(keyword in text for keyword in ("环保", "农业", "健康", "green", "sustain")):
        return "forest"
    if any(keyword in text for keyword in ("金融", "投资", "董事", "finance", "board")):
        return "slate"
    if any(keyword in text for keyword in ("消费", "营销", "品牌", "consumer", "marketing")):
        return "sunrise"
    return "aurora"


async def _run_outline_stage(
    request: BuildRequest, client: LLMClient, checkpoints: _Checkpoints, brief: ContentBrief
) -> DeckSkeleton:
    cached = checkpoints.load("skeleton.json")
    if cached is not None:
        return DeckSkeleton.model_validate(cached)
    content_budget = max(3, request.page_count - 8)
    payload = await client.complete_json(
        task="outline",
        system=prompts.OUTLINE_SYSTEM,
        user=prompts.build_outline_user_prompt(brief, content_budget=content_budget),
        context={"brief": brief.model_dump(mode="json"), "content_budget": content_budget},
    )
    outline = DeckOutline.model_validate(payload)
    skeleton = build_skeleton(
        outline, total_pages=request.page_count, language=brief.language
    )
    checkpoints.save("skeleton.json", skeleton.model_dump(mode="json"))
    return skeleton


async def _run_page_brief_stage(
    client: LLMClient,
    checkpoints: _Checkpoints,
    brief: ContentBrief,
    skeleton: DeckSkeleton,
    *,
    concurrency: int,
    progress: Progress,
) -> DeckSkeleton:
    cached = checkpoints.load("skeleton_with_briefs.json")
    if cached is not None:
        return DeckSkeleton.model_validate(cached)

    semaphore = asyncio.Semaphore(concurrency)
    sections = skeleton.outline.sections
    slots_by_section: dict[int, list[PageSlot]] = {}
    for slot in skeleton.slots:
        if slot.kind == "content" and slot.section_index is not None:
            slots_by_section.setdefault(slot.section_index, []).append(slot)

    async def briefs_for(section_index: int) -> tuple[int, list[PageBrief]]:
        section = sections[section_index - 1]
        page_count = len(slots_by_section.get(section_index, []))
        if page_count == 0:
            return section_index, []
        async with semaphore:
            try:
                payload = await client.complete_json(
                    task="section_pages",
                    system=prompts.SECTION_PAGES_SYSTEM,
                    user=prompts.build_section_pages_user_prompt(
                        brief,
                        section,
                        page_count=page_count,
                        deck_title=skeleton.deck_title,
                        prior_titles=[
                            earlier.title
                            for earlier in sections[: section_index - 1]
                        ],
                    ),
                    context={
                        "section": section.model_dump(mode="json"),
                        "page_count": page_count,
                        "brief": brief.model_dump(mode="json"),
                    },
                )
                briefs = parse_page_briefs(payload)
            except (ValidationError, ValueError) as exc:
                progress(
                    f"[briefs] section {section_index} fell back to talking points ({exc})"
                )
                briefs = [
                    PageBrief(title=point, summary=section.goal, layout_hint="auto")
                    for point in section.talking_points[:page_count]
                ]
        return section_index, reconcile_page_briefs(
            briefs, page_count, section_title=section.title
        )

    results = await asyncio.gather(
        *(briefs_for(index) for index in sorted(slots_by_section))
    )
    briefs_map = dict(results)
    new_slots: list[PageSlot] = []
    cursor: dict[int, int] = {}
    for slot in skeleton.slots:
        if slot.kind == "content" and slot.section_index is not None:
            position = cursor.get(slot.section_index, 0)
            cursor[slot.section_index] = position + 1
            slot = slot.model_copy(
                update={"brief": briefs_map[slot.section_index][position]}
            )
        new_slots.append(slot)
    enriched = skeleton.model_copy(update={"slots": new_slots})
    checkpoints.save("skeleton_with_briefs.json", enriched.model_dump(mode="json"))
    return enriched


async def _design_content_page(
    client: LLMClient,
    checkpoints: _Checkpoints,
    brief: ContentBrief,
    theme: ThemeSpec,
    skeleton: DeckSkeleton,
    slot: PageSlot,
    *,
    semaphore: asyncio.Semaphore,
    repair_rounds: int,
    qa_gate: str,
    progress: Progress,
    revision_instruction: str | None = None,
    current_page_json: str | None = None,
) -> tuple[PageDesign, PageQAResult, PageOutcome]:
    checkpoint_name = f"pages/page_{slot.page_number:03d}.json"
    cached = checkpoints.load(checkpoint_name)
    if cached is not None:
        page = PageDesign.model_validate(cached["page"])
        qa_result = PageQAResult.model_validate(cached["qa"])
        outcome = PageOutcome.model_validate(cached["outcome"])
        return page, qa_result, outcome

    page_brief = slot.brief or PageBrief(title=slot.section_title or "Untitled")
    neighbor_titles = _neighbor_titles(skeleton, slot)
    context = {
        "revision_instruction": revision_instruction,
        "page_brief": page_brief.model_dump(mode="json"),
        "page_number": slot.page_number,
        "section_title": slot.section_title,
        "language": brief.language,
    }

    async def call_model() -> PageDesign:
        user_prompt = prompts.build_page_design_user_prompt(
            brief=brief,
            theme=theme,
            deck_title=skeleton.deck_title,
            section_title=slot.section_title or "",
            page_brief=page_brief,
            page_number=slot.page_number,
            total_pages=skeleton.total_pages,
            neighbor_titles=neighbor_titles,
        )
        if revision_instruction:
            user_prompt = prompts.append_revision_block(
                user_prompt,
                instruction=revision_instruction,
                current_page_json=current_page_json,
            )
        payload = await client.complete_json(
            task="page_design",
            system=prompts.build_page_design_system(brief.language),
            user=user_prompt,
            context=context,
        )
        normalized = normalize_page_payload(dict(payload), page_number=slot.page_number)
        normalized.setdefault("section", slot.section_title)
        return PageDesign.model_validate(normalized)

    status: PageStatus = "model"
    attempts = 0
    page: PageDesign | None = None
    async with semaphore:
        for attempt in range(2):
            attempts += 1
            try:
                page = await call_model()
                break
            except BudgetExceededError:
                progress(
                    f"[design] budget reached; page {slot.page_number} uses the archetype library"
                )
                break
            except (ValidationError, ValueError, RuntimeError) as exc:
                progress(
                    f"[design] page {slot.page_number} attempt {attempt + 1} failed: "
                    f"{str(exc)[:160]}"
                )
        if page is None:
            status = "fallback"
            page = design_fallback_page(
                page_brief,
                page_number=slot.page_number,
                section_title=slot.section_title,
                language=brief.language,
            )

        page, qa_result = review_page(page, theme)
        if qa_result.errors and status == "model" and repair_rounds > 0:
            try:
                repair_payload = await client.complete_json(
                    task="page_repair",
                    system=prompts.REPAIR_SYSTEM,
                    user=prompts.build_repair_user_prompt(
                        page.model_dump_json(),
                        [issue.message for issue in qa_result.errors],
                    ),
                    context={"page_payload": page.model_dump(mode="json")},
                )
                repaired = PageDesign.model_validate(
                    normalize_page_payload(dict(repair_payload), page_number=slot.page_number)
                )
                repaired, repaired_qa = review_page(repaired, theme)
                if len(repaired_qa.errors) < len(qa_result.errors):
                    page, qa_result = repaired, repaired_qa
                    status = "repaired"
            except (ValidationError, ValueError, RuntimeError, BudgetExceededError) as exc:
                progress(
                    f"[repair] page {slot.page_number} repair skipped: {str(exc)[:160]}"
                )

        # QA gate: in strict mode a page that still fails after repair is
        # replaced by an archetype page, so no broken page ever ships.
        note: str | None = None
        if qa_result.errors and status != "fallback" and qa_gate == "strict":
            note = (
                f"replaced by QA gate: {len(qa_result.errors)} unresolved error(s) — "
                + "; ".join(issue.code for issue in qa_result.errors)
            )
            progress(
                f"[qa-gate] page {slot.page_number} replaced by archetype "
                f"({len(qa_result.errors)} unresolved errors)"
            )
            status = "fallback"
            page = design_fallback_page(
                page_brief,
                page_number=slot.page_number,
                section_title=slot.section_title,
                language=brief.language,
            )
            page, qa_result = review_page(page, theme)

        # A user-approved spoken script always wins over the model's own notes,
        # and is checkpointed so rebuilds (revisions) keep it.
        approved_notes = (slot.brief.speaker_notes if slot.brief else "").strip()
        if approved_notes:
            page = page.model_copy(update={"speaker_notes": approved_notes})

    outcome = PageOutcome(
        page_number=slot.page_number,
        status=status,
        model_attempts=attempts,
        error_issues=len(qa_result.errors),
        warning_issues=len(qa_result.issues) - len(qa_result.errors),
        note=note,
    )
    checkpoints.save(
        checkpoint_name,
        {
            "page": page.model_dump(mode="json"),
            "qa": qa_result.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
        },
    )
    return page, qa_result, outcome


def _neighbor_titles(skeleton: DeckSkeleton, slot: PageSlot) -> list[str]:
    titles: list[str] = []
    for other in skeleton.slots:
        if other.kind != "content" or other.brief is None:
            continue
        if 0 < abs(other.page_number - slot.page_number) <= 2:
            titles.append(other.brief.title)
    return titles


def _build_anchor_pages(
    skeleton: DeckSkeleton, brief: ContentBrief, theme: ThemeSpec
) -> dict[int, PageDesign]:
    """Deterministic anchor pages. Only the TOC remains code-generated; cover,
    dividers and closing are model-designed via _design_anchor_page."""

    anchors: dict[int, PageDesign] = {}
    toc_entries = section_start_pages(skeleton)
    toc_slots = [slot for slot in skeleton.slots if slot.kind == "toc"]
    if toc_slots:
        toc_pages = build_toc_pages(
            start_page_number=toc_slots[0].page_number,
            sections=toc_entries,
            language=brief.language,
            theme=theme,
        )
        for page in toc_pages[: len(toc_slots)]:
            anchors[page.page_number] = page
    return anchors


def _anchor_fallback_page(
    slot: PageSlot, skeleton: DeckSkeleton, brief: ContentBrief, theme: ThemeSpec
) -> PageDesign:
    """Archetype fallback for a structural page, variant chosen per deck."""

    variant = anchor_variant_seed(skeleton.deck_title)
    if slot.kind == "cover":
        return build_cover_page(
            page_number=slot.page_number,
            deck_title=skeleton.deck_title,
            subtitle=skeleton.subtitle or brief.subtitle,
            language=brief.language,
            theme=theme,
            variant=variant,
        )
    if slot.kind == "closing":
        return build_closing_page(
            page_number=slot.page_number,
            deck_title=skeleton.deck_title,
            language=brief.language,
            theme=theme,
            variant=variant,
        )
    section = skeleton.outline.sections[(slot.section_index or 1) - 1]
    return build_section_divider(
        page_number=slot.page_number,
        section_index=slot.section_index or 1,
        section_count=len(skeleton.outline.sections),
        section_title=section.title,
        section_goal=section.goal or None,
        language=brief.language,
        theme=theme,
        variant=variant,
    )


_PAGE_DESIGN_FIELDS = frozenset(PageDesign.model_fields.keys())


def _anchor_design_rejection(page: PageDesign, kind: str, theme: ThemeSpec) -> str | None:
    """Why a model-designed structural page must be replaced; None if it passes.

    Structural pages have one job: read as a cover / divider / closing at a
    glance. A page full of small text, diagrams or a pale washed-out backdrop
    reads as a content page, so the deterministic archetype wins instead.
    """

    if any(isinstance(element, (ChartItem, TableItem)) for element in page.elements):
        return "charts/tables do not belong on structural pages"
    if len(page.elements) > 12:
        return f"{len(page.elements)} elements are too busy for a structural page"
    texts = [element for element in page.elements if isinstance(element, TextItem)]
    if len(texts) > 5:
        return f"{len(texts)} text blocks read like a content page"
    if not any(
        text.role in ("display", "section") or (text.size_pt or 0) >= 40 for text in texts
    ):
        return "no hero title (display/section role)"
    if kind in ("cover", "closing") and theme.dark_cover:
        backdrop = (
            page.background_gradient.start
            if page.background_gradient is not None
            else page.background
        )
        if relative_luminance(theme.palette.resolve(backdrop)) > 0.55:
            return "cover/closing needs a strong dark hero backdrop"
    return None


async def _design_anchor_page(
    client: LLMClient,
    checkpoints: _Checkpoints,
    brief: ContentBrief,
    theme: ThemeSpec,
    skeleton: DeckSkeleton,
    slot: PageSlot,
    *,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    revision_instruction: str | None = None,
    current_page_json: str | None = None,
) -> tuple[PageDesign, PageQAResult, PageOutcome]:
    """Model-design one structural page (cover/divider/closing) with the same
    QA + archetype-fallback guarantees as content pages."""

    checkpoint_name = f"pages/page_{slot.page_number:03d}.json"
    cached = checkpoints.load(checkpoint_name)
    if cached is not None:
        page = PageDesign.model_validate(cached["page"])
        qa_result = PageQAResult.model_validate(cached["qa"])
        outcome = PageOutcome.model_validate(cached["outcome"])
        return page, qa_result, outcome

    section = (
        skeleton.outline.sections[(slot.section_index or 1) - 1]
        if slot.kind == "section_divider"
        else None
    )

    async def call_model() -> PageDesign:
        user_prompt = prompts.build_anchor_design_user_prompt(
            kind=slot.kind,
            brief=brief,
            theme=theme,
            deck_title=skeleton.deck_title,
            subtitle=skeleton.subtitle or brief.subtitle,
            section_index=slot.section_index,
            section_count=len(skeleton.outline.sections),
            section_title=section.title if section else None,
            section_goal=(section.goal or None) if section else None,
        )
        if revision_instruction:
            user_prompt = prompts.append_revision_block(
                user_prompt,
                instruction=revision_instruction,
                current_page_json=current_page_json,
            )
        payload = await client.complete_json(
            task="anchor_design",
            system=prompts.build_anchor_design_system(brief.language),
            user=user_prompt,
            context={
                "revision_instruction": revision_instruction,
                "kind": slot.kind,
                "page_number": slot.page_number,
                "deck_title": skeleton.deck_title,
                "section_index": slot.section_index,
                "section_count": len(skeleton.outline.sections),
                "section_title": section.title if section else None,
                "language": brief.language,
            },
        )
        normalized = normalize_page_payload(dict(payload), page_number=slot.page_number)
        normalized = {
            key: value for key, value in normalized.items() if key in _PAGE_DESIGN_FIELDS
        }
        normalized["page_number"] = slot.page_number
        normalized["role"] = slot.kind
        normalized["show_chrome"] = False
        if section is not None:
            normalized["section"] = section.title
        normalized.setdefault("title", section.title if section else skeleton.deck_title)
        return PageDesign.model_validate(normalized)

    status: PageStatus = "model"
    attempts = 0
    page: PageDesign | None = None
    async with semaphore:
        for attempt in range(2):
            attempts += 1
            try:
                page = await call_model()
                break
            except BudgetExceededError:
                progress(
                    f"[design] budget reached; anchor page {slot.page_number} uses the archetype library"
                )
                break
            except (ValidationError, ValueError, RuntimeError) as exc:
                progress(
                    f"[design] anchor page {slot.page_number} attempt {attempt + 1} failed: "
                    f"{str(exc)[:160]}"
                )
        if page is None:
            status = "anchor"
            page = _anchor_fallback_page(slot, skeleton, brief, theme)

        # Identity gate: a structural page that does not read as one is
        # replaced outright — repair cannot fix a wrong concept.
        if status == "model":
            rejection = _anchor_design_rejection(page, slot.kind, theme)
            if rejection:
                progress(
                    f"[anchor-gate] page {slot.page_number} ({slot.kind}) uses the "
                    f"archetype library: {rejection}"
                )
                status = "anchor"
                page = _anchor_fallback_page(slot, skeleton, brief, theme)

        page, qa_result = review_page(page, theme)
        if qa_result.errors and status == "model":
            try:
                repair_payload = await client.complete_json(
                    task="page_repair",
                    system=prompts.REPAIR_SYSTEM,
                    user=prompts.build_repair_user_prompt(
                        page.model_dump_json(),
                        [issue.message for issue in qa_result.errors],
                    ),
                    context={"page_payload": page.model_dump(mode="json")},
                )
                repaired = PageDesign.model_validate(
                    normalize_page_payload(dict(repair_payload), page_number=slot.page_number)
                )
                repaired, repaired_qa = review_page(repaired, theme)
                if len(repaired_qa.errors) < len(qa_result.errors):
                    page, qa_result = repaired, repaired_qa
                    status = "repaired"
            except (ValidationError, ValueError, RuntimeError, BudgetExceededError) as exc:
                progress(
                    f"[repair] anchor page {slot.page_number} repair skipped: {str(exc)[:160]}"
                )

        note: str | None = None
        if qa_result.errors and status != "anchor":
            note = (
                f"replaced by QA gate: {len(qa_result.errors)} unresolved error(s) — "
                + "; ".join(issue.code for issue in qa_result.errors)
            )
            progress(
                f"[qa-gate] anchor page {slot.page_number} replaced by archetype "
                f"({len(qa_result.errors)} unresolved errors)"
            )
            status = "anchor"
            page = _anchor_fallback_page(slot, skeleton, brief, theme)
            page, qa_result = review_page(page, theme)

    outcome = PageOutcome(
        page_number=slot.page_number,
        status=status,
        model_attempts=attempts,
        error_issues=len(qa_result.errors),
        warning_issues=len(qa_result.issues) - len(qa_result.errors),
        note=note,
    )
    checkpoints.save(
        checkpoint_name,
        {
            "page": page.model_dump(mode="json"),
            "qa": qa_result.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
        },
    )
    return page, qa_result, outcome


async def build_deck_async(
    request: BuildRequest,
    client: LLMClient,
    *,
    search_provider: SearchProvider | None = None,
    progress: Progress = print,
) -> BuildResult:
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoints = _Checkpoints(output_dir / "checkpoints", resume=request.resume)
    stage_seconds: dict[str, float] = {}

    def timed(stage: str):
        start = time.perf_counter()

        def done() -> None:
            stage_seconds[stage] = round(time.perf_counter() - start, 2)
            progress(f"[stage] {stage} finished in {stage_seconds[stage]}s")

        return done

    finish = timed("intake")
    intake = ingest_sources(request.source_paths) if request.source_paths else IntakeResult()
    for warning in intake.warnings:
        progress(f"[intake] {warning}")
    search_digest: str | None = None
    if request.enable_search and search_provider is not None:
        try:
            results = await search_provider.search(request.prompt, max_results=6)
            search_digest = format_search_digest(results) or None
        except Exception as exc:  # noqa: BLE001 - research is best-effort
            progress(f"[search] skipped: {exc}")
    finish()

    finish = timed("brief")
    brief = await _run_brief_stage(request, client, checkpoints, intake, search_digest)
    finish()
    progress(f"[brief] '{brief.deck_title}' | language={brief.language}")

    finish = timed("theme")
    theme = await _run_theme_stage(request, client, checkpoints, brief)
    finish()
    progress(f"[theme] {theme.name} (motif: {theme.motif})")

    finish = timed("outline")
    skeleton = await _run_outline_stage(request, client, checkpoints, brief)
    finish()
    progress(
        f"[outline] {len(skeleton.outline.sections)} sections / "
        f"{skeleton.total_pages} pages"
    )

    finish = timed("page_briefs")
    skeleton = await _run_page_brief_stage(
        client,
        checkpoints,
        brief,
        skeleton,
        concurrency=request.concurrency,
        progress=progress,
    )
    finish()

    finish = timed("page_designs")
    semaphore = asyncio.Semaphore(request.concurrency)
    content_slots = skeleton.content_slots()
    anchor_slots = [
        slot
        for slot in skeleton.slots
        if slot.kind in ("cover", "section_divider", "closing")
    ]
    completed = 0
    lock = asyncio.Lock()

    async def design_with_progress(
        slot: PageSlot,
    ) -> tuple[PageDesign, PageQAResult, PageOutcome]:
        nonlocal completed
        page, qa_result, outcome = await _design_content_page(
            client,
            checkpoints,
            brief,
            theme,
            skeleton,
            slot,
            semaphore=semaphore,
            repair_rounds=request.repair_rounds,
            qa_gate=request.qa_gate,
            progress=progress,
        )
        async with lock:
            completed += 1
            if completed % 10 == 0 or completed == len(content_slots):
                progress(f"[design] {completed}/{len(content_slots)} content pages done")
        return page, qa_result, outcome

    async def design_anchor_with_progress(
        slot: PageSlot,
    ) -> tuple[PageDesign, PageQAResult, PageOutcome]:
        page, qa_result, outcome = await _design_anchor_page(
            client,
            checkpoints,
            brief,
            theme,
            skeleton,
            slot,
            semaphore=semaphore,
            progress=progress,
        )
        progress(f"[design] anchor page {slot.page_number} ({slot.kind}) ready")
        return page, qa_result, outcome

    design_results = await asyncio.gather(
        *(design_with_progress(slot) for slot in content_slots),
        *(design_anchor_with_progress(slot) for slot in anchor_slots),
    )
    finish()

    finish = timed("assemble_qa")
    anchors = _build_anchor_pages(skeleton, brief, theme)
    pages_by_number: dict[int, PageDesign] = {}
    qa_results: list[PageQAResult] = []
    outcomes: list[PageOutcome] = []
    # Anchor pages go through the same QA as model pages so the report truly
    # covers every page of the deck.
    for page_number, anchor in sorted(anchors.items()):
        reviewed, anchor_qa = review_page(anchor, theme)
        pages_by_number[page_number] = reviewed
        qa_results.append(anchor_qa)
        outcomes.append(
            PageOutcome(
                page_number=page_number,
                status="anchor",
                error_issues=len(anchor_qa.errors),
                warning_issues=len(anchor_qa.issues) - len(anchor_qa.errors),
            )
        )
    for page, qa_result, outcome in design_results:
        pages_by_number[page.page_number] = page
        qa_results.append(qa_result)
        outcomes.append(outcome)

    qa_results.sort(key=lambda result: result.page_number)
    outcomes.sort(key=lambda item: item.page_number)

    deck = DeckDesign(
        deck_title=skeleton.deck_title,
        subtitle=skeleton.subtitle,
        language=brief.language,
        theme=theme,
        pages=[pages_by_number[number] for number in sorted(pages_by_number)],
    )
    finish()

    qa_summary = summarize(
        qa_results,
        repaired=[item.page_number for item in outcomes if item.status == "repaired"],
        fallback=[item.page_number for item in outcomes if item.status == "fallback"],
    )
    deck_design_path = output_dir / f"{request.deck_name}_design.json"
    deck_design_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    qa_report_path = output_dir / f"{request.deck_name}_qa_report.json"
    qa_report_path.write_text(qa_summary.model_dump_json(indent=2), encoding="utf-8")

    fallback_count = sum(1 for item in outcomes if item.status == "fallback")
    error_page_count = sum(1 for result in qa_results if result.errors)
    quality_gate_passed = error_page_count == 0
    pptx_path: Path | None = None
    if quality_gate_passed or request.qa_gate == "lenient":
        finish = timed("render")
        pptx_path = render_deck(deck, output_dir / f"{request.deck_name}.pptx")
        finish()

    if not quality_gate_passed and request.qa_gate == "strict":
        final_status = "quality_gate_failed"
    elif not quality_gate_passed:
        final_status = "completed_with_qa_errors"
    elif fallback_count:
        final_status = "succeeded_with_fallbacks"
    else:
        final_status = "succeeded"

    run_report = {
        "request": request.model_dump(mode="json"),
        "usage": client.usage.snapshot(),
        "stage_seconds": stage_seconds,
        "outcomes": [item.model_dump(mode="json") for item in outcomes],
        "intake_warnings": intake.warnings,
        "quality_gate": {
            "mode": request.qa_gate,
            "passed": quality_gate_passed,
            "pages_with_errors": error_page_count,
            "pptx_generated": pptx_path is not None,
        },
    }
    run_report_path = output_dir / f"{request.deck_name}_run_report.json"
    run_report_path.write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return BuildResult(
        status=final_status,
        pptx_path=str(pptx_path) if pptx_path is not None else None,
        deck_design_path=str(deck_design_path),
        qa_report_path=str(qa_report_path),
        run_report_path=str(run_report_path),
        page_count=len(deck.pages),
        model_pages=sum(1 for item in outcomes if item.status in ("model", "repaired")),
        repaired_pages=sum(1 for item in outcomes if item.status == "repaired"),
        fallback_pages=fallback_count,
        usage=client.usage.snapshot(),
        stage_seconds=stage_seconds,
    )


def build_deck(
    request: BuildRequest,
    client: LLMClient,
    *,
    search_provider: SearchProvider | None = None,
    progress: Progress = print,
) -> BuildResult:
    """Synchronous wrapper for CLI/API callers."""

    return asyncio.run(
        build_deck_async(
            request, client, search_provider=search_provider, progress=progress
        )
    )


class PlanResult(StrictModel):
    """Output of the standalone planning phase (no page designs yet)."""

    brief: ContentBrief
    skeleton: DeckSkeleton
    usage: dict[str, Any]


async def plan_deck_async(
    request: BuildRequest,
    client: LLMClient,
    *,
    search_provider: SearchProvider | None = None,
    progress: Progress = print,
) -> PlanResult:
    """Run only the planning stages: intake -> brief -> outline -> page briefs.

    Checkpoints land in <output_dir>/checkpoints using the same file names as
    build_deck_async, so an approved plan can seed a later generation run that
    resumes past every planning stage.
    """

    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoints = _Checkpoints(output_dir / "checkpoints", resume=request.resume)

    intake = ingest_sources(request.source_paths) if request.source_paths else IntakeResult()
    for warning in intake.warnings:
        progress(f"[intake] {warning}")
    search_digest: str | None = None
    if request.enable_search and search_provider is not None:
        try:
            results = await search_provider.search(request.prompt, max_results=6)
            search_digest = format_search_digest(results) or None
        except Exception as exc:  # noqa: BLE001 - research is best-effort
            progress(f"[search] skipped: {exc}")

    brief = await _run_brief_stage(request, client, checkpoints, intake, search_digest)
    progress(f"[brief] '{brief.deck_title}' | language={brief.language}")
    skeleton = await _run_outline_stage(request, client, checkpoints, brief)
    progress(
        f"[outline] {len(skeleton.outline.sections)} sections / {skeleton.total_pages} pages"
    )
    skeleton = await _run_page_brief_stage(
        client,
        checkpoints,
        brief,
        skeleton,
        concurrency=request.concurrency,
        progress=progress,
    )
    progress("[plan] page briefs ready for review")
    return PlanResult(brief=brief, skeleton=skeleton, usage=client.usage.snapshot())


def plan_deck(
    request: BuildRequest,
    client: LLMClient,
    *,
    search_provider: SearchProvider | None = None,
    progress: Progress = print,
) -> PlanResult:
    """Synchronous wrapper for CLI/API callers."""

    return asyncio.run(
        plan_deck_async(
            request, client, search_provider=search_provider, progress=progress
        )
    )
