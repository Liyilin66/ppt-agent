"""Claim audit on a finished run's pages, nothing rewritten: what the audit would act on."""
import asyncio, glob, json, sys
from ppt_agent import deck_jobs
from ppt_agent.runtime import load_dotenv_file
from ppt_agent.v2 import claim_audit
load_dotenv_file()
run = sys.argv[1]
removed = set(json.load(open(f"{run}/deck_run_report.json")).get("content_recovery", {}).get("by_status", {}).get("PAGE_REMOVED") or [])
client = deck_jobs.create_v2_review_client()
class Store:
    def __init__(self): self.d = {}
    def load(self, n): return self.d.get(n)
    def save(self, n, v): self.d[n] = v
async def main():
    pages = {}
    for f in sorted(glob.glob(f"{run}/checkpoints/typeset/content_0*.json")):
        n = int(f[-8:-5]); d = json.load(open(f))
        if n in removed: continue
        pages[n] = (d["content"], (d["record"].get("evidence") or {}).get("text", ""))
    found = await asyncio.gather(*(claim_audit.audit_page(client, Store(), n, c, e) for n, (c, e) in pages.items()))
    out = {}
    for (n, (c, _)), v in zip(pages.items(), found):
        dec = claim_audit.decide(v, c)
        out[n] = {"verdict": v, "decision": dec}
        if dec["rewrite"] or dec["move_items"]:
            print(n, c["title"], "| REWRITE", [list(x.values())[0].get("text") for x in dec["rewrite"]], "| MOVE", [c["items"][i].get("heading") for i in dec["move_items"]])
    print("minor only:", sorted(n for n, o in out.items() if not o["decision"]["rewrite"] and not o["decision"]["move_items"] and o["decision"]["minor"]))
    json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
    print(client.usage.snapshot())
asyncio.run(main())
