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
from collections import Counter
import json
import os
import shutil
import tempfile
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
from ppt_agent.v2.providers import LLMClient, encode_image_for_vision
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


class TypesetRevisionPlan(StrictModel):
    reply: str = Field(..., min_length=1)
    profile: str | None = None
    pages: list[PageRevisionInstruction] = Field(default_factory=list)
    unsupported_reason: str | None = None



async def _revise_typeset_deck(**kwargs) -> ReviseResult:
    """Stage all outputs: failed layout/render leaves the approved deck intact."""
    output_root = kwargs["output_root"]
    with tempfile.TemporaryDirectory(prefix=".typeset-revise-", dir=output_root.parent) as temporary:
        stage = Path(temporary) / "deck"
        shutil.copytree(output_root, stage)
        staged_kwargs = {
            **kwargs, "output_root": stage,
            "checkpoints": _Checkpoints(stage / "checkpoints", resume=True),
        }
        result = await _revise_typeset_deck_staged(**staged_kwargs)
        # Absolute paths in generated reports must refer to the lasting job.
        changed = {}
        for source in stage.rglob("*"):
            if not source.is_file():
                continue
            data = source.read_bytes()
            if source.suffix == ".json":
                data = data.replace(str(stage).encode(), str(output_root).encode())
            destination = output_root / source.relative_to(stage)
            previous = destination.read_bytes() if destination.is_file() else None
            if data != previous:
                changed[destination] = (data, previous)
        committed = []
        try:
            for destination, (data, previous) in changed.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as pending:
                    pending.write(data)
                    pending_name = pending.name
                try:
                    os.replace(pending_name, destination)
                finally:
                    Path(pending_name).unlink(missing_ok=True)
                committed.append(destination)
        except Exception:
            for destination in reversed(committed):
                previous = changed[destination][1]
                if previous is None:
                    destination.unlink(missing_ok=True)
                else:
                    destination.write_bytes(previous)
            raise
        return result.model_copy(update={
            "pptx_path": str(output_root / Path(result.pptx_path).relative_to(stage))
        })


