"""Compare replays of one frozen snapshot: decisions, slide map and a normalised PPTX structure (P4-Fix8 P0-1).

Usage: uv run python data/html-pages/replay_compare.py RUN_A RUN_B [RUN_C ...]
The PPTX files differ in packaging timestamps; what is compared is every slide's shapes (kind, name,
position, size, text) and chart data, hashed per slide.
"""
import hashlib, json, sys
from pathlib import Path
from pptx import Presentation


def structure(pptx):
    slides = []
    for slide in Presentation(pptx).slides:
        shapes = []
        for shape in slide.shapes:
            item = [shape.shape_type and int(shape.shape_type), shape.name,
                    *(round((value or 0) / 12700, 1) for value in (shape.left, shape.top, shape.width, shape.height))]
            if shape.has_text_frame:
                item.append(shape.text_frame.text)
            if getattr(shape, "has_chart", False) and shape.has_chart:
                plot = shape.chart.plots[0]
                item.append([list(plot.categories), [list(series.values) for series in plot.series]])
            shapes.append(item)
        slides.append(hashlib.sha1(json.dumps(shapes, ensure_ascii=False, default=str).encode()).hexdigest()[:12])
    return slides


def state(run):
    run = Path(run)
    report = json.loads((run / "deck_run_report.json").read_text())
    recovery = report.get("content_recovery") or {}
    slides = [{key: slide.get(key) for key in ("slide_id", "final_page", "status", "claim_audit", "content_version")}
              for slide in recovery.get("slides") or []]
    overlap = json.loads((run / "checkpoints/typeset/content_overlap.json").read_text())
    audit = report.get("claim_audit") or {}
    return {"slides": slides, "removed": overlap.get("removed"), "retargeted": overlap.get("retargeted"),
            "overlap_repeated": overlap.get("repeated"),
            "audit": {key: audit.get(key) for key in ("rewritten", "still_failing", "items_moved", "fix")},
            "pptx": structure(run / "deck.pptx")}


runs = sys.argv[1:]
states = [state(run) for run in runs]
same = True
for key in states[0]:
    values = [json.dumps(s[key], ensure_ascii=False, sort_keys=True) for s in states]
    if len(set(values)) > 1:
        same = False
        print(f"DIFFERENT: {key}")
        if key == "pptx":
            for index, hashes in enumerate(zip(*[s["pptx"] for s in states]), 1):
                if len(set(hashes)) > 1:
                    print(f"   slide {index}: {hashes}")
            print("   slide counts:", [len(s["pptx"]) for s in states])
    else:
        print(f"same: {key}")
print("IDENTICAL" if same else "NOT IDENTICAL", "across", len(runs), "runs;", len(states[0]["pptx"]), "slides")
