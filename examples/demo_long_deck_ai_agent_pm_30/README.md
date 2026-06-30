# 30-page long deck IR dry run

This demo verifies the long-deck batch path for a 30-slide AI product manager deck. It is a dry run for Deck IR artifacts only.

It exercises:

- deterministic `LongDeckPlan`
- batch-level Deck IR generation
- per-batch validation and status files
- deterministic batch merge into one long Deck IR
- cross-batch long-deck QA

It does not render PPTX in this stage.

## Run

```bash
export OPENAI_API_KEY="..."
uv run python scripts/run_long_deck_demo.py
```

The demo defaults to `batch_size=2`, so a 30-slide run produces 15 mini-batches. This keeps each model request small enough to avoid common proxy or Cloudflare 120-second read timeout limits. `batch_size=3` or `batch_size=5` may be faster, but they are more likely to timeout behind a proxy. If you use the official API or a more stable backend, you can try a larger batch size.

Override the input file value from the CLI:

```bash
uv run python scripts/run_long_deck_demo.py --batch-size 2
uv run python scripts/run_long_deck_demo.py --batch-size 3
uv run python scripts/run_long_deck_demo.py --batch-size 5
```

The script reads:

```text
examples/demo_long_deck_ai_agent_pm_30/input.json
```

By default it writes:

```text
examples/demo_long_deck_ai_agent_pm_30/output/
```

## Artifacts

Expected output shape:

```text
output/
  generated_long_deck_plan.json
  generated_long_deck_ir.json
  generated_long_deck_qa.json
  long_deck_run_report.json
  batches/
    batch_01_deck_ir.json
    batch_01_qa_report.json
    batch_01_attempts.json
    batch_01_status.json
    batch_02_deck_ir.json
    batch_02_qa_report.json
    batch_02_attempts.json
    batch_02_status.json
    batch_03_deck_ir.json
    batch_03_qa_report.json
    batch_03_attempts.json
    batch_03_status.json
    ...
    batch_15_deck_ir.json
    batch_15_qa_report.json
    batch_15_attempts.json
    batch_15_status.json
```

`generated_long_deck_plan.json` is the deterministic section and batch plan.

`batches/batch_<id>_deck_ir.json` is one generated batch Deck IR.

`batches/batch_<id>_qa_report.json` is the normal per-batch QA report.

`batches/batch_<id>_attempts.json` records generation attempts for resume/debugging.

`batches/batch_<id>_status.json` records whether the batch succeeded or failed.

`generated_long_deck_ir.json` is the stitched 30-slide Deck IR.

`generated_long_deck_qa.json` is the deterministic cross-batch QA report.

Long-deck QA is a diagnostic quality radar. Coverage warnings help identify sections that may read thin, but they are not schema validation failures and do not block artifact generation.

`long_deck_run_report.json` is the run-level checkpoint summary.

If a batch fails, inspect `batches/batch_<id>_status.json` first, then `long_deck_run_report.json`. Completed earlier batch artifacts are intentionally kept so later resume support has a checkpoint surface.

If the failure mentions `524`, `origin_response_timeout`, `Proxy Read Timeout`, `timeout`, or `retryable`, use `batch_size=2`, wait for the provider retry window, and run again.
