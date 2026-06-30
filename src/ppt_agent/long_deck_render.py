"""Offline rendering helpers for stitched long-deck Deck IR artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.export import write_model_json
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import StrictModel
from ppt_agent.renderer import render_deck_to_pptx
from ppt_agent.runtime import sanitize_error_message, utc_now_iso


class LongDeckRenderReport(StrictModel):
    status: Literal["succeeded", "failed"]
    input_deck_ir_path: Path
    output_pptx_path: Path
    slide_count: int | None = Field(default=None, ge=0)
    error_message: str | None = None
    generated_at: str
    warnings: list[str] = Field(default_factory=list)


def _write_render_report(report: LongDeckRenderReport, report_path: str | Path) -> LongDeckRenderReport:
    write_model_json(report, report_path)
    return report


def _failure_report(
    *,
    input_deck_ir_path: Path,
    output_pptx_path: Path,
    report_path: Path,
    error: object,
    slide_count: int | None = None,
) -> LongDeckRenderReport:
    report = LongDeckRenderReport(
        status="failed",
        input_deck_ir_path=input_deck_ir_path,
        output_pptx_path=output_pptx_path,
        slide_count=slide_count,
        error_message=sanitize_error_message(error),
        generated_at=utc_now_iso(),
    )
    return _write_render_report(report, report_path)


def render_long_deck_ir_to_pptx(
    input_deck_ir_path: str | Path,
    output_pptx_path: str | Path,
    report_path: str | Path,
    *,
    theme_path: str | Path,
    assets_dir: str | Path | None = None,
) -> LongDeckRenderReport:
    """Render an already-generated long Deck IR to PPTX without calling an LLM."""

    resolved_input = Path(input_deck_ir_path)
    resolved_output = Path(output_pptx_path)
    resolved_report = Path(report_path)
    resolved_theme = Path(theme_path)
    resolved_assets = Path(assets_dir) if assets_dir is not None else None

    if not resolved_input.exists():
        return _failure_report(
            input_deck_ir_path=resolved_input,
            output_pptx_path=resolved_output,
            report_path=resolved_report,
            error=f"Input Deck IR not found: {resolved_input}",
        )

    deck = None
    try:
        deck = load_deck(resolved_input)
        theme = load_theme(resolved_theme)
        render_deck_to_pptx(
            deck,
            theme,
            resolved_output,
            assets_dir=resolved_assets,
        )
    except Exception as exc:
        return _failure_report(
            input_deck_ir_path=resolved_input,
            output_pptx_path=resolved_output,
            report_path=resolved_report,
            error=exc,
            slide_count=len(deck.slides) if deck is not None else None,
        )

    report = LongDeckRenderReport(
        status="succeeded",
        input_deck_ir_path=resolved_input,
        output_pptx_path=resolved_output,
        slide_count=len(deck.slides),
        error_message=None,
        generated_at=utc_now_iso(),
    )
    return _write_render_report(report, resolved_report)
