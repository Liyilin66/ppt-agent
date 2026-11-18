"""Per-slide acceptance of a finished deck, read from what PowerPoint drew (P4-Fix7).

Usage: uv run python data/html-pages/final_gate.py RUN_DIR PPTX SNAP_DIR OUT_JSON

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
    if slide.get("status") == "NEEDS_CONTENT_REVIEW":
        problems.append("content: needs review (claim audit or recovery)")
    rows.append({**slide, "problems": problems, "charts": len(charts.get(final, [])),
                 "acceptance": "AUTO_PASS" if not problems else "NEEDS_REVIEW"})
summary = {"slides": sum(row["acceptance"] != "REMOVED" for row in rows),
           "auto_pass": [row["final_page"] for row in rows if row["acceptance"] == "AUTO_PASS"],
           "needs_review": {row["final_page"]: row["problems"] for row in rows if row["acceptance"] == "NEEDS_REVIEW"},
           "removed": [row["slide_id"] for row in rows if row["acceptance"] == "REMOVED"],
           "pdf_findings": drawn, "charts_read_back": sum(len(v) for v in charts.values())}
out.write_text(json.dumps({"summary": summary, "slides": rows}, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
