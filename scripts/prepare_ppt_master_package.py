"""Prepare a local ppt-master handoff package from Deck IR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ppt_agent.ppt_master_integration import (
    create_ppt_master_job_package,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = (
    REPO_ROOT
    / "examples"
    / "demo_long_deck_ai_agent_pm_30"
    / "output"
    / "generated_long_deck_ir.json"
)
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "examples"
    / "demo_long_deck_ai_agent_pm_30"
    / "output"
    / "ppt_master_package"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a local ppt-master job package from Deck IR."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Path to generated Deck IR JSON.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where the ppt-master package will be written.",
    )
    parser.add_argument(
        "--ppt-master-dir",
        default=None,
        help="Optional local ppt-master repository root. Defaults to PPT_MASTER_DIR.",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Optional topic override for source.md and run_prompt.md.",
    )
    parser.add_argument(
        "--audience",
        default=None,
        help="Optional audience override for source.md and run_prompt.md.",
    )
    parser.add_argument(
        "--style-notes",
        action="append",
        default=None,
        help="Additional style note to include in source.md. Repeat for multiple notes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)

    if not input_path.is_file():
        print(
            f"Input Deck IR not found: {input_path}\n"
            "Run the long-deck generation first, or pass --input to an existing Deck IR JSON file.",
            file=sys.stderr,
        )
        return 2

    try:
        deck_payload = json.loads(input_path.read_text(encoding="utf-8"))
        package = create_ppt_master_job_package(
            deck_payload,
            args.output_dir,
            ppt_master_root=args.ppt_master_dir,
            style_notes=args.style_notes,
            topic=args.topic,
            audience=args.audience,
        )
    except json.JSONDecodeError as exc:
        print(f"Could not parse Deck IR JSON: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Could not prepare ppt-master package: {exc}", file=sys.stderr)
        return 2

    for warning in package.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    print(f"ppt_master_package: {package.output_dir}")
    print(f"source: {package.source_path}")
    print(f"run_prompt: {package.run_prompt_path}")
    print(f"manifest: {package.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
