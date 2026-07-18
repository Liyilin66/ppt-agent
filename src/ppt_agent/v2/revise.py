"""Post-generation revision: chat-driven edits to an already generated deck.

A revision runs against the job's checkpoint directory:

  plan (one model call: which pages change, how) ->
  optional theme revision (palette/style tokens; recolors the whole deck for
  free because pages only reference color roles) ->
  forced redesign of the affected pages (same QA + fallback path as the
  original build) ->
  reassemble every page, re-run QA, re-render the PPTX in place.

Page structure (add/remove pages) is deliberately out of scope — that is what
the pre-generation outline step is for.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from ppt_agent.models import StrictModel
from ppt_agent.v2 import prompts
from ppt_agent.v2.design import ThemeSpec, normalize_theme
from ppt_agent.v2.ir import DeckDesign, PageDesign
from ppt_agent.v2.intake import ingest_sources
from ppt_agent.v2.orchestrator import (
    PageOutcome,
    Progress,
    _build_anchor_pages,
    _Checkpoints,
    _design_anchor_page,
    _design_content_page,
    _image_dimensions,
    _stage_image_assets,
)
from ppt_agent.v2.planning import ContentBrief, DeckSkeleton, PageBrief, PageSlot
from ppt_agent.v2.providers import LLMClient
from ppt_agent.v2.qa import PageQAResult, review_page, summarize
from ppt_agent.v2.render import render_deck


class PageRevisionInstruction(StrictModel):
    page_number: int = Field(..., ge=1)
    instruction: str = ""
    new_brief: PageBrief | None = None


class RevisionPlan(StrictModel):
    reply: str = Field(..., min_length=1)
    theme_instruction: str | None = None
    all_pages_instruction: str | None = Field(
        default=None,
        description=(
            "A single design directive applied to EVERY page (except TOC), for "
            "requests like 'remove the left rail on every page'."
        ),
    )
    pages: list[PageRevisionInstruction] = Field(default_factory=list)


class ReviseResult(StrictModel):
    reply: str
    revised_pages: list[int]
    theme_changed: bool
    qa_error_pages: int
    pptx_path: str | None
    usage: dict[str, Any]


class RevisionError(RuntimeError):
    """The deck cannot be revised (missing checkpoints or invalid request)."""


def _deck_summary(skeleton: DeckSkeleton) -> str:
    lines = []
    for slot in skeleton.slots:
        if slot.kind == "content" and slot.brief is not None:
            label = slot.brief.title
        elif slot.kind == "section_divider":
            label = f"section divider — {slot.section_title}"
        elif slot.kind == "cover":
            label = f"cover — {skeleton.deck_title}"
        elif slot.kind == "closing":
            label = "closing"
        else:
            label = "table of contents"
        lines.append(f"P{slot.page_number:02d} [{slot.kind}] {label}")
    return "\n".join(lines)


def _load_checkpoint_model(checkpoints: _Checkpoints, name: str, model, what: str):
    payload = checkpoints.load(name)
    if payload is None:
        raise RevisionError(
            f"Missing checkpoint '{name}' — this job has no {what} to revise."
        )
    return model.model_validate(payload)


async def revise_deck_async(
    *,
    output_dir: str | Path,
    deck_name: str,
    message: str,
    client: LLMClient,
    selected_pages: list[int] | None = None,
    attachment_paths: list[str] | None = None,
    concurrency: int = 6,
    progress: Progress = print,
) -> ReviseResult:
    output_root = Path(output_dir)
    checkpoints = _Checkpoints(output_root / "checkpoints", resume=True)

    brief = _load_checkpoint_model(checkpoints, "brief.json", ContentBrief, "brief")
    theme = _load_checkpoint_model(checkpoints, "theme.json", ThemeSpec, "theme")
    skeleton = _load_checkpoint_model(
        checkpoints, "skeleton_with_briefs.json", DeckSkeleton, "page plan"
    )
    slot_map: dict[int, PageSlot] = {slot.page_number: slot for slot in skeleton.slots}

    # 0. Attachments: documents become planner reference text; images are
    # vision-digested and staged so redesigned pages can place them.
    from ppt_agent.v2.orchestrator import _IMAGE_MEDIA_TYPES

    attachment_paths = attachment_paths or []
    doc_paths = [
        item for item in attachment_paths
        if Path(item).suffix.lower() in (".pdf", ".docx", ".md", ".txt")
    ]
    image_paths = [
        item for item in attachment_paths
        if Path(item).suffix.lower() in _IMAGE_MEDIA_TYPES
    ]
    attachments_note = ""
    if doc_paths:
        digest = ingest_sources(doc_paths).digest.strip()
        if digest:
            attachments_note += (
                f"Reference documents uploaded with this request:\n{digest[:4000]}\n\n"
            )
    image_entries: list[dict[str, Any]] = list(checkpoints.load("image_digests.json") or [])
    if image_paths:
        import base64 as _base64

        staged = _stage_image_assets(image_paths, output_root)
        known = {entry["src"] for entry in image_entries}
        new_entries: list[dict[str, Any]] = []
        for name, path in staged.items():
            if name in known:
                continue
            width, height = _image_dimensions(path)
            entry: dict[str, Any] = {
                "src": name, "width": width, "height": height,
                "description": "", "extracted_text": "",
            }
            try:
                payload = await client.complete_json(
                    task="image_digest",
                    system=prompts.IMAGE_DIGEST_SYSTEM,
                    user=prompts.build_image_digest_user_prompt(
                        name=name, language=brief.language
                    ),
                    context={"name": name},
                    images=[
                        (
                            _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/png"),
                            _base64.b64encode(path.read_bytes()).decode("ascii"),
                        )
                    ],
                )
                entry["description"] = str(payload.get("description") or "").strip()
                entry["extracted_text"] = str(payload.get("extracted_text") or "").strip()
            except (ValidationError, ValueError, RuntimeError) as exc:
                progress(f"[revise] image digest for {name} skipped: {str(exc)[:120]}")
            image_entries.append(entry)
            new_entries.append(entry)
        if new_entries:
            checkpoints.save("image_digests.json", image_entries)
            attachments_note += (
                "New image assets uploaded with this request (pages may place them "
                "via the image element):\n"
                + "\n".join(
                    f"- {entry['src']}: {entry.get('description', '')}"
                    for entry in new_entries
                )
                + "\n\n"
            )
    available_images = [
        {
            "src": entry["src"],
            "description": entry.get("description", ""),
            "width": entry.get("width"),
            "height": entry.get("height"),
        }
        for entry in image_entries
    ] or None

    # 1. Plan the revision.
    plan_payload = await client.complete_json(
        task="revision_plan",
        system=prompts.REVISION_PLAN_SYSTEM,
        user=prompts.build_revision_plan_user_prompt(
            message=message,
            deck_summary=_deck_summary(skeleton),
            selected_pages=selected_pages,
            attachments_note=attachments_note,
        ),
        context={
            "message": message,
            "selected_pages": selected_pages,
            "deck_title": skeleton.deck_title,
            "total_pages": skeleton.total_pages,
        },
    )
    plan = RevisionPlan.model_validate(plan_payload)
    revisions = [
        item
        for item in plan.pages
        if item.page_number in slot_map and slot_map[item.page_number].kind != "toc"
    ]
    # A deck-wide page directive expands to every non-TOC page not already
    # covered by an explicit per-page entry.
    if plan.all_pages_instruction:
        explicit = {item.page_number for item in revisions}
        for slot in skeleton.slots:
            if slot.kind == "toc" or slot.page_number in explicit:
                continue
            revisions.append(
                PageRevisionInstruction(
                    page_number=slot.page_number,
                    instruction=plan.all_pages_instruction,
                )
            )
        revisions.sort(key=lambda item: item.page_number)
    progress(
        f"[revise] plan: {len(revisions)} page(s), "
        f"theme_instruction={'yes' if plan.theme_instruction else 'no'}, "
        f"all_pages={'yes' if plan.all_pages_instruction else 'no'}"
    )

    # 2. Deck-wide restyle: revise theme tokens (palette, motif, style, chrome);
    # pages pick the changes up at re-render time for free.
    theme_changed = False
    theme_change_labels: list[str] = []
    if plan.theme_instruction:
        try:
            theme_payload = await client.complete_json(
                task="theme_revise",
                system=prompts.THEME_REVISE_SYSTEM,
                user=prompts.build_theme_revise_user_prompt(
                    current_theme_json=theme.model_dump_json(),
                    instruction=plan.theme_instruction,
                ),
                context={
                    "theme": theme.model_dump(mode="json"),
                    "instruction": plan.theme_instruction,
                },
            )
            revised_theme = normalize_theme(ThemeSpec.model_validate(theme_payload))
            if revised_theme.palette != theme.palette:
                theme_change_labels.append("配色")
            if revised_theme.motif != theme.motif:
                theme_change_labels.append("母版装饰")
            if revised_theme.chrome != theme.chrome:
                theme_change_labels.append("页码/页脚显示")
            if revised_theme.style != theme.style:
                theme_change_labels.append("风格签名")
            if revised_theme != theme:
                theme = revised_theme
                checkpoints.save("theme.json", theme.model_dump(mode="json"))
                theme_changed = True
                progress(
                    f"[revise] theme updated ({', '.join(theme_change_labels) or 'minor'})"
                )
            else:
                progress("[revise] theme revision produced no actual change")
        except (ValidationError, ValueError, RuntimeError) as exc:
            progress(f"[revise] theme revision skipped: {str(exc)[:160]}")

    # Nothing executable: be honest and leave the deck untouched instead of
    # re-rendering an identical file and claiming success.
    if not revisions and not theme_changed:
        progress("[revise] no executable change; deck left untouched")
        existing_pptx = output_root / f"{deck_name}.pptx"
        return ReviseResult(
            reply=(
                f"{plan.reply}\n（本次没有产生实际改动——这个请求超出了当前可修改的范围，"
                "或规划结果与现状一致。PPTX 保持原样，你可以换一种说法，"
                "例如指定具体页码或要求整体换配色/隐藏页码。）"
            ),
            revised_pages=[],
            theme_changed=False,
            qa_error_pages=0,
            pptx_path=str(existing_pptx) if existing_pptx.is_file() else None,
            usage=client.usage.snapshot(),
        )

    # 3. Apply content-level brief rewrites to the skeleton.
    briefs_changed = False
    new_slots = list(skeleton.slots)
    for item in revisions:
        slot = slot_map[item.page_number]
        if item.new_brief is not None and slot.kind == "content":
            updated = slot.model_copy(update={"brief": item.new_brief})
            new_slots[updated.page_number - 1] = updated
            slot_map[updated.page_number] = updated
            briefs_changed = True
    if briefs_changed:
        skeleton = skeleton.model_copy(update={"slots": new_slots})
        payload = skeleton.model_dump(mode="json")
        checkpoints.save("skeleton.json", payload)
        checkpoints.save("skeleton_with_briefs.json", payload)

    # 4. Redesign the affected pages (forced: drop their checkpoints first).
    semaphore = asyncio.Semaphore(concurrency)

    async def redesign(item: PageRevisionInstruction) -> int:
        slot = slot_map[item.page_number]
        checkpoint_path = checkpoints.root / f"pages/page_{slot.page_number:03d}.json"
        current_page_json: str | None = None
        if checkpoint_path.is_file():
            try:
                current_page_json = json.dumps(
                    json.loads(checkpoint_path.read_text(encoding="utf-8"))["page"],
                    ensure_ascii=False,
                )
            except (ValueError, KeyError):
                current_page_json = None
            checkpoint_path.unlink()
        instruction = item.instruction or message
        if slot.kind == "content":
            await _design_content_page(
                client,
                checkpoints,
                brief,
                theme,
                skeleton,
                slot,
                semaphore=semaphore,
                repair_rounds=1,
                qa_gate="strict",
                progress=progress,
                revision_instruction=instruction,
                current_page_json=current_page_json,
                available_images=available_images,
            )
        else:
            await _design_anchor_page(
                client,
                checkpoints,
                brief,
                theme,
                skeleton,
                slot,
                semaphore=semaphore,
                progress=progress,
                revision_instruction=instruction,
                current_page_json=current_page_json,
            )
        progress(f"[revise] page {slot.page_number} redesigned")
        return slot.page_number

    revised_pages = sorted(await asyncio.gather(*(redesign(item) for item in revisions)))

    # 5. Reassemble the whole deck from checkpoints and re-render in place.
    existing_design: dict[int, dict[str, Any]] = {}
    design_path = output_root / f"{deck_name}_design.json"
    if design_path.is_file():
        try:
            for page in json.loads(design_path.read_text(encoding="utf-8")).get("pages", []):
                existing_design[int(page["page_number"])] = page
        except (ValueError, KeyError, TypeError):
            existing_design = {}

    pages_by_number: dict[int, PageDesign] = {}
    qa_results: list[PageQAResult] = []
    outcomes: list[PageOutcome] = []

    for page_number, anchor in sorted(_build_anchor_pages(skeleton, brief, theme).items()):
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

    for slot in skeleton.slots:
        if slot.kind == "toc":
            continue
        cached = checkpoints.load(f"pages/page_{slot.page_number:03d}.json")
        if cached is not None:
            page = PageDesign.model_validate(cached["page"])
            outcome = PageOutcome.model_validate(cached["outcome"])
        elif slot.page_number in existing_design:
            # Jobs generated before per-page anchor checkpoints existed.
            page = PageDesign.model_validate(existing_design[slot.page_number])
            outcome = PageOutcome(page_number=slot.page_number, status="anchor")
        else:
            raise RevisionError(
                f"Page {slot.page_number} has neither a checkpoint nor a stored design."
            )
        page, qa_result = review_page(page, theme)
        approved_notes = (slot.brief.speaker_notes if slot.brief else "").strip()
        if approved_notes:
            page = page.model_copy(update={"speaker_notes": approved_notes})
        pages_by_number[slot.page_number] = page
        qa_results.append(qa_result)
        outcomes.append(
            outcome.model_copy(
                update={
                    "error_issues": len(qa_result.errors),
                    "warning_issues": len(qa_result.issues) - len(qa_result.errors),
                }
            )
        )

    qa_results.sort(key=lambda result: result.page_number)
    outcomes.sort(key=lambda item: item.page_number)

    deck = DeckDesign(
        deck_title=skeleton.deck_title,
        subtitle=skeleton.subtitle,
        language=brief.language,
        theme=theme,
        pages=[pages_by_number[number] for number in sorted(pages_by_number)],
    )
    qa_summary = summarize(
        qa_results,
        repaired=[item.page_number for item in outcomes if item.status == "repaired"],
        fallback=[item.page_number for item in outcomes if item.status == "fallback"],
    )
    design_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    qa_report_path = output_root / f"{deck_name}_qa_report.json"
    qa_report_path.write_text(qa_summary.model_dump_json(indent=2), encoding="utf-8")

    qa_error_pages = sum(1 for result in qa_results if result.errors)
    pptx_path = render_deck(
        deck, output_root / f"{deck_name}.pptx", assets_dir=output_root / "assets"
    )
    progress(f"[revise] deck re-rendered: {pptx_path}")

    theme_summary = "、".join(theme_change_labels) or "视觉设置"
    reply = plan.reply
    if revised_pages and theme_changed:
        page_list = "、".join(str(number) for number in revised_pages)
        reply = f"{reply}\n（已更新第 {page_list} 页，调整了全局{theme_summary}，并重新渲染 PPTX。）"
    elif revised_pages:
        page_list = "、".join(str(number) for number in revised_pages)
        reply = f"{reply}\n（已更新第 {page_list} 页并重新渲染 PPTX。）"
    else:
        reply = f"{reply}\n（已调整全局{theme_summary}并重新渲染 PPTX。）"

    return ReviseResult(
        reply=reply,
        revised_pages=revised_pages,
        theme_changed=theme_changed,
        qa_error_pages=qa_error_pages,
        pptx_path=str(pptx_path),
        usage=client.usage.snapshot(),
    )


def revise_deck(
    *,
    output_dir: str | Path,
    deck_name: str,
    message: str,
    client: LLMClient,
    selected_pages: list[int] | None = None,
    attachment_paths: list[str] | None = None,
    concurrency: int = 6,
    progress: Progress = print,
) -> ReviseResult:
    """Synchronous wrapper for API callers."""

    return asyncio.run(
        revise_deck_async(
            output_dir=output_dir,
            deck_name=deck_name,
            message=message,
            client=client,
            selected_pages=selected_pages,
            attachment_paths=attachment_paths,
            concurrency=concurrency,
            progress=progress,
        )
    )
