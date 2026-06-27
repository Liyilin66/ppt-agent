"""Command-line interface for ppt-agent."""

from __future__ import annotations

import argparse
import os
from typing import Sequence

from ppt_agent.export import write_model_json
from ppt_agent.generation import DeckGenerationRequest, generate_deck_with_model
from ppt_agent.load import load_deck, load_theme
from ppt_agent.qa import analyze_deck
from ppt_agent.renderer import render_deck_to_pptx


def _add_theme_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--theme", required=True, help="Path to a theme JSON file.")


def _cmd_generate(args: argparse.Namespace) -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Set it to run ppt-agent generate.")
        return 1

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("langchain-openai is not installed. Run: uv sync")
        return 1

    theme = load_theme(args.theme)
    request = DeckGenerationRequest(
        topic=args.topic,
        audience=args.audience,
        slide_count=args.slides,
        style=args.style or theme.name,
        language=args.language,
        key_points=args.key_point,
    )

    model = ChatOpenAI(model=args.model)
    deck = generate_deck_with_model(model, request)
    output_path = write_model_json(deck, args.output)
    print(output_path)
    return 0


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
    generate.add_argument("--language", default="en", help="Output language. Defaults to en.")
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
    generate.set_defaults(func=_cmd_generate)

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
