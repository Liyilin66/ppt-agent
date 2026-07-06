# 30-page long deck IR dry run

This demo verifies the long-deck batch path for a 30-slide AI product manager deck, then renders the stitched Deck IR to editable PPTX offline.

It exercises:

- deterministic `LongDeckPlan`
- batch-level Deck IR generation
- per-batch validation and status files
- deterministic batch merge into one long Deck IR
- cross-batch long-deck QA
- offline PPTX rendering from the already-generated Deck IR

The batch run calls the model. The render step does not call the model and does not require `OPENAI_API_KEY`.

## Run Dry Run

```bash
export OPENAI_API_KEY="..."
uv run python scripts/run_long_deck_demo.py --batch-size 2
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

## Render PPTX

After the dry run succeeds, render the stitched Deck IR to editable PPTX:

```bash
uv run python scripts/render_long_deck_demo.py
```

The render script reads:

```text
examples/demo_long_deck_ai_agent_pm_30/output/generated_long_deck_ir.json
```

By default it writes:

```text
examples/demo_long_deck_ai_agent_pm_30/output/generated_long_deck.pptx
examples/demo_long_deck_ai_agent_pm_30/output/long_deck_render_report.json
```

This remains a local demo path, not a Web UI long-PPT entrypoint.

## Alternative rendering experiment: PPT Master Adapter

The legacy renderer is still available, but this spike can export the stitched Deck IR as a Markdown source document for a manual ppt-master quality experiment.

1. Generate the long deck IR with ppt-agent first.
2. Run:

```bash
uv run python scripts/export_to_ppt_master.py
```

3. The adapter writes:

```text
examples/demo_long_deck_ai_agent_pm_30/output/ppt_master_source.md
```

4. Use that Markdown file as the source document in ppt-master manually.
5. This project does not currently embed ppt-master, copy its source code, or guarantee the quality of ppt-master output.

## PPT Master Local Integration Spike

For a local workflow handoff, prepare a job package that can be used from a separate ppt-master checkout.

1. Install or clone ppt-master locally, for example:

```text
/Users/jay/Documents/ppt-master
```

2. Set the local ppt-master root:

```bash
export PPT_MASTER_DIR="/Users/jay/Documents/ppt-master"
```

3. Prepare the package:

```bash
uv run python scripts/prepare_ppt_master_package.py \
  --input examples/demo_long_deck_ai_agent_pm_30/output/generated_long_deck_ir.json \
  --output-dir examples/demo_long_deck_ai_agent_pm_30/output/ppt_master_package
```

4. Open the generated `run_prompt.md`.
5. In Claude Code, Codex, or CodeBuddy, open the ppt-master repo and run that prompt there.
6. ppt-agent does not automatically run ppt-master in this stage. This spike only generates a job package that can be handed to the local ppt-master workflow.

## Local PPT Master Setup Check

Check the local ppt-master checkout before preparing or running a package:

```bash
uv run python scripts/check_ppt_master_setup.py --ppt-master-dir /Users/jay/Documents/ppt-master
```

For machine-readable output:

```bash
uv run python scripts/check_ppt_master_setup.py --ppt-master-dir /Users/jay/Documents/ppt-master --json
```

If the directory does not exist, clone the official repository:

```bash
cd /Users/jay/Documents
git clone https://github.com/hugohe3/ppt-master.git
```

If the directory already exists but may be old, inspect and update it manually:

```bash
cd /Users/jay/Documents/ppt-master
git status
git pull
```

ppt-agent does not automatically update ppt-master. This avoids silently changing the user's local checkout.

## Web Handoff UX

The Web UI can show the PPT Master handoff package after a successful long deck run.

1. Set `PPT_MASTER_DIR`, or prepare packages with a local ppt-master root such as `/Users/jay/Documents/ppt-master`.
2. Run the 30-page long deck flow in the Web UI.
3. After success, open the `PPT Master 渲染包` section.
4. Download `run_prompt.md`, `source.md`, `manifest.json`, and `README.md`.
5. In `/Users/jay/Documents/ppt-master`, use Claude Code, Codex, or CodeBuddy to execute the generated `run_prompt.md`.
6. ppt-agent does not automatically run ppt-master in this stage.

## PPT Master Output Registration

When local ppt-master has already generated a PPTX, register that output back into ppt-agent so the Web UI can show it as a formal artifact.

1. Put the local ppt-master result under:

```text
data/jobs/<job_id>/ppt_master_output/
```

Typical files:

```text
data/jobs/<job_id>/ppt_master_output/generated_by_ppt_master.pptx
data/jobs/<job_id>/ppt_master_output/generation_notes.md
```

2. Register the result:

```bash
uv run python scripts/register_ppt_master_output.py \
  --job-id <job_id> \
  --output-dir data/jobs/<job_id>/ppt_master_output
