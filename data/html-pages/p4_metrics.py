"""P4 design metrics: four stages kept apart, and every repair and retry counted."""
import json, sys
from pathlib import Path
SOFT = {"sparse", "small_text", "tight", "focal_conflict", "long_display", "hole", "red_overuse", "small_note"}
run = Path(sys.argv[1]); cp = run / "checkpoints"
report = json.loads((run / "deck_run_report.json").read_text())
html = report.get("html_design") or {}
removed = set((report.get("content_recovery") or {}).get("by_status", {}).get("PAGE_REMOVED") or [])
content = sorted(n for n in (int(p.stem[-3:]) for p in (cp / "typeset").glob("content_[0-9]*.json")) if n not in removed)
templated = set(html.get("templated_pages") or []) | set(html.get("fell_back_pages") or [])
stage = {"model_first": [], "geometric_repair": [], "model_retry": [], "fallback": [], "unknown": []}
repairs = retries = 0
rows = []
for n in content:
    f = cp / f"html/page_{n:03d}.json"
    trace = json.loads(f.read_text()).get("trace") if f.is_file() else None
    if n in templated:
        stage["fallback"].append(n); rows.append((n, "fallback (plain template)")); continue
    if not trace:
        stage["unknown"].append(n); rows.append((n, "no trace")); continue
    hard = [k for k in trace[0]["kinds"] if k not in SOFT]
    repairs += sum(bool(t.get("repaired")) for t in trace)
    retries += len(trace) - 1
    if not hard:
        stage["model_first"].append(n); rows.append((n, "model first", len(trace)))
    elif trace[0].get("repaired"):
        stage["geometric_repair"].append(n); rows.append((n, "geometric repair of first output", hard))
    else:
        stage["model_retry"].append(n); rows.append((n, f"model retry ({len(trace) - 1})", hard))
total = len(content)
cumulative, out = 0, {"content_pages": total}
for name in ("model_first", "geometric_repair", "model_retry", "fallback"):
    cumulative += len(stage[name]) if name != "fallback" else 0
    out[name] = {"pages": stage[name], "count": len(stage[name]), "share": round(len(stage[name]) / max(1, total), 2)}
out["passed_by_stage"] = {"after_model_first": round(len(stage["model_first"]) / max(1, total), 2),
                          "after_geometric_repair": round((len(stage["model_first"]) + len(stage["geometric_repair"])) / max(1, total), 2),
                          "after_model_retry": round(cumulative / max(1, total), 2)}
out["geometric_repairs"], out["model_retries"], out["design_model_calls"] = repairs, retries, html.get("model_calls")
out["unknown"] = stage["unknown"]
out["removed_pages"] = sorted(removed)
print(json.dumps(out, ensure_ascii=False))
for row in rows: print(row)
