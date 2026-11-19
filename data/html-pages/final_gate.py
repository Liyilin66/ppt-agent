"""Per-slide acceptance of a finished deck, read from what PowerPoint drew (P4-Fix7).

Usage: uv run python data/html-pages/final_gate.py RUN_DIR PPTX SNAP_DIR OUT_JSON [RESOLUTIONS_JSON]

RESOLUTIONS_JSON records which version a person chose where the source states a figure two ways;
the chosen figure then passes and the rejected one is still a P0, wherever it appears in the deck.

- PowerPoint exports the deck to PDF (scripts/pptx_snapshot.sh); collisions and orphan characters
  are read from that PDF (scripts/pdf_collisions.py), not from the browser's measurements.
- Every native chart is read back and compared with its verified series (readback_charts.py).
- The run report gives each slide's stable id, content status and claim-audit verdict.

A slide is AUTO_PASS only when none of these finds anything; that is an automatic check, not a sign-off.
"""
import json
import subprocess
import sys
from pathlib import Path

run, pptx, snap, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
root = Path(__file__).resolve().parents[2]
pdf = snap / f"{pptx.stem}.pdf"
if not pdf.is_file():
    subprocess.run(["zsh", str(root / "scripts/pptx_snapshot.sh"), str(snap), str(pptx)], check=True,
                   stdout=subprocess.DEVNULL)
drawn = json.loads(subprocess.run(["uvx", "--from", "pymupdf", "python", str(root / "scripts/pdf_collisions.py"), str(pdf)],
                                  check=True, capture_output=True, text=True).stdout or "{}")
readback = subprocess.run(["uv", "run", "python", str(root / "data/html-pages/readback_charts.py"), str(run), str(pptx)],
                          check=True, capture_output=True, text=True).stdout.splitlines()
charts = {}
for line in readback:
    if line.startswith("("):
        number, series, verdict = eval(line)[:3]  # noqa: S307 - our own script's tuple output
        charts.setdefault(number, []).append(verdict)

# Every slide's own text and notes, against the source: a figure the source states two ways may also
# reach a page built outside the per-page content check (the executive summary carried CNNIC's
# "17179万人" after slide 3 had been corrected).
from pptx import Presentation
from ppt_agent.v2.evidence import EvidenceStore
from ppt_agent.v2.source_conflicts import conflicts as source_conflicts
store = EvidenceStore.from_dict(json.loads((run / "checkpoints/evidence_store.json").read_text()))
resolved = json.loads(Path(sys.argv[5]).read_text())["source_conflicts"] if len(sys.argv) > 5 else []
kept = {item["keep"] for item in resolved}
deck_conflicts = {}
for index, slide in enumerate(Presentation(pptx).slides, start=1):
    text = " ".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)
    if slide.has_notes_slide:
        text += " " + slide.notes_slide.notes_text_frame.text
    found = [f for f in source_conflicts({"text": text}, store) if f["figure"] not in kept]
    if found:
        deck_conflicts[index] = found

report = json.loads((run / "deck_run_report.json").read_text())
slides = (report.get("content_recovery") or {}).get("slides") or []
rows = []
for slide in slides:
    final = slide.get("final_page")
    if final is None:
        rows.append({**slide, "acceptance": "REMOVED"})
        continue
    problems = []
    for found in drawn.get(str(final), []):
        if "orphan" in found:
            problems.append(f"orphan「{found['orphan']}」after「{found['after']}」")
        elif "tight" in found:
            problems.append(f"tight ({found['gap_pt']}pt): {' / '.join(found['tight'])}")
        else:
            problems.append(f"collision: {' / '.join(found['lines'])}")
    problems += [f"chart: {verdict}" for verdict in charts.get(final, []) if verdict != "matches the verified series"]
    for found in deck_conflicts.get(final, []) if resolved or not slide.get("source_conflicts") else []:
        problems.append(f"P0 source conflict on the slide: {found['figure']} (p{found['page']}) vs {found['other']} (p{found['other_page']})")
    for found in [] if resolved else slide.get("source_conflicts") or []:  # resolved: judged on the deck's own text
        problems.append(f"P0 source conflict: {found['figure']} (p{found['page']}) vs {found['other']} (p{found['other_page']})")
    if slide.get("status") == "NEEDS_CONTENT_REVIEW":
        problems.append("content: needs review (claim audit or recovery)")
    rows.append({**slide, "problems": problems, "charts": len(charts.get(final, [])),
                 "acceptance": "AUTO_PASS" if not problems else "NEEDS_REVIEW"})
covered = {row.get("final_page") for row in rows}
for index, found in deck_conflicts.items():
    if index not in covered:
        rows.append({"final_page": index, "slide_id": f"slide{index:02d}", "acceptance": "NEEDS_REVIEW",
                     "problems": [f"P0 source conflict on the slide: {f['figure']} (p{f['page']}) vs {f['other']} (p{f['other_page']})"
                                  for f in found]})
summary = {"slides": sum(row["acceptance"] != "REMOVED" for row in rows if str(row.get("slide_id", "")).startswith("p")),
           "auto_pass": [row["final_page"] for row in rows if row["acceptance"] == "AUTO_PASS"],
           "needs_review": {row["final_page"]: row["problems"] for row in rows if row["acceptance"] == "NEEDS_REVIEW"},
           "removed": [row["slide_id"] for row in rows if row["acceptance"] == "REMOVED"],
           "pdf_findings": drawn, "charts_read_back": sum(len(v) for v in charts.values())}
out.write_text(json.dumps({"summary": summary, "slides": rows}, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
