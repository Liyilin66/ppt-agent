"""Find a report's figures where they are drawn, not only where a picture is embedded (P5 P1-A).

``figures.extract`` takes embedded pictures captioned "图 N". The IEA Global EV Outlook draws its charts as
vector paths, numbers none of them, and credits each with "IEA. CC BY 4.0." and "Sources:"; that
extractor found none of its charts, so a 173-page report full of charts gave a deck without one.

A figure is found as a dense cluster of drawn paths or pictures on a page. Its extent is set by the
text around it: from the end of the body text above to the credit line below ("Sources:", "Notes:",
"IEA. CC BY", "资料来源", "来源："). The first line inside is its title. The region is rendered as the reader
sees it (axes, legend and labels included) and handed to ``figures.assess``, which decides whether it is
redrawn as a native chart (two independent readings agree and every value is printed), shown as the
original, or only cited.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

CREDIT = re.compile(r"^\s*(?:IEA\.?\s*CC\s*BY|Sources?\s*:|Notes?\s*:|资料来源|数据来源|来源[:：]|注[:：])", re.I)
CAPTION = re.compile(r"^\s*((?:Figure|Fig\.|Chart|Exhibit|图|图表)\s*[\d.]+[a-z]?)\s*[:：.]?\s*(.*)$", re.I)
MIN_OBJECTS = 8          # a few rules and boxes are ornament; a chart is many marks
MIN_WIDTH, MIN_HEIGHT = 150, 70   # points
GAP = 14                 # points between marks of one figure
RENDER_DPI = 200
MAX_EDGE = 1800


def _prose(text: str) -> bool:
    """Body text, not a chart label: a long line of words."""

    return len(re.findall(r"[A-Za-z]{2,}", text)) >= 9 or len(re.findall(r"[一-鿿]", text)) >= 28


def _lines(textpage: Any) -> list[tuple[float, float, float, float, str]]:
    """Text lines: the text's rectangles are pieces of lines, joined when they share a baseline and are close."""

    pieces = []
    for index in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(index)
        text = textpage.get_text_bounded(left=left, bottom=bottom, right=right, top=top).strip()
        if text:
            pieces.append([left, bottom, right, top, " ".join(text.split())])
    lines: list[list[Any]] = []
    for piece in sorted(pieces, key=lambda p: (p[0], -p[1])):
        middle = (piece[1] + piece[3]) / 2
        line = next((line for line in lines if line[1] - 1 <= middle <= line[3] + 1 and -2 <= piece[0] - line[2] <= 40), None)
        if line is None:
            lines.append(piece)
        else:
            joiner = "" if piece[0] - line[2] < 2.5 else " "  # pieces of one word are drawn touching
            line[2], line[3] = piece[2], max(line[3], piece[3])
            line[4] += joiner + piece[4]
    # The text of the joined line as the PDF spaces it (pieces of one word are drawn apart: "e lectric").
    return [(line[0], line[1], line[2], line[3],
             " ".join(textpage.get_text_bounded(left=line[0], bottom=line[1], right=line[2], top=line[3]).split()) or line[4])
            for line in lines]


def _rule(obj: Any, raw: Any, width: float, height: float) -> bool:
    """A table's line: a thin rule, or a rectangle that is only outlined (a cell), never a filled bar or a curve."""

    import ctypes

    if obj.type != raw.FPDF_PAGEOBJ_PATH:
        return False
    if min(width, height) <= 2.5:
        return True
    fill, stroke = ctypes.c_int(0), ctypes.c_int(0)
    raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
    return fill.value == 0 and bool(stroke.value) and raw.FPDFPath_CountSegments(obj.raw) <= 5


