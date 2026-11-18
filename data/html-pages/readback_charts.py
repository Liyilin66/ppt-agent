"""Read a finished PPTX back: every native chart names its verified series; its categories and values must equal it."""
import glob, json, sys
from pptx import Presentation
from ppt_agent.v2.htmldeck.charts import series_key
run, pptx = sys.argv[1], sys.argv[2]
verified = {}
for f in glob.glob(f"{run}/checkpoints/series/*.json"):
    for s in json.load(open(f))["series"]:
        verified[series_key(s)] = s
rows = []
for number, slide in enumerate(Presentation(pptx).slides, 1):
    for shape in slide.shapes:
        if not shape.has_chart:
            continue
        key = shape.name.replace("chart ", "") if shape.name.startswith("chart series:") else None
        chart = shape.chart
        categories = [str(c) for c in chart.plots[0].categories]
        values = list(chart.plots[0].series[0].values)
        source = verified.get(key)
        if source is None:
            rows.append((number, key, "NO VERIFIED SERIES FOUND", categories)); continue
        want = {p["label"]: p["value"] for p in source["points"]}
        ok = sorted(categories) == sorted(want) and all(abs(want[c] - v) < 1e-9 for c, v in zip(categories, values))
        rows.append((number, key, "matches the verified series" if ok else "MISMATCH", source["metric"][:30]))
for row in rows: print(row)
print(json.dumps({"charts": len(rows), "matching": sum(r[2].startswith("matches") for r in rows)}))
