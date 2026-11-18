"""Text collisions in a PDF exported by PowerPoint: glyphs of two different lines drawn over each other.

The browser measures pages before conversion; PowerPoint lays the text out again with its own font
metrics, so a page can pass in the browser and still collide on the slide (CNNIC 57 round 6:
"6.02亿人" into the label beside it). This reads what PowerPoint actually drew.

Usage: uvx --from pymupdf python scripts/pdf_collisions.py deck.pdf [out.json]
"""
import json
import sys

import pymupdf

MIN_SHARE = 0.2  # of the smaller glyph's area


def glyphs(page):
    found = []
    for b, block in enumerate(page.get_text("rawdict")["blocks"]):
        for l, line in enumerate(block.get("lines", [])):
            for span in line["spans"]:
                for char in span["chars"]:
                    if char["c"].strip():
                        x0, y0, x1, y1 = char["bbox"]
                        # glyph boxes carry the line's ascent and descent; keep the inked middle
                        h = y1 - y0
                        found.append({"c": char["c"], "line": (b, l), "size": span["size"],
                                      "box": (x0, y0 + 0.18 * h, x1, y1 - 0.18 * h), "text": ""})
    return found


def area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def collisions(page):
    chars = glyphs(page)
    lines = {}
    for char in chars:
        lines.setdefault(char["line"], []).append(char["c"])
    hits = {}
    for i, a in enumerate(chars):
        for b in chars[i + 1:]:
            if a["line"] == b["line"]:
                continue
            box = (max(a["box"][0], b["box"][0]), max(a["box"][1], b["box"][1]),
                   min(a["box"][2], b["box"][2]), min(a["box"][3], b["box"][3]))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            if area(box) >= MIN_SHARE * min(area(a["box"]), area(b["box"])):
                key = tuple(sorted([a["line"], b["line"]]))
                hits.setdefault(key, set()).update([a["c"], b["c"]])
    return [{"lines": ["".join(lines[k]) for k in key], "glyphs": "".join(sorted(chars_))}
            for key, chars_ in hits.items()]


def orphans(page):
    """A wrapped paragraph whose last line holds one CJK character, as PowerPoint broke it."""

    found = []
    rows = []  # (text, bbox, size) of every line, to find a wrapped line PowerPoint wrote as its own block
    for block in page.get_text("dict")["blocks"]:
        lines = [("".join(span["text"] for span in line["spans"]).strip(), line["bbox"],
                  max((span["size"] for span in line["spans"]), default=0)) for line in block.get("lines", [])]
        lines = [line for line in lines if line[0]]
        rows += lines
        if len(lines) >= 2 and len(lines[-1][0]) == 1 and "\u2e80" <= lines[-1][0] <= "\u9fff":
            found.append({"orphan": lines[-1][0], "after": lines[-2][0]})
    for text, box, size in rows:
        if len(text) != 1 or not "\u2e80" <= text <= "\u9fff":
            continue
        above = [other for other in rows if other[0] != text and abs(other[1][0] - box[0]) < 2
                 and abs(other[2] - size) < 0.5 and 0.5 * size < box[1] - other[1][1] < 2 * size and len(other[0]) >= 4]
        if above and not any(item["orphan"] == text and item["after"] == above[0][0] for item in found):
            found.append({"orphan": text, "after": above[0][0]})
    return found


def main():
    doc = pymupdf.open(sys.argv[1])
    report = {}
    for number, page in enumerate(doc, 1):
        found = collisions(page) + orphans(page)
        if found:
            report[number] = found
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if len(sys.argv) > 2:
        open(sys.argv[2], "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
