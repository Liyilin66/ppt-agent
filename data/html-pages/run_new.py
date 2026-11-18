"""A small deck from scratch on another source (real model)."""
import sys, time
from pathlib import Path
from ppt_agent import deck_jobs
from ppt_agent.runtime import load_dotenv_file
from ppt_agent.v2.orchestrator import BuildRequest, build_deck
load_dotenv_file()
out, source, pages, prompt = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
req = BuildRequest(layout_engine="typeset", style_profile="consulting", html_theme="studio", html_review=False, prompt=prompt,
                   page_count=pages, source_paths=[str(Path(source).resolve())], enable_search=False, output_dir=str(out),
                   deck_name="deck", resume="--resume" in sys.argv, concurrency=4, budget_usd=8.0, qa_gate="strict",
                   claim_audit="--claim-audit" in sys.argv)
client = deck_jobs.create_v2_model_client()
t = time.time()
result = build_deck(req, client, progress=lambda m: print(f"{time.time()-t:6.0f}s {m}", flush=True))
print(result.status, result.pptx_path)
print(client.usage.snapshot())
