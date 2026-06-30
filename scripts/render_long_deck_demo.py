"""Render the 30-slide long-deck demo Deck IR to editable PPTX."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal, Sequence

from pydantic import Field

from ppt_agent.export import write_model_json
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import StrictModel
from ppt_agent.renderer import render_deck_to_pptx
from ppt_agent.runtime import sanitize_error_message, utc_now_iso


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = (
    REPO_ROOT / "examples" / "demo_long_deck_ai_agent_pm_30" / "output" / "generated_long_deck_ir.json"
)
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT / "examples" / "demo_long_deck_ai_agent_pm_30" / "output" / "generated_long_deck.pptx"
)
DEFAULT_REPORT_PATH = (
    REPO_ROOT / "examples" / "demo_long_deck_ai_agent_pm_30" / "output" / "long_deck_render_report.json"
)
DEFAULT_THEME_PATH = REPO_ROOT / "examples" / "theme.json"
DEFAULT_ASSETS_DIR = REPO_ROOT / "examples"


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


def render_long_deck_demo(
    input_deck_ir_path: str | Path = DEFAULT_INPUT_PATH,
    output_pptx_path: str | Path = DEFAULT_OUTPUT_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    theme_path: str | Path = DEFAULT_THEME_PATH,
    assets_dir: str | Path | None = DEFAULT_ASSETS_DIR,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the generated 30-slide long-deck IR demo to editable PPTX."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Path to generated_long_deck_ir.json.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for generated_long_deck.pptx.",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT_PATH),
        help="Path for long_deck_render_report.json.",
    )
    parser.add_argument(
        "--theme",
        default=str(DEFAULT_THEME_PATH),
        help="Path to theme JSON.",
    )
    parser.add_argument(
        "--assets-dir",
        default=str(DEFAULT_ASSETS_DIR),
        help="Optional assets directory for image elements.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = render_long_deck_demo(
        input_deck_ir_path=args.input,
        output_pptx_path=args.output,
        report_path=args.report,
        theme_path=args.theme,
        assets_dir=args.assets_dir,
    )

    if report.status == "succeeded":
        print(f"generated_long_deck_pptx: {report.output_pptx_path}")
        print(f"long_deck_render_report: {args.report}")
        print(f"slide_count: {report.slide_count}")
        return 0

    print(f"Could not render long deck demo: {report.error_message}", file=sys.stderr)
    print(f"long_deck_render_report: {args.report}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
