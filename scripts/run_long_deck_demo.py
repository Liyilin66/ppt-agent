"""Run the 30-slide long-deck demo dry run."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ppt_agent.long_deck_orchestrator import LongDeckRunRequest, run_long_deck_batch_generation


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = REPO_ROOT / "examples" / "demo_long_deck_ai_agent_pm_30" / "input.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "examples" / "demo_long_deck_ai_agent_pm_30" / "output"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")


def default_output_dir(input_path: Path = DEFAULT_INPUT_PATH) -> Path:
    return input_path.parent / "output"


def load_long_deck_run_request(
    input_path: str | Path = DEFAULT_INPUT_PATH,
    output_dir: str | Path | None = None,
) -> LongDeckRunRequest:
    resolved_input = Path(input_path)
    payload = json.loads(resolved_input.read_text(encoding="utf-8"))
    payload["output_dir"] = str(Path(output_dir) if output_dir is not None else default_output_dir(resolved_input))
    return LongDeckRunRequest.model_validate(payload)


def _make_chat_model(model_name: str):
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("langchain-openai is not installed. Run: uv sync", file=sys.stderr)
        return None
    return ChatOpenAI(model=model_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the 30-slide AI Agent PM long-deck IR dry run."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Path to long-deck demo input JSON.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to <input directory>/output.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.5.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.getenv("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. Set it to run the 30-page long deck demo dry run.",
            file=sys.stderr,
        )
        return 2

    try:
        request = load_long_deck_run_request(args.input, args.output_dir)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"Could not load long deck demo input: {exc}", file=sys.stderr)
        return 2

    model = _make_chat_model(args.model)
    if model is None:
        return 2

    report = run_long_deck_batch_generation(request, model)
    print(f"run_id: {report.run_id}")
    print(f"status: {report.status}")
    print(f"completed_batches: {', '.join(report.completed_batches) or 'none'}")
    print(f"failed_batches: {', '.join(report.failed_batches) or 'none'}")
    if report.merged_deck_ir_path is not None:
        print(f"generated_long_deck_ir: {report.merged_deck_ir_path}")
    if report.long_deck_qa_path is not None:
        print(f"generated_long_deck_qa: {report.long_deck_qa_path}")
    if report.run_report_path is not None:
        print(f"long_deck_run_report: {report.run_report_path}")
    if report.error_message:
        print(f"error: {report.error_message}", file=sys.stderr)

    return 0 if report.status == "succeeded" else 2


if __name__ == "__main__":
    raise SystemExit(main())
