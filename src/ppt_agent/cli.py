"""Command-line interface for ppt-agent."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

from ppt_agent.export import write_model_json
from ppt_agent.generation import DeckGenerationRequest, generate_deck_with_quality_gate
from ppt_agent.load import load_deck, load_patch, load_theme
from ppt_agent.patch import apply_patch
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline
from ppt_agent.qa import analyze_deck
from ppt_agent.renderer import render_deck_to_pptx


def _add_theme_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--theme", required=True, help="Path to a theme JSON file.")


def _require_openai_api_key(command: str) -> bool:
    if not os.getenv("OPENAI_API_KEY"):
        print(f"OPENAI_API_KEY is not set. Set it to run ppt-agent {command}.")
        return False
    return True


def _make_chat_model(args: argparse.Namespace):
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("langchain-openai is not installed. Run: uv sync")
        return None

    return ChatOpenAI(model=args.model)


def _generation_request(args: argparse.Namespace, theme_name: str) -> DeckGenerationRequest:
    return DeckGenerationRequest(
        topic=args.topic,
        audience=args.audience,
        slide_count=args.slides,
        style=args.style or theme_name,
        language=args.language,
        key_points=args.key_point,
        user_requirements=args.user_requirements,
    )


def _run_generation(args: argparse.Namespace, theme):
    model = _make_chat_model(args)
    if model is None:
        return None

    return generate_deck_with_quality_gate(
        model,
        _generation_request(args, theme.name),
        theme=theme,
        min_score=args.min_qa_score,
        max_attempts=args.max_attempts,
    )


def _cmd_generate(args: argparse.Namespace) -> int:
    if not _require_openai_api_key("generate"):
        return 1

    theme = load_theme(args.theme)
    result = _run_generation(args, theme)
    if result is None:
        return 1

    output_path = write_model_json(result.deck, args.output)
    if args.qa_output:
        write_model_json(result.qa_report, args.qa_output)
    if args.attempts_output:
        write_model_json(result, args.attempts_output)

    print(output_path)
    if not result.accepted:
        print(
            f"Generated Deck IR did not meet the QA score gate: "
            f"{result.qa_report.score} < {args.min_qa_score}"
        )
        return 2

    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    if not _require_openai_api_key("build"):
        return 1

    model = _make_chat_model(args)
    if model is None:
        return 1

    request = BuildPipelineRequest(
        generation_request=DeckGenerationRequest(
            topic=args.topic,
            audience=args.audience,
            slide_count=args.slides,
            style=args.style,
            language=args.language,
            key_points=args.key_point,
            user_requirements=args.user_requirements,
        ),
        theme_path=Path(args.theme),
        output_dir=Path(args.output_dir),
        min_qa_score=args.min_qa_score,
        max_attempts=args.max_attempts,
        assets_dir=Path(args.assets_dir) if args.assets_dir else None,
        patch_path=Path(args.patch) if args.patch else None,
    )
    result = run_build_pipeline(model, request)

    for artifact in result.artifacts:
        print(artifact.path)
    for message in result.messages:
        print(message)

    return result.status_code


def _cmd_render(args: argparse.Namespace) -> int:
    deck = load_deck(args.deck)
    theme = load_theme(args.theme)
    output_path = render_deck_to_pptx(
        deck,
        theme,
        args.output,
        assets_dir=args.assets_dir,
    )
    print(output_path)
    return 0


def _cmd_qa(args: argparse.Namespace) -> int:
    deck = load_deck(args.deck)
    theme = load_theme(args.theme)
    report = analyze_deck(deck, theme)
    output_path = write_model_json(report, args.output)
    print(output_path)
    return 0


def _cmd_patch(args: argparse.Namespace) -> int:
    deck = load_deck(args.deck)
    patch = load_patch(args.patch)
    result = apply_patch(deck, patch)

    output_path = write_model_json(result.deck, args.output)
    print(output_path)
    if args.result_output:
        result_path = write_model_json(result, args.result_output)
        print(result_path)

    if result.issues:
        print(f"Patch completed with {len(result.issues)} issue(s).")
        return 2

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppt-agent",
        description="Validate, generate, QA, and render ppt-agent Slide IR.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate Deck IR JSON with structured output.")
    generate.add_argument("--topic", required=True, help="Presentation topic.")
    generate.add_argument("--audience", required=True, help="Target audience.")
    generate.add_argument("--slides", required=True, type=int, help="Slide count, from 1 to 10.")
    _add_theme_argument(generate)
    generate.add_argument("--output", required=True, help="Path for generated Deck IR JSON.")
    generate.add_argument("--style", default=None, help="Optional style label. Defaults to theme name.")
    generate.add_argument("--language", default="zh-CN", help="Output language. Defaults to zh-CN.")
    generate.add_argument(
        "--requirements",
        "--prompt",
        dest="user_requirements",
        default=None,
        help="Detailed user requirements for the deck.",
    )
    generate.add_argument(
        "--key-point",
        action="append",
        default=[],
        help="Optional key point. Can be passed multiple times.",
    )
    generate.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.5.",
    )
    generate.add_argument(
        "--min-qa-score",
        default=80,
        type=_qa_score,
        help="Minimum QA score for accepting generated Deck IR. Defaults to 80.",
    )
    generate.add_argument(
        "--max-attempts",
        default=2,
        type=int,
        help="Maximum generation attempts before returning the last Deck IR. Defaults to 2.",
    )
    generate.add_argument("--qa-output", default=None, help="Optional path for the final QA report JSON.")
    generate.add_argument(
        "--attempts-output",
        default=None,
        help="Optional path for the full generation attempts summary JSON.",
    )
    generate.set_defaults(func=_cmd_generate)

    build = subparsers.add_parser("build", help="Generate, QA, and render an editable PPTX.")
    build.add_argument("--topic", required=True, help="Presentation topic.")
    build.add_argument("--audience", required=True, help="Target audience.")
    build.add_argument("--slides", required=True, type=int, help="Slide count, from 1 to 10.")
    _add_theme_argument(build)
    build.add_argument("--output-dir", required=True, help="Directory for generated JSON and PPTX outputs.")
    build.add_argument("--style", default=None, help="Optional style label. Defaults to theme name.")
    build.add_argument("--language", default="zh-CN", help="Output language. Defaults to zh-CN.")
    build.add_argument(
        "--requirements",
        "--prompt",
        dest="user_requirements",
        default=None,
        help="Detailed user requirements for the deck.",
    )
    build.add_argument(
        "--key-point",
        action="append",
        default=[],
        help="Optional key point. Can be passed multiple times.",
    )
    build.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.5.",
    )
    build.add_argument(
        "--min-qa-score",
        default=80,
        type=_qa_score,
        help="Minimum QA score for accepting generated Deck IR. Defaults to 80.",
    )
    build.add_argument(
        "--max-attempts",
        default=2,
        type=int,
        help="Maximum generation attempts before returning the last Deck IR. Defaults to 2.",
    )
    build.add_argument("--assets-dir", default=None, help="Optional image asset directory.")
    build.add_argument("--patch", default=None, help="Optional structured patch JSON to apply after generation.")
    build.set_defaults(func=_cmd_build)

    render = subparsers.add_parser("render", help="Render Deck IR JSON to editable PPTX.")
    render.add_argument("deck", help="Path to Deck IR JSON.")
    _add_theme_argument(render)
    render.add_argument("--output", required=True, help="Path for output PPTX.")
    render.add_argument("--assets-dir", default=None, help="Optional image asset directory.")
    render.set_defaults(func=_cmd_render)

    qa = subparsers.add_parser("qa", help="Analyze Deck IR JSON and write a QA report.")
    qa.add_argument("deck", help="Path to Deck IR JSON.")
    _add_theme_argument(qa)
    qa.add_argument("--output", required=True, help="Path for output QA report JSON.")
    qa.set_defaults(func=_cmd_qa)

    patch = subparsers.add_parser("patch", help="Apply structured patch JSON to Deck IR.")
    patch.add_argument("deck", help="Path to input Deck IR JSON.")
    patch.add_argument("--patch", required=True, help="Path to structured patch JSON.")
    patch.add_argument("--output", required=True, help="Path for patched Deck IR JSON.")
    patch.add_argument("--result-output", default=None, help="Optional path for full patch result JSON.")
    patch.set_defaults(func=_cmd_patch)

    return parser


def _qa_score(value: str) -> int:
    score = int(value)
    if not 0 <= score <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return score


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