```

3. Go back to the Web UI and open the `PPT Master 生成结果` section.
4. Download `generated_by_ppt_master.pptx`, the generation notes, or the output manifest.

## PPT Master Execution Bridge v0

Execution Bridge v0 prepares the next step inside ppt-agent without running ppt-master.
It checks the job package, checks the local ppt-master checkout, creates `ppt_master_output/`, and writes `ppt_master_execution_plan.json`.

1. In the Web UI, first generate or recover a `PPT Master 渲染包`.
2. Click `准备 PPT Master 执行计划`, or run:

```bash
uv run python scripts/prepare_ppt_master_execution.py \
  --job-id <job_id> \
  --job-dir data/jobs/<job_id> \
  --ppt-master-dir /Users/jay/Documents/ppt-master
```

3. Open the Web UI `PPT Master 执行桥` section and download `PPT Master Execution Plan`.
4. In `/Users/jay/Documents/ppt-master`, let Claude Code, Codex, or CodeBuddy read the plan and `run_prompt.md`.
5. The external ppt-master workflow should write:

```text
data/jobs/<job_id>/ppt_master_output/generated_by_ppt_master.pptx
data/jobs/<job_id>/ppt_master_output/generation_notes.md
```

6. Register the output:

```bash
uv run python scripts/register_ppt_master_output.py \
  --job-id <job_id> \
  --output-dir data/jobs/<job_id>/ppt_master_output
```

7. Return to the Web UI and download `generated_by_ppt_master.pptx` from `PPT Master 生成结果`.

ppt-agent still does not automatically run ppt-master, call a model, install ppt-master requirements, or open PowerPoint in this stage.

### PPT Master Recovery Package

If the hard quality gate fails, ppt-agent does not generate the old renderer PPTX.
When `generated_long_deck_ir.json` exists, it still creates a PPT Master recovery package:

- `ppt_master_source.md`
- `ppt_master_package/source.md`
- `ppt_master_package/run_prompt.md`
- `ppt_master_package/README.md`
- `ppt_master_package/manifest.json`

Download `run_prompt.md` and `source.md`, then hand them to the local ppt-master workflow.
This is meant to recover from old renderer quality failures through a sanitized source document, not to bypass the quality gate and ship a bad PPTX.

### Timeout / Resume

If a long deck job times out during batch generation, there is no complete merged Deck IR yet.
In that case, ppt-agent does not generate a PPT Master package, because there is no stitched `generated_long_deck_ir.json` to hand off.

- Use the Web UI `继续/重试长 PPT` action to resume from the last completed batch.
- Completed batch artifacts are reused; ppt-agent does not delete valid finished batch outputs.
- `LONG_DECK_JOB_TIMEOUT_SECONDS` can be set on the server to adjust the long deck timeout without affecting short PPT jobs.

Only after the merged Deck IR exists will ppt-agent generate either:

- a normal PPT Master package after successful quality gate evaluation, or
- a recovery PPT Master package after hard quality gate failure.

## Artifacts

Expected output shape:

```text
output/
  generated_long_deck_plan.json
  generated_long_deck_ir.json
  generated_long_deck_qa.json
  generated_long_deck.pptx
  ppt_master_source.md
  ppt_master_package/
    source.md
    run_prompt.md
    README.md
    manifest.json
  ppt_master_execution_plan.json
  ppt_master_output/
    generated_by_ppt_master.pptx
    generation_notes.md
    ppt_master_output_manifest.json
  long_deck_render_report.json
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

`generated_long_deck.pptx` is the editable PowerPoint rendered from the stitched Deck IR.

`ppt_master_source.md` is a source Markdown outline for a manual ppt-master adapter experiment. It is generated from the already-validated Deck IR and does not call the model or run ppt-master.

`ppt_master_package/` is the local integration handoff package. It contains the same source document, a runnable prompt for a local ppt-master checkout, a README, and a manifest with ppt-master availability warnings. If the hard quality gate failed after the stitched IR was created, the manifest uses `package_mode: recovery` and records the quality gate report path.

`ppt_master_execution_plan.json` is the Execution Bridge v0 plan. It records the local ppt-master root, package paths, expected output paths, current execution status, and suggested next steps. It does not mean ppt-master has been run.

`ppt_master_output/` is the optional local result directory after a separate ppt-master workflow has already generated a PPTX. `register_ppt_master_output.py` turns those files into formal ppt-agent artifacts so the Web UI can display and download them.

`long_deck_render_report.json` records render status, input/output paths, slide count, timestamp, warnings, and any render error.

`long_deck_run_report.json` is the run-level checkpoint summary.

If a batch fails, inspect `batches/batch_<id>_status.json` first, then `long_deck_run_report.json`. Completed earlier batch artifacts are intentionally kept so later resume support has a checkpoint surface.

If the failure mentions `524`, `origin_response_timeout`, `Proxy Read Timeout`, `timeout`, or `retryable`, use `batch_size=2`, wait for the provider retry window, and run again.
