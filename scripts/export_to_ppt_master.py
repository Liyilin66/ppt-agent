"""Export the demo long-deck Deck IR to a ppt-master source Markdown file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ppt_agent.ppt_master_adapter import export_deck_ir_to_ppt_master_markdown


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = (
    REPO_ROOT
    / "examples"
    / "demo_long_deck_ai_agent_pm_30"
    / "output"
    / "generated_long_deck_ir.json"
)
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT
    / "examples"
    / "demo_long_deck_ai_agent_pm_30"
    / "output"
    / "ppt_master_source.md"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Deck IR as source Markdown for a manual ppt-master experiment."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Path to generated Deck IR JSON.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for the ppt-master source Markdown output.",
    )
    parser.add_argument(
        "--style-notes",
        action="append",
        default=None,
        help="Additional style note to include. Repeat the flag for multiple notes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.is_file():
        print(
            f"Input Deck IR not found: {input_path}\n"
            "Run the long-deck generation first, or pass --input to an existing Deck IR JSON file.",
            file=sys.stderr,
        )
        return 2

    try:
        deck_payload = json.loads(input_path.read_text(encoding="utf-8"))
        exported_path = export_deck_ir_to_ppt_master_markdown(
            deck_payload,
            output_path,
            style_notes=args.style_notes,
        )
    except json.JSONDecodeError as exc:
        print(f"Could not parse Deck IR JSON: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Could not export ppt-master source Markdown: {exc}", file=sys.stderr)
        return 2

    print(f"ppt_master_source: {exported_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