async def _revise_typeset_deck_staged(
    *,
    output_root: Path,
    deck_name: str,
    message: str,
    client: LLMClient,
    checkpoints: _Checkpoints,
    config: dict[str, Any],
    selected_pages: list[int] | None,
    attachment_paths: list[str] | None,
    concurrency: int,
    progress: Progress,
) -> ReviseResult:
    from ppt_agent.v2.visual.profiles import PROFILES

    brief = _load_checkpoint_model(checkpoints, "brief.json", ContentBrief, "brief")
    skeleton = _load_checkpoint_model(
        checkpoints, "skeleton_with_briefs.json", DeckSkeleton, "page plan"
    )
    slots = {slot.page_number: slot for slot in skeleton.slots}
    current_profile = config.get("profile")
    if current_profile not in PROFILES:
        raise RevisionError("档案无效；只能使用 consulting / launch / training / corporate 四种风格。")
    if attachment_paths:
        raise RevisionError("排版模式暂不支持修改时新增附件；请修改内容页文字或切换四种风格。")
    if selected_pages and any(number not in slots or slots[number].kind != "content" for number in selected_pages):
        raise RevisionError("排版模式只支持修改内容页；封面、目录、章节分隔和结尾页暂不能单独修改。")
    summary_slots = []
    for slot in skeleton.slots:
        cached = checkpoints.load(f"typeset/content_{slot.page_number:03d}.json")
        if slot.kind == "content" and cached is not None:
            current_title = cached["content"]["title"]
            current_brief = (slot.brief or PageBrief(title=current_title)).model_copy(
                update={"title": current_title}
            )
            slot = slot.model_copy(update={"brief": current_brief})
        summary_slots.append(slot)
    current_summary = _deck_summary(skeleton.model_copy(update={"slots": summary_slots}))
    payload = await client.complete_json(
        task="typeset_revision_plan",
        system=(
            "Plan an executable typeset-deck revision. Return JSON: "
            '{"reply":string,"profile":null|"consulting"|"launch"|"training"|"corporate",'
            '"pages":[{"page_number":integer,"instruction":string}],"unsupported_reason":null|string}. '
            "Only rewrite content-page text and/or switch among the four profiles. "
            "Coordinates, image changes, custom palettes, adding/removing pages and structural-page "
            "edits are unsupported: set unsupported_reason and return no actions. "
            "Preserve all numbers unless supported by the user request or supplied evidence. "
            "Acknowledge planned actions, never claim already completed. "
            "If selected pages are supplied, only target those content pages."
        ),
        user=f"Current profile: {current_profile}\n{current_summary}\nSelected pages: {selected_pages}\nRequest: {message}",
        context={"message": message, "selected_pages": selected_pages, "profile": current_profile},
    )
    try:
        plan = TypesetRevisionPlan.model_validate(payload)
    except ValidationError as exc:
        raise RevisionError("修改规划无效；只支持内容页改写及四种风格切换。") from exc
    if plan.unsupported_reason:
        raise RevisionError(f"当前排版模式不能完成该修改：{plan.unsupported_reason}")
    if plan.profile is not None and plan.profile not in PROFILES:
        raise RevisionError("只能在 consulting / launch / training / corporate 四种风格之间切换。")
    profile_name = plan.profile or current_profile
    theme_changed = profile_name != current_profile
    page_numbers = [item.page_number for item in plan.pages]
    if len(set(page_numbers)) != len(page_numbers):
        raise RevisionError("同一内容页不能重复修改。")
    if any(number not in slots or slots[number].kind != "content" for number in page_numbers):
        raise RevisionError("规划包含不支持的页码；仅能修改内容页。")
    if selected_pages and not set(page_numbers).issubset(selected_pages):
        raise RevisionError("修改规划超出所选内容页。")
    if not plan.pages and not theme_changed:
        raise RevisionError("没有可执行的实际改动；请指定内容页改写，或切换到另一种风格档案。")

    # Reuse the generation pipeline so every content page is typeset in order,
    # with the same history and content-only QA as an original build.
    from ppt_agent.v2.orchestrator import BuildRequest
    from ppt_agent.v2.typeset_pipeline import build_typeset_deck, generate_content

    profile = PROFILES[profile_name]
    updated_slots = list(skeleton.slots)
    originals = {
        number: checkpoints.load(f"typeset/content_{number:03d}.json")
        for number in page_numbers
    }
    try:
        for item in plan.pages:
            slot = slots[item.page_number]
            if item.new_brief is not None:
                slot = slot.model_copy(update={"brief": item.new_brief})
                updated_slots[item.page_number - 1] = slot
            evidence = None
            evidence_data = checkpoints.load("evidence_store.json")
            if evidence_data is not None:
                from ppt_agent.v2.evidence import EvidenceStore
                current = (originals[slot.page_number] or {}).get("content") or {}
                query = " ".join([slot.brief.title if slot.brief else "", slot.section_title or "",
                                  current.get("title", ""), item.instruction or message])
                evidence = EvidenceStore.from_dict(evidence_data).select(query, section_query=slot.section_title)
            content, record = await generate_content(
                client, checkpoints, slot, brief, profile,
                evidence=evidence,
                revision_instruction=item.instruction or message, force_regenerate=True,
                current_content=(originals[slot.page_number] or {}).get("content"),
                source_references=config.get("source_references", []),
                source_page_counts=config.get("source_page_counts", {}),
            )
            original = originals[slot.page_number]
            if original is not None and content.model_dump(mode="json") == original["content"]:
                raise RevisionError(f"第 {slot.page_number} 页模型没有产生实际内容变化，未应用修改。")
            if record["fallback"]:
                raise RevisionError(f"第 {slot.page_number} 页内容生成校验失败；未应用该修改，请缩短要求后重试。")
    except Exception:
        # A failed later page must not leave a hidden partial content edit.
        for number, original in originals.items():
            name = f"typeset/content_{number:03d}.json"
            if original is not None:
                checkpoints.save(name, original)
            else:
                (checkpoints.root / name).unlink(missing_ok=True)
        raise
    skeleton = skeleton.model_copy(update={"slots": updated_slots})
    checkpoints.save("skeleton_with_briefs.json", skeleton.model_dump(mode="json"))
    checkpoints.save("skeleton.json", skeleton.model_dump(mode="json"))
    request = BuildRequest(
        prompt=message, page_count=skeleton.total_pages, language=brief.language,
        output_dir=str(output_root), deck_name=deck_name, resume=True,
        concurrency=concurrency, layout_engine="typeset", style_profile=profile_name,
    )
    previous_report_path = output_root / f"{deck_name}_run_report.json"
    previous_records = {}
    if previous_report_path.is_file():
        previous_report = json.loads(previous_report_path.read_text(encoding="utf-8"))
        previous_records = {
            record["page_number"]: record
            for record in previous_report.get("typeset_pages", [])
        }
    result = await build_typeset_deck(request, client, checkpoints, brief, skeleton, progress=progress)
    if result.pptx_path is None:
        raise RevisionError("修改后未通过质量检查，未导出新版 PPTX；请查看运行报告。")
    if theme_changed:
        updated_brief = brief.model_copy(update={
            "deck_type": profile_name,
            "deck_type_reason": "用户在成片修改中要求切换风格档案。",
        })
        checkpoints.save("brief.json", updated_brief.model_dump(mode="json"))
    qa = json.loads(Path(result.qa_report_path).read_text(encoding="utf-8"))
    report_path = Path(result.run_report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    affected = {slot.page_number for slot in skeleton.content_slots()} if theme_changed else set(page_numbers)
    for record in report.get("typeset_pages", []):
        previous = previous_records.get(record["page_number"], {})
        dropped_notes = Counter(
            str(note) for note in record.get("notes", [])
            if "dropped " in str(note).lower()
        )
        previous_drops = Counter(
            str(note) for note in previous.get("notes", [])
            if "dropped " in str(note).lower()
        )
        errors = Counter(map(str, record.get("layout_errors", [])))
        previous_errors = Counter(map(str, previous.get("layout_errors", [])))
        has_loss = record.get("fallback") or errors or record.get("data_loss") or dropped_notes
        new_loss = (
            (record.get("fallback") and not previous.get("fallback"))
            or bool(errors - previous_errors)
            or (record.get("data_loss") and not previous.get("data_loss"))
            or bool(dropped_notes - previous_drops)
        )
        if (record["page_number"] in affected and has_loss) or new_loss:
            raise RevisionError(
                f"第 {record['page_number']} 页排版发生退化或内容丢失，未应用本次修改。"
            )
    report["revision"] = {
        "revised_pages": sorted(page_numbers),
        "theme_changed": theme_changed,
        "previous_profile": current_profile,
        "profile": profile_name,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    labels = "、".join(map(str, sorted(page_numbers)))
    actions = []
    if page_numbers:
        actions.append(f"已重新生成第 {labels} 页内容")
    if theme_changed:
        actions.append(f"已切换为 {profile.label} 风格")
    return ReviseResult(
        reply="；".join(actions) + "，并按页序重新排版导出。",
        revised_pages=sorted(page_numbers), theme_changed=theme_changed,
        qa_error_pages=qa.get("pages_with_errors", 0), pptx_path=result.pptx_path,
        usage=client.usage.snapshot(),
    )


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
    config = checkpoints.load("typeset_config.json")
    if config and config.get("layout_engine") == "typeset":
        return await _revise_typeset_deck(
            output_root=output_root, deck_name=deck_name, message=message, client=client,
            checkpoints=checkpoints, config=config, selected_pages=selected_pages,
            attachment_paths=attachment_paths, concurrency=concurrency, progress=progress,
        )

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
                    images=[encode_image_for_vision(path)],
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
