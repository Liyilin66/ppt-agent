"""Key pages get a second, differently composed design; PowerPoint's rendering decides (P5 P1-B).

Stage C measured it: writing a page an art-direction brief did not reliably make it better, but designing it
in another form the content can take, and keeping whichever a blind reviewer prefers on PowerPoint's own
rendering, raised the mean score on 10 fixed pages from 4.25 to 4.58; the page in place still won 6 of 10.
So only key pages get a candidate (section openers, pages built around a chart, a source figure or a focal
figure: about a fifth of the deck), the page in place is kept unless the candidate is clearly and
consistently better, and judging is on PowerPoint's export (the browser draws native charts as sketches,
which biased the first comparison against chart pages).
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

SHARE = 0.2            # of the content pages
POWERPOINT = Path("/Applications/Microsoft PowerPoint.app")
VISUAL_FORMS = {"chart", "figure", "poster", "units", "strip"}
LOOKS = 3

Judge = Callable[[int, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any] | None]]


def key_pages(jobs: list[Any], plan: dict[int, Any], held: set[int], share: float = SHARE) -> list[int]:
    """The pages worth a second design: section openers, pages built around a visual or a focal figure."""

    content = [job for job in jobs if job.role == "content" and job.page_number not in held]
    if not content:
        return []
    seen_sections: set[str] = set()
    scored = []
    for job in content:
        score = 0
        if job.section and job.section not in seen_sections:
            score += 2  # the first page of a section sets its tone
            seen_sections.add(job.section)
        fingerprint = plan.get(job.page_number)
        if fingerprint is not None and fingerprint.form in VISUAL_FORMS:
            score += 2
        if (job.content.get("highlight") or {}).get("value"):
            score += 1
        if job.content.get("tier") == "visual":
            score += 1
        scored.append((score, job.page_number))
    count = max(1, round(len(content) * share))
    chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[:count]
    return sorted(number for score, number in chosen if score > 0)


def powerpoint_available() -> bool:
    return POWERPOINT.exists() and shutil.which("osascript") is not None


_EXPORT = asyncio.Lock()  # one presentation at a time: PowerPoint answers one script at a time


def _export(pptx: Path, out: Path) -> list[bytes]:
    """The slides as PowerPoint draws them: export to PDF through PowerPoint, rasterise each page."""

    import pypdfium2 as pdfium

    box = Path.home() / "Library/Containers/com.microsoft.Powerpoint/Data/Documents"
    box.mkdir(parents=True, exist_ok=True)
    pdf = box / f"{pptx.stem}.pdf"
    subprocess.run(["open", "-g", "-a", "Microsoft PowerPoint", str(pptx.resolve())], check=True)
    import time

    time.sleep(4)
    subprocess.run(["osascript", "-e", f'tell application "Microsoft PowerPoint" to save active presentation in '
                                       f'(POSIX file "{pdf}") as save as PDF',
                    "-e", 'tell application "Microsoft PowerPoint" to close active presentation saving no'],
                   check=True, capture_output=True, timeout=120)
    time.sleep(1)
    shutil.copy(pdf, out / pdf.name)
    images = []
    document = pdfium.PdfDocument(str(out / pdf.name))
    for page in document:
        picture = page.render(scale=110 / 72).to_pil().convert("RGB")
        picture.thumbnail((1280, 1280))
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        images.append(buffer.getvalue())
    return images


def powerpoint_judge(reviewer: Any, deck_meta: dict[str, Any], assets_dir: Path | None, record_dir: Path | None) -> Judge:
    """A judge comparing versions of one page on PowerPoint's rendering, blind, the page in place kept unless beaten."""

    from ppt_agent.v2 import visual_review
    from ppt_agent.v2.ir import DeckDesign
    from ppt_agent.v2.render import render_deck

    async def judge(number: int, versions: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any] | None:
        names = list(versions)
        with tempfile.TemporaryDirectory() as work:
            work_dir = Path(work)
            pages = [page.model_copy(update={"page_number": index}) for index, page in enumerate(versions.values(), 1)]
            deck = DeckDesign.model_validate({**deck_meta, "pages": [page.model_dump(mode="json") for page in pages]})
            pptx = render_deck(deck, work_dir / f"select_{number:03d}.pptx", assets_dir=assets_dir)
            try:
                async with _EXPORT:
                    images = await asyncio.to_thread(_export, pptx, work_dir)
            except Exception as error:  # noqa: BLE001 - no rendering, no judgement: the page in place stays
                return {"keep": names[0], "changed": False, "why": "PowerPoint 导出失败：" + str(error)[:80]}
            if record_dir is not None:
                record_dir.mkdir(parents=True, exist_ok=True)
                for name, image in zip(names, images):
                    (record_dir / f"p{number:03d}-{name}.png").write_bytes(image)
        verdict = await visual_review.review_page(
            reviewer, facts=facts, versions=[visual_review.Version(name, image) for name, image in zip(names, images)],
            reference_png=None, seed=number, looks=LOOKS)
        choice = visual_review.choose(verdict, incumbent=names[0])
        return {**choice, "scores": {name: (verdict["versions"].get(name) or {}).get("overall") for name in names}}

    return judge


def facts_of(content: dict[str, Any]) -> dict[str, Any]:
    return {key: content.get(key) for key in ("title", "highlight", "lead", "takeaway", "items") if content.get(key)}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
