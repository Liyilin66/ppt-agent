"""Figures of the source document, reproduced as they were published.

A report's charts hold data its text never spells out. Rather than redraw them
from guesses, the page shows the original: the embedded picture, its own
caption and credit line, and the page it stands on. A figure goes only to a
page that cites the page it comes from and is about the same thing.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path
from typing import Any

MIN_WIDTH, MIN_HEIGHT = 300, 150   # smaller pictures are logos and ornaments
REPEATS = 3                        # a picture on this many pages is a running header
MAX_EDGE = 1600                    # larger than a slide can show
MIN_SHARED = 3                     # two-character runs a caption and a page must share
_CAPTION = re.compile(r"^\s*(图\s*\d+)\s+(\S[^\n]{1,48})$", re.M)
_CREDIT = re.compile(r"^\s*(?:资料)?来源[:：]\s*(\S[^\n]{0,60})$", re.M)


def _grams(text: str) -> set[str]:
    return {run[i:i + 2] for run in re.findall(r"[㐀-鿿A-Za-z0-9]+", text or "") for i in range(len(run) - 1)}


def extract(source_paths: list[str | Path], assets_dir: Path) -> list[dict[str, Any]]:
    """Every captioned picture of the PDFs, saved under ``assets_dir`` as PNG.

    A page is used only when its captions and its pictures pair off one to one;
    where they do not, which picture a caption belongs to would be a guess."""

    from PIL import Image
    from pypdf import PdfReader

    found: list[dict[str, Any]] = []
    for doc_index, path in enumerate(source_paths, 1):
        path = Path(path)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        try:
            reader = PdfReader(str(path))
            pages = [(number, page, list(page.images)) for number, page in enumerate(reader.pages, 1)]
        except Exception:  # noqa: BLE001 - a PDF whose pictures cannot be read has no figures
            continue
        seen = Counter((image.name, len(image.data)) for _, _, images in pages for image in images)
        for number, page, images in pages:
            text = page.extract_text() or ""
            captions = _CAPTION.findall(text)
            pictures = []
            for image in images:
                if seen[(image.name, len(image.data))] >= REPEATS:
                    continue
                try:
                    picture = image.image
                    picture.load()
                except Exception:  # noqa: BLE001
                    continue
                if picture.width >= MIN_WIDTH and picture.height >= MIN_HEIGHT:
                    pictures.append(picture)
            if not captions or len(captions) != len(pictures):
                continue
            credits = _CREDIT.findall(text)
            for index, ((label, title), picture) in enumerate(zip(captions, pictures)):
                # Transparent areas were white paper in the report.
                flat = Image.new("RGB", picture.size, "white")
                rgba = picture.convert("RGBA")
                flat.paste(rgba, mask=rgba.split()[3])
                if max(flat.size) > MAX_EDGE:
                    flat.thumbnail((MAX_EDGE, MAX_EDGE))
                name = f"fig_d{doc_index}_p{number}_{index + 1}.png"
                assets_dir.mkdir(parents=True, exist_ok=True)
                buffer = io.BytesIO()
                flat.save(buffer, format="PNG")
                (assets_dir / name).write_bytes(buffer.getvalue())
                found.append({
                    "id": name, "label": re.sub(r"\s+", " ", label), "title": title.strip(),
                    "credit": credits[index].strip() if len(credits) == len(captions) else None,
                    "document": path.name, "page": number, "ref": f"{path.name} 第{number}页",
                    "width": flat.width, "height": flat.height,
                })
    return found


def caption(figure: dict[str, Any]) -> str:
    """The line under a reproduced figure: its own caption, credit and page."""

    parts = [f"{figure['label']} {figure['title']}"]
    if figure.get("credit"):
        parts.append("来源：" + figure["credit"])
    parts.append(f"原文第 {figure['page']} 页")
    return " · ".join(parts)


def _cited(source: str | None, document: str) -> set[int]:
    pages: set[int] = set()
    for clause in re.split(r"[;；]", source or ""):
        if document not in clause:
            continue
        for first, last in re.findall(r"第\s*(\d+)(?:\s*[-–—]\s*(\d+))?\s*页", clause):
            pages.update(range(int(first), int(last or first) + 1))
    return pages


def assign(figures: list[dict[str, Any]], contents: dict[int, dict[str, Any]],
           taken: set[int] = frozenset()) -> dict[int, dict[str, Any]]:
    """At most one figure a page, each figure on one page: the page that cites
    the figure's own page and shares the most of its caption's words.
    ``taken`` are pages that already draw something (a chart)."""

    scored = []
    for figure in figures:
        words = _grams(re.sub(r"[（(]单位[^）)]*[）)]", "", figure["title"]))
        for page, content in contents.items():
            if page in taken or figure["page"] not in _cited(content.get("source"), figure["document"]):
                continue
            said = " ".join(str(part) for part in (
                content.get("title"), (content.get("highlight") or {}).get("text"), content.get("lead"),
                *(f"{item.get('heading') or ''} {item.get('body') or ''}" for item in content.get("items") or [])))
            shared = len(words & _grams(said))
            if shared >= MIN_SHARED:
                scored.append((shared, -page, figure))
    placed: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    for shared, negative_page, figure in sorted(scored, key=lambda entry: (-entry[0], -entry[1])):
        page = -negative_page
        if page in placed or figure["id"] in used:
            continue
        placed[page] = {"id": figure["id"], "caption": caption(figure), "ref": figure["ref"],
                        "ratio": round(figure["width"] / figure["height"], 3)}
        used.add(figure["id"])
    return placed
