"""Render the 30-slide long-deck demo Deck IR to editable PPTX."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ppt_agent.long_deck_render import LongDeckRenderReport, render_long_deck_ir_to_pptx


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


def render_long_deck_demo(
    input_deck_ir_path: str | Path = DEFAULT_INPUT_PATH,
    output_pptx_path: str | Path = DEFAULT_OUTPUT_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    theme_path: str | Path = DEFAULT_THEME_PATH,
    assets_dir: str | Path | None = DEFAULT_ASSETS_DIR,
) -> LongDeckRenderReport:
    """Render an already-generated long Deck IR to PPTX without calling an LLM."""

    return render_long_deck_ir_to_pptx(
        input_deck_ir_path,
        output_pptx_path,
        report_path,
        theme_path=theme_path,
        assets_dir=assets_dir,
    )


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
