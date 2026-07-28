"""Image understanding & editable page reconstruction.

One uploaded image becomes one slide, along a user-chosen route:

  rebuild              faithful editable reconstruction of a slide-like image
  design_from_content  new slide designed from the image's information
  embed_with_notes     the image itself placed as an exhibit + editable notes
  style_reference      a style-sample slide in the image's visual language
  extract_text         editable transcription of the image's text/data only

Honest editability: structure, text, charts and tables become native editable
elements; photographic regions stay images (the model marks them with
``crop:x,y,w,h`` src values that are cut out of the source picture here).

The output is byte-compatible with a v2 generation job — same checkpoint
files, same artifact names — so the studio preview, delivery center and the
post-generation revision chat all work on rebuilt decks unchanged.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from ppt_agent.models import StrictModel
from ppt_agent.v2 import prompts
from ppt_agent.v2.design import ThemeSpec, get_builtin_theme, normalize_theme
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DeckDesign,
    Frame,
    ImageItem,
    PageDesign,
    TextItem,
    normalize_page_payload,
)
from ppt_agent.v2.orchestrator import (
    _IMAGE_MEDIA_TYPES,
    _PAGE_DESIGN_FIELDS,
    PageOutcome,
    Progress,
    _Checkpoints,
)
from ppt_agent.v2.planning import (
    ContentBrief,
    DeckOutline,
    DeckSkeleton,
    PageBrief,
    PageSlot,
    SectionOutline,
)
from ppt_agent.v2.providers import (
    BudgetExceededError,
    LLMClient,
    encode_image_for_vision,
)
from ppt_agent.v2.qa import review_page, summarize
from ppt_agent.v2.render import render_deck


RebuildRoute = Literal[
    "rebuild",
    "design_from_content",
    "embed_with_notes",
    "style_reference",
    "extract_text",
]

ROUTE_LABELS: dict[str, str] = {
    "rebuild": "可编辑页面重建",
    "design_from_content": "根据内容设计一页",
    "embed_with_notes": "图片放入并补充解读",
    "style_reference": "参考视觉风格",
    "extract_text": "仅提取文字和数据",
}


class RebuildItem(StrictModel):
    image_path: str = Field(..., min_length=1)
    route: RebuildRoute
    note: str | None = Field(default=None, max_length=2000)


class RebuildResult(StrictModel):
    status: Literal["succeeded", "completed_with_qa_errors"]
    pptx_path: str
    deck_design_path: str
    qa_report_path: str
    page_count: int
    qa_error_pages: int
    rebuilt_pages: int
    fallback_pages: int
    usage: dict[str, Any]


def _encode_image(path: Path) -> tuple[str, str]:
    return encode_image_for_vision(path)


def _apply_crop_regions(
    page: PageDesign, source: Path, assets_dir: Path, *, page_number: int
) -> PageDesign:
    """Cut ``crop:x,y,w,h`` regions out of the source image into real assets."""

    from PIL import Image

    elements = list(page.elements)
    changed = False
    crop_index = 0
    with Image.open(source) as original:
        width, height = original.size
        for index, element in enumerate(elements):
            if not isinstance(element, ImageItem):
                continue
            src = (element.src or "").strip()
            if not src.startswith("crop:"):
                continue
            crop_index += 1
            try:
                x, y, w, h = (float(part) for part in src[5:].split(","))
            except ValueError:
                x, y, w, h = 0.0, 0.0, 1.0, 1.0
            left = min(max(x, 0.0), 0.98) * width
            top = min(max(y, 0.0), 0.98) * height
            right = min(max(x + max(w, 0.02), 0.02), 1.0) * width
            bottom = min(max(y + max(h, 0.02), 0.02), 1.0) * height
            assets_dir.mkdir(parents=True, exist_ok=True)
            crop_name = f"crop_p{page_number:03d}_{crop_index}.png"
            original.crop((int(left), int(top), int(right), int(bottom))).save(
                assets_dir / crop_name
            )
            elements[index] = element.model_copy(update={"src": crop_name})
            changed = True
    return page.model_copy(update={"elements": elements}) if changed else page


def _fallback_embed_page(item: RebuildItem, page_number: int, asset_name: str) -> PageDesign:
    """When reconstruction fails, ship the original image instead of a hole."""

    return PageDesign(
        page_number=page_number,
        role="content",
        title=Path(item.image_path).stem,
        background="background",
        show_chrome=False,
        elements=[
            TextItem(
                id="fallback_title",
                frame=Frame(x=64, y=40, w=1000, h=52),
                text=f"{Path(item.image_path).stem}（原图保留）",
                role="title",
            ),
            ImageItem(
                id="fallback_original",
                frame=Frame(x=64, y=120, w=CANVAS_WIDTH - 128, h=CANVAS_HEIGHT - 190),
                src=asset_name,
                label="原图",
            ),
            TextItem(
                id="fallback_note",
                frame=Frame(x=64, y=CANVAS_HEIGHT - 56, w=1000, h=30),
                text="这一页的自动重建未成功，原图已按原样放入，可手动替换。",
                role="caption",
            ),
        ],
        speaker_notes="自动重建失败，保留原图。",
    )


async def rebuild_deck_async(
    *,
    items: list[RebuildItem],
    output_dir: str | Path,
    client: LLMClient,
    deck_name: str = "generated_long_deck_v2",
    deck_title: str = "图片重建",
    language: str = "zh-CN",
    concurrency: int = 4,
    progress: Progress = print,
) -> RebuildResult:
    if not items:
        raise ValueError("At least one image is required.")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    assets_dir = output_root / "assets"
    checkpoints = _Checkpoints(output_root / "checkpoints", resume=False)

    # Stage every original image as an asset (embed route references them,
    # and failed rebuilds fall back to embedding).
    asset_names: dict[int, str] = {}
    for index, item in enumerate(items, start=1):
        source = Path(item.image_path)
        name = source.name
        if name in asset_names.values():
            name = f"{source.stem}_{index}{source.suffix}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / name).write_bytes(source.read_bytes())
        asset_names[index] = name

    # 1. Extract a shared theme from the images (first 3 keep the payload small).
    try:
        theme_payload = await client.complete_json(
            task="theme_from_images",
            system=prompts.THEME_FROM_IMAGES_SYSTEM,
            user=prompts.build_theme_from_images_user_prompt(
                names=[asset_names[index] for index in sorted(asset_names)][:3],
                language=language,
            ),
            context={"names": list(asset_names.values())[:3]},
            images=[
                _encode_image(Path(item.image_path)) for item in items[:3]
            ],
        )
        theme = normalize_theme(ThemeSpec.model_validate(theme_payload))
    except (ValidationError, ValueError, RuntimeError) as exc:
        progress(f"[rebuild] theme extraction fell back to builtin: {str(exc)[:120]}")
        theme = get_builtin_theme("slate")
    checkpoints.save("theme.json", theme.model_dump(mode="json"))
    progress(f"[theme] {theme.name} (motif: {theme.motif})")

    # 2. One slide per image, along its chosen route.
    semaphore = asyncio.Semaphore(concurrency)
    total = len(items)
    completed = 0
    lock = asyncio.Lock()

    async def build_page(index: int, item: RebuildItem) -> tuple[PageDesign, Any, PageOutcome]:
        nonlocal completed
        source = Path(item.image_path)
        status = "model"
        page: PageDesign | None = None
        attempts = 0
        async with semaphore:
            for attempt in range(2):
                attempts += 1
                try:
                    payload = await client.complete_json(
                        task="image_page",
                        system=prompts.build_image_page_system(language),
                        user=prompts.build_image_page_user_prompt(
                            route=item.route,
                            theme=theme,
                            name=asset_names[index],
                            page_number=index,
                            total_pages=total,
                            language=language,
                            user_note=item.note,
                        ),
                        context={
                            "route": item.route,
                            "name": asset_names[index],
                            "page_number": index,
                        },
                        images=[_encode_image(source)],
                    )
                    normalized = normalize_page_payload(dict(payload), page_number=index)
                    normalized = {
                        key: value
                        for key, value in normalized.items()
                        if key in _PAGE_DESIGN_FIELDS
                    }
                    normalized["page_number"] = index
                    normalized["role"] = "content"
                    normalized["show_chrome"] = False
                    normalized.setdefault("title", source.stem)
                    page = PageDesign.model_validate(normalized)
                    break
                except BudgetExceededError:
                    progress(f"[rebuild] budget reached; page {index} embeds the original")
                    break
                except (ValidationError, ValueError, RuntimeError) as exc:
                    progress(
                        f"[rebuild] page {index} attempt {attempt + 1} failed: {str(exc)[:140]}"
                    )
            if page is None:
                status = "fallback"
                page = _fallback_embed_page(item, index, asset_names[index])
            page = _apply_crop_regions(page, source, assets_dir, page_number=index)
            # QA is advisory here: a faithful reconstruction is never replaced,
            # deterministic fixes still apply and issues land in the report.
            page, qa_result = review_page(page, theme)
        outcome = PageOutcome(
            page_number=index,
            status=status,
            model_attempts=attempts,
            error_issues=len(qa_result.errors),
            warning_issues=len(qa_result.issues) - len(qa_result.errors),
        )
        checkpoints.save(
            f"pages/page_{index:03d}.json",
            {
                "page": page.model_dump(mode="json"),
                "qa": qa_result.model_dump(mode="json"),
                "outcome": outcome.model_dump(mode="json"),
            },
        )
        async with lock:
            completed += 1
            progress(f"[design] {completed}/{total} content pages done")
        return page, qa_result, outcome

    results = await asyncio.gather(
        *(build_page(index, item) for index, item in enumerate(items, start=1))
    )
    pages = [result[0] for result in results]
    qa_results = [result[1] for result in results]
    outcomes = [result[2] for result in results]

    # 3. Compatible brief + skeleton checkpoints (the revision chat needs them).
    brief = ContentBrief(
        topic=deck_title,
        deck_title=deck_title,
        language=language,
        purpose="以可编辑形式重建用户提供的图片",
        source_digest="\n".join(
            f"P{index:02d} [{ROUTE_LABELS.get(item.route, item.route)}] {asset_names[index]}"
            for index, item in enumerate(items, start=1)
        ),
    )
    checkpoints.save("brief.json", brief.model_dump(mode="json"))
    skeleton = DeckSkeleton(
        deck_title=deck_title,
        language=language,
        total_pages=total,
        outline=DeckOutline(
            deck_title=deck_title,
            sections=[
                SectionOutline(title="图片重建", goal="逐图重建", content_pages=total)
            ],
        ),
        slots=[
            PageSlot(
                page_number=index,
                kind="content",
                section_index=1,
                section_title="图片重建",
                brief=PageBrief(
                    title=page.title or asset_names[index],
                    summary=ROUTE_LABELS.get(item.route, item.route),
                    speaker_notes=page.speaker_notes or "",
                ),
            )
            for (index, item), page in zip(enumerate(items, start=1), pages)
        ],
    )
    skeleton_payload = skeleton.model_dump(mode="json")
    checkpoints.save("skeleton.json", skeleton_payload)
    checkpoints.save("skeleton_with_briefs.json", skeleton_payload)

    # 4. Assemble, report, render — same artifact names as a v2 generation job.
    deck = DeckDesign(
        deck_title=deck_title,
        language=language,
        theme=theme,
        pages=pages,
    )
    qa_summary = summarize(
        qa_results,
        repaired=[],
        fallback=[item.page_number for item in outcomes if item.status == "fallback"],
    )
    design_path = output_root / f"{deck_name}_design.json"
    design_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    qa_report_path = output_root / f"{deck_name}_qa_report.json"
    qa_report_path.write_text(qa_summary.model_dump_json(indent=2), encoding="utf-8")
    run_report = {
        "mode": "image_rebuild",
        "items": [
            {"image": asset_names[index], "route": item.route}
            for index, item in enumerate(items, start=1)
        ],
        "usage": client.usage.snapshot(),
        "outcomes": [item.model_dump(mode="json") for item in outcomes],
    }
    (output_root / f"{deck_name}_run_report.json").write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pptx_path = render_deck(deck, output_root / f"{deck_name}.pptx", assets_dir=assets_dir)
    progress(f"[render] editable deck written: {pptx_path}")

    qa_error_pages = sum(1 for result in qa_results if result.errors)
    return RebuildResult(
        status="completed_with_qa_errors" if qa_error_pages else "succeeded",
        pptx_path=str(pptx_path),
        deck_design_path=str(design_path),
        qa_report_path=str(qa_report_path),
        page_count=total,
        qa_error_pages=qa_error_pages,
        rebuilt_pages=sum(1 for item in outcomes if item.status == "model"),
        fallback_pages=sum(1 for item in outcomes if item.status == "fallback"),
        usage=client.usage.snapshot(),
    )


def rebuild_deck(
    *,
    items: list[RebuildItem],
    output_dir: str | Path,
    client: LLMClient,
    deck_name: str = "generated_long_deck_v2",
    deck_title: str = "图片重建",
    language: str = "zh-CN",
    concurrency: int = 4,
    progress: Progress = print,
) -> RebuildResult:
    """Synchronous wrapper for API callers."""

    return asyncio.run(
        rebuild_deck_async(
            items=items,
            output_dir=output_dir,
            client=client,
            deck_name=deck_name,
            deck_title=deck_title,
            language=language,
            concurrency=concurrency,
            progress=progress,
        )
    )
