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

Generate Deck IR with optional LangChain structured output:

```bash
uv run ppt-agent generate \
  --topic "AI in Education" \
  --audience "university students" \
  --slides 8 \
  --theme examples/theme.json \
  --output examples/output/generated_deck.json
```

This command requires `OPENAI_API_KEY`. It only writes Slide IR JSON and does not generate PPTX directly.

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

## LLM Deck Generation

LLM deck generation is optional and requires `OPENAI_API_KEY`.

```bash
uv run ppt-agent generate \
  --topic "AI readiness roadmap" \
  --audience "executive leadership team" \
  --slides 4 \
  --theme examples/theme.json \
  --output examples/output/generated_deck_ir.json
```

The LLM is only used to generate Slide IR that validates against the existing `Deck` Pydantic schema. It does not generate PPTX directly. PowerPoint files are still produced by the deterministic `python-pptx` renderer.

Optional environment variables:

- `OPENAI_MODEL`: model name, defaults to `gpt-5.5`
- `PPT_AGENT_TOPIC`: deck topic
- `PPT_AGENT_AUDIENCE`: target audience
- `PPT_AGENT_SLIDE_COUNT`: slide count from 1 to 10
- `PPT_AGENT_STYLE`: style label
- `PPT_AGENT_LANGUAGE`: output language

The `generate` command writes only the Deck IR JSON. Use `ppt-agent qa` and `ppt-agent render` as separate deterministic steps to produce a QA report and editable PPTX.

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
