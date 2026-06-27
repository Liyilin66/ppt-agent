# ppt-agent

`ppt-agent` is an early milestone for an AI Presentation Agent. It defines a small, validated Slide IR, renders that IR into an editable PowerPoint file, runs deterministic rule-based QA checks, applies structured patch edits, and provides an end-to-end demo pipeline.

## Scope

Included in this milestone:

- Python 3.11+ project structure
- Pydantic v2 models for `Deck`, `Slide`, `BBox`, `TextStyle`, and element types
- A minimal `Theme` schema for consistent future rendering
- Editable Slide IR to `.pptx` rendering with `python-pptx`
- Rule-based QA for overlap, density, and text-fit checks
- Structured Patch Edit for targeted slide and element updates
- End-to-end demo pipeline for validation, QA, rendering, patching, re-QA, and re-rendering
- Example JSON files
- pytest tests that load and validate the examples

Explicitly not included yet:

- LLM calls, LangChain, or LangGraph
- FastAPI or frontend code
- Databases, RAG, or image-to-PPT
- Natural-language editing
- HTML/SVG preview, Playwright, or LibreOffice

## Install

```bash
python -m pip install -e ".[dev]"
```

With uv:

```bash
uv pip install -e ".[dev]"
```

## Run Tests

```bash
python -m pytest
```

## Render Sample Deck

```bash
python scripts/render_sample_deck.py
```

This writes an editable PowerPoint file to:

```text
examples/output/sample_deck.pptx
```

## Run End-to-End Demo

```bash
python scripts/run_demo_pipeline.py
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
