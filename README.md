# ppt-agent

`ppt-agent` is an early milestone for an AI Presentation Agent. It defines a small, validated Slide IR, renders that IR into an editable PowerPoint file, runs deterministic rule-based QA checks, applies structured patch edits, provides an end-to-end demo pipeline, and can optionally generate Slide IR with LangChain structured output.

## Scope

Included in this milestone:

- Python 3.11+ project structure
- Pydantic v2 models for `Deck`, `Slide`, `BBox`, `TextStyle`, and element types
- A minimal `Theme` schema for consistent future rendering
- Editable Slide IR to `.pptx` rendering with `python-pptx`
- Rule-based QA for overlap, density, and text-fit checks
- Structured Patch Edit for targeted slide and element updates
- End-to-end demo pipeline for validation, QA, rendering, patching, re-QA, and re-rendering
- Optional LangChain structured-output deck generation into Slide IR
- Example JSON files
- pytest tests that load and validate the examples

Explicitly not included yet:

- LangGraph
- FastAPI or frontend code
- Databases, RAG, or image-to-PPT
- Natural-language editing
- HTML/SVG preview, Playwright, or LibreOffice

## Install

```bash
uv sync
```

## Run Tests

```bash
uv run pytest
```

## CLI Usage

The primary product entry point is the `ppt-agent` CLI.

Recommended one-step AI deck build:

```bash
uv run ppt-agent build \
  --topic "AI in Education" \
  --audience "university students" \
  --slides 8 \
  --theme examples/theme.json \
  --output-dir examples/output \
  --patch examples/sample_patch.json
```

The build command runs structured-output Deck IR generation, deterministic QA, and deterministic PPTX rendering. The LLM only generates Slide IR; `generated_deck.pptx` is still produced by the `python-pptx` renderer.
If `--patch` is provided, build also applies structured Patch Edit to the generated Deck IR and renders the patched deck.

Build outputs:

- `generated_deck_ir.json`
- `generated_qa_report.json`
- `generated_attempts.json`
- `generated_deck.pptx`
- `patched_deck_ir.json` when `--patch` is provided
- `patch_result.json` when `--patch` is provided
- `patched_deck.pptx` when `--patch` is provided

Generate Deck IR with optional LangChain structured output and QA-gated retry:

```bash
uv run ppt-agent generate \
  --topic "AI in Education" \
  --audience "university students" \
  --slides 8 \
  --theme examples/theme.json \
  --output examples/output/generated_deck.json \
  --min-qa-score 80 \
  --max-attempts 2 \
  --qa-output examples/output/generated_qa_report.json \
  --attempts-output examples/output/generated_attempts.json
```

This command requires `OPENAI_API_KEY`. It only writes Slide IR JSON and optional generation QA metadata; it does not generate PPTX directly.

Render Deck IR to editable PowerPoint:

```bash
uv run ppt-agent render examples/output/generated_deck.json \
  --theme examples/theme.json \
  --output examples/output/generated_deck.pptx
```

Run deterministic QA:

```bash
uv run ppt-agent qa examples/output/generated_deck.json \
  --theme examples/theme.json \
  --output examples/output/generated_qa_report.json
```

Apply a structured JSON patch:

```bash
uv run ppt-agent patch examples/sample_slide_ir.json \
  --patch examples/sample_patch.json \
  --output examples/output/patched_deck_ir.json \
  --result-output examples/output/patch_result.json
```

Patch Edit accepts structured JSON only. It is not natural-language editing.

## Run End-to-End Demo

The scripts in `scripts/` are demo/helper entry points. The CLI above is the main product interface.

```bash
uv run python scripts/run_demo_pipeline.py
```

This writes:

- `examples/output/qa_report.json`: original QA report
- `examples/output/sample_deck.pptx`: original editable PPTX
- `examples/output/patch_result.json`: structured patch result
- `examples/output/patched_qa_report.json`: patched QA report
- `examples/output/patched_sample_deck.pptx`: patched editable PPTX

## Rule-Based QA

```python
from ppt_agent.load import load_deck, load_theme
from ppt_agent.qa import analyze_deck

deck = load_deck("examples/sample_slide_ir.json")
theme = load_theme("examples/theme.json")
report = analyze_deck(deck, theme)
```

The QA layer is deterministic and does not call an LLM. It reports issues such as obvious bbox overlap, slides that are too dense or too sparse, and text that is likely too long for its bbox.

## Structured Patch Edit

```python
from ppt_agent.load import load_deck, load_patch
from ppt_agent.patch import apply_patch

deck = load_deck("examples/sample_slide_ir.json")
patch = load_patch("examples/sample_patch.json")
result = apply_patch(deck, patch)
```

Patch Edit accepts structured JSON operations such as `update_text`, `move_element`, `resize_element`, and `update_shape_style`. It does not parse natural language and does not call an LLM. Each successful operation returns a newly validated `Deck`; failed operations are reported as patch issues without mutating the original deck.

The same patch system is available through the CLI:

```bash
uv run ppt-agent patch examples/sample_slide_ir.json \
  --patch examples/sample_patch.json \
  --output examples/output/patched_deck_ir.json
```

You can also pass `--patch examples/sample_patch.json` to `ppt-agent build` to produce both the generated and patched deck artifacts in one run.

## LLM Deck Generation

LLM deck generation is optional and requires `OPENAI_API_KEY`. The `generate` command uses LangChain structured output to create Deck IR, runs deterministic QA, and can retry with QA feedback when the score is below the gate.

For most AI deck builds, use `ppt-agent build` as the one-step command. Use `ppt-agent generate` when you only want the Deck IR JSON.

```bash
uv run ppt-agent generate \
  --topic "AI readiness roadmap" \
  --audience "executive leadership team" \
  --slides 4 \
  --theme examples/theme.json \
  --output examples/output/generated_deck_ir.json \
  --qa-output examples/output/generated_qa_report.json \
  --attempts-output examples/output/generated_attempts.json
```

The LLM is only used to generate Slide IR that validates against the existing `Deck` Pydantic schema. It does not generate PPTX directly. PowerPoint files are still produced by the deterministic `python-pptx` renderer through the `render` command.

QA-gated generation options:

- `--min-qa-score`: required QA score before accepting the generated Deck IR, defaults to `80`
- `--max-attempts`: maximum structured-output attempts, defaults to `2`
- `--qa-output`: optional final QA report JSON
- `--attempts-output`: optional full generation attempts summary JSON

Optional environment variables:

- `OPENAI_MODEL`: model name, defaults to `gpt-5.5`
- `PPT_AGENT_TOPIC`: deck topic
- `PPT_AGENT_AUDIENCE`: target audience
- `PPT_AGENT_SLIDE_COUNT`: slide count from 1 to 10
- `PPT_AGENT_STYLE`: style label
- `PPT_AGENT_LANGUAGE`: output language

The `generate` command writes only the Deck IR JSON plus optional QA metadata. Use `ppt-agent qa` and `ppt-agent render` as separate deterministic steps to produce a QA report and editable PPTX.

## Examples

- `examples/sample_slide_ir.json` contains a three-slide sample deck.
- `examples/theme.json` contains the `clean_business` theme.
- `examples/sample_patch.json` contains a small structured patch.

All bounding boxes use PowerPoint-style inches:

```json
{
  "x": 0.7,
  "y": 0.6,
  "width": 6.2,
  "height": 1.0
}
```