def _blank(obj: Any, raw: Any) -> bool:
    import ctypes

    if obj.type != raw.FPDF_PAGEOBJ_PATH:
        return False
    fill, stroke = ctypes.c_int(0), ctypes.c_int(0)
    raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
    if not fill.value or stroke.value:
        return False
    r, g, b, a = (ctypes.c_uint(0) for _ in range(4))
    raw.FPDFPageObj_GetFillColor(obj.raw, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
    return min(r.value, g.value, b.value) >= 245


def _clusters(page: Any, width: float, height: float) -> list[list[float]]:
    import pypdfium2.raw as raw

    boxes = []
    for obj in page.get_objects(max_depth=4):
        if obj.type not in (raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_IMAGE):
            continue
        left, bottom, right, top = obj.get_pos()
        if (right - left) * (top - bottom) > width * height * 0.5 or (right - left) > width * 0.98:
            continue  # the page's own background and full-width rules
        if _blank(obj, raw):
            continue  # a white fill behind a table cell or a label is paper, not a mark
        boxes.append([left, bottom, right, top, 1, int(_rule(obj, raw, right - left, top - bottom))])
    clusters: list[list[float]] = []
    for box in sorted(boxes, key=lambda b: -b[3]):
        for cluster in clusters:
            if box[0] < cluster[2] + GAP and box[2] > cluster[0] - GAP and box[1] < cluster[3] + GAP and box[3] > cluster[1] - GAP:
                cluster[0], cluster[1] = min(cluster[0], box[0]), min(cluster[1], box[1])
                cluster[2], cluster[3] = max(cluster[2], box[2]), max(cluster[3], box[3])
                cluster[4] += 1
                cluster[5] += box[5]
                break
        else:
            clusters.append(box)
    # A cluster nearly as tall as the page is a shaded text box (a sidebar), not a figure.
    # Nearly all thin rules is a table's grid: tables are read as data by ``v2.tables``, not pictured.
    return [c for c in clusters if c[4] >= MIN_OBJECTS and c[2] - c[0] >= MIN_WIDTH and c[3] - c[1] >= MIN_HEIGHT
            and c[3] - c[1] < height * 0.7 and c[5] < 0.9 * c[4]]


def _credit(text: str) -> str:
    """Who the figure is from, not its notes: the "Sources:" sentence (or the licence line), short."""

    match = re.search(r"Sources?\s*:\s*(.+?)(?:\.\s|$)", text, re.I) or re.search(r"(?:资料|数据)?来源[:：]\s*(\S[^。]*)", text)
    licence = re.search(r"IEA\.?\s*CC\s*BY\s*[\d.]+", text)
    parts = [part for part in ((licence.group(0).rstrip(".") if licence else ""), (match.group(1).strip() if match else "")) if part]
    return "；".join(parts)[:80]


def regions(page: Any) -> list[dict[str, Any]]:
    """Each figure on one page: its extent (PDF points, origin bottom-left), title, credit and its text."""

    width, height = page.get_size()
    lines = _lines(page.get_textpage())
    found: list[dict[str, Any]] = []
    for cluster in _clusters(page, width, height):
        below = [line for line in lines if line[3] <= cluster[1] + 4 and CREDIT.match(line[4])]
        bottom = max((line[3] for line in below), default=cluster[1] - 4)
        bottom = min(bottom, cluster[1]) if below else bottom
        # The title sits just above the marks: climb line by line while they stay close (a title wraps to
        # two or three lines); body text further up is not part of the figure.
        top = cluster[3]
        for _ in range(3):
            near = [line for line in lines if line[1] >= top - 4 and line[1] - top <= 24 and line[0] < cluster[2]
                    and not (line[1] - top > 6 and _prose(line[4]) and len(line[4]) > 140)]
            if not near:
                break
            top = max(line[3] for line in near)
        inside = sorted((line for line in lines if line[1] >= bottom - 1 and line[3] <= top + 1
                         and not CREDIT.match(line[4])), key=lambda line: (-line[3], line[0]))
        titled = [line for line in inside if re.search(r"[A-Za-z]{3}|[一-鿿]{4}", line[4])]
        if not titled:
            continue
        title = titled[0][4]
        caption = CAPTION.match(title)
        credit = _credit(" ".join(line[4] for line in sorted(below, key=lambda line: -line[3])[:4]))
        box = [min(cluster[0], min((line[0] for line in inside), default=cluster[0])) - 4, bottom,
               max(cluster[2], max((line[2] for line in inside), default=cluster[2])) + 4, top]
        if any(box[1] < other["box"][3] and box[3] > other["box"][1] and box[0] < other["box"][2] and box[2] > other["box"][0]
               for other in found):
            continue  # a second cluster of a figure already found (panels of one chart)
        found.append({"box": box, "title": (caption.group(2) if caption else title)[:120],
                      "label": caption.group(1) if caption else "", "credit": credit[:200],
                      "text": "\n".join(line[4] for line in inside), "marks": int(cluster[4])})
    return found


def discover(path: str | Path, doc_index: int, assets_dir: Path) -> list[dict[str, Any]]:
    """Every drawn or embedded figure of a PDF, rendered under ``assets_dir`` as PNG, in the shape
    ``figures.extract`` gives (so ``figures.assess`` and ``figures.assign`` take it as they are)."""

    import pypdfium2 as pdfium

    path = Path(path)
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception:  # noqa: BLE001 - a PDF that cannot be opened has no figures to find
        return []
    texts: dict[int, str] = {}
    figures: list[dict[str, Any]] = []
    for number in range(1, len(document) + 1):
        page = document[number - 1]
        texts[number] = page.get_textpage().get_text_range()
        for index, region in enumerate(regions(page), 1):
            left, bottom, right, top = region["box"]
            scale = RENDER_DPI / 72
            width, height = page.get_size()
            picture = page.render(scale=scale).to_pil().convert("RGB")
            crop = picture.crop((max(0, left * scale), max(0, (height - top) * scale),
                                 min(picture.width, right * scale), min(picture.height, (height - bottom) * scale)))
            if max(crop.size) > MAX_EDGE:
                crop.thumbnail((MAX_EDGE, MAX_EDGE))
            name = f"fig_d{doc_index}_p{number}_r{index}.png"
            assets_dir.mkdir(parents=True, exist_ok=True)
            crop.save(assets_dir / name, format="PNG")
            figures.append({"id": name, "label": region["label"], "title": region["title"], "credit": region["credit"] or None,
                            "document": path.name, "page": number, "ref": f"{path.name} 第{number}页",
                            "width": crop.width, "height": crop.height, "origin": "drawn", "marks": region["marks"],
                            "figure_text": region["text"]})
    for figure in figures:
        figure["context"] = "\n".join(texts.get(near, "") for near in (figure["page"] - 1, figure["page"], figure["page"] + 1))
    return figures
