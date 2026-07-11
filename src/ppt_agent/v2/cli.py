"""CLI commands for the v2 long-deck pipeline (registered under `ppt-agent v2`)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ppt_agent.v2.design import BUILTIN_THEMES
from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, BuildResult, build_deck
from ppt_agent.v2.planning import MAX_PAGES, MIN_PAGES
from ppt_agent.v2.providers import (
    ProviderConfig,
    ProviderError,
    build_client,
    provider_config_from_env,
)
from ppt_agent.v2.search import default_search_provider


def _add_build_arguments(parser: argparse.ArgumentParser, *, offline: bool) -> None:
    parser.add_argument("--prompt", required=True, help="What the deck should be about.")
    parser.add_argument(
        "--pages",
        type=int,
        default=100,
        help=f"Total slide count including cover/TOC/dividers/closing ({MIN_PAGES}-{MAX_PAGES}).",
    )
    parser.add_argument("--output-dir", required=True, help="Directory for PPTX and artifacts.")
    parser.add_argument("--deck-name", default="deck", help="Base name for output files.")
    parser.add_argument(
        "--theme",
        default="auto",
        help=f"'auto' (model-designed) or a builtin: {', '.join(sorted(BUILTIN_THEMES))}.",
    )
    parser.add_argument("--language", default=None, help="Force slide language (e.g. zh-CN, en).")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        dest="sources",
        help="Source document (pdf/docx/md/txt); repeatable.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse checkpoints in the output directory instead of regenerating.",
    )
    parser.add_argument(
        "--qa-gate",
        choices=("strict", "lenient"),
        default="strict",
        help=(
            "strict (default): pages still failing QA after repair are replaced "
            "by clean archetype pages. lenient: keep them and mark the run "
            "completed_with_qa_errors."
        ),
    )
    if not offline:
        parser.add_argument(
            "--provider",
            choices=("openai", "anthropic"),
            default=None,
            help="Model API protocol. Defaults to PPT_AGENT_PROVIDER or openai.",
        )
        parser.add_argument("--model", default=None, help="Model name at the provider.")
        parser.add_argument(
            "--base-url", default=None, help="Custom API base URL (proxies, compatible vendors)."
        )
        parser.add_argument(
            "--concurrency", type=int, default=8, help="Parallel page-design calls (1-32)."
        )
        parser.add_argument(
            "--budget-usd",
            type=float,
            default=15.0,
            help=(
                "Estimated cost guardrail. Pass --input-cost/--output-cost for "
                "billing-aligned metering; missing rates use non-zero estimates."
            ),
        )
        parser.add_argument(
            "--input-cost", type=float, default=None, help="USD per million input tokens."
        )
        parser.add_argument(
            "--output-cost", type=float, default=None, help="USD per million output tokens."
        )
        parser.add_argument(
            "--search",
            action="store_true",
            help="Enrich planning with web search (requires TAVILY_API_KEY).",
        )
        parser.add_argument(
            "--repair-rounds", type=int, default=1, help="LLM repair rounds per failing page (0-2)."
        )


def _build_request(args: argparse.Namespace, *, offline: bool) -> BuildRequest:
    return BuildRequest(
        prompt=args.prompt,
        page_count=args.pages,
        language=args.language,
        source_paths=list(args.sources),
        enable_search=bool(getattr(args, "search", False)),
        theme=args.theme,
        output_dir=args.output_dir,
        deck_name=args.deck_name,
        resume=args.resume,
        qa_gate=args.qa_gate,
        concurrency=getattr(args, "concurrency", 8),
        budget_usd=getattr(args, "budget_usd", None),
        repair_rounds=getattr(args, "repair_rounds", 1),
    )


def _print_result(result: BuildResult) -> None:
    print(f"\nstatus: {result.status}")
    print(
        f"pptx: {result.pptx_path}"
        if result.pptx_path
        else "pptx: not generated (quality gate failed)"
    )
    print(
        f"pages: {result.page_count} "
        f"(model {result.model_pages}, repaired {result.repaired_pages}, "
        f"fallback {result.fallback_pages})"
    )
    print(f"usage: {json.dumps(result.usage, ensure_ascii=False)}")
    print(f"stages: {json.dumps(result.stage_seconds)}")
    print(f"reports: {result.qa_report_path} | {result.run_report_path}")


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        env_config = provider_config_from_env()
        config = ProviderConfig(
            protocol=args.provider or env_config.protocol,
            model=args.model or env_config.model,
            base_url=args.base_url or env_config.base_url,
            input_cost_per_mtok_usd=args.input_cost,
            output_cost_per_mtok_usd=args.output_cost,
        )
        config.resolved_api_key()  # fail fast before any planning work
    except ProviderError as exc:
        print(f"provider configuration error: {exc}")
        return 1
    from ppt_agent.v2.providers import UsageMeter, ensure_pricing

    if args.budget_usd is not None:
        config, used_defaults = ensure_pricing(config)
        if used_defaults:
            print(
                f"budget guardrail: missing token rate(s); using an estimated "
                f"${config.input_cost_per_mtok_usd}/"
                f"${config.output_cost_per_mtok_usd} per MTok for '{config.model}'. "
                "Pass real rates for accurate metering."
            )
    client = build_client(config, usage=UsageMeter(budget_usd=args.budget_usd))
    search = default_search_provider() if args.search else None
    if args.search and search is None:
        print("--search requested but TAVILY_API_KEY is not set; continuing without search.")
    result = build_deck(_build_request(args, offline=False), client, search_provider=search)
    _print_result(result)
    return 2 if result.status == "quality_gate_failed" else 0


def _cmd_demo(args: argparse.Namespace) -> int:
    result = build_deck(_build_request(args, offline=True), MockLLMClient())
    _print_result(result)
    return 2 if result.status == "quality_gate_failed" else 0


def _cmd_preview(args: argparse.Namespace) -> int:
    from ppt_agent.v2.ir import DeckDesign
    from ppt_agent.v2.preview import deck_to_html

    design = DeckDesign.model_validate(
        json.loads(Path(args.design).read_text(encoding="utf-8"))
    )
    output = deck_to_html(design, args.output)
    print(f"preview: {output}")
    return 0


def register_v2_commands(subparsers) -> None:
    v2 = subparsers.add_parser(
        "v2", help="Long-deck free-layout pipeline (BYOK, up to 100 slides)."
    )
    v2_sub = v2.add_subparsers(dest="v2_command", required=True)

    build = v2_sub.add_parser("build", help="Generate a full deck with your model API key.")
    _add_build_arguments(build, offline=False)
    build.set_defaults(func=_cmd_build)

    demo = v2_sub.add_parser(
        "demo", help="Offline pipeline dry run with the deterministic mock designer."
    )
    _add_build_arguments(demo, offline=True)
    demo.set_defaults(func=_cmd_demo)

    preview = v2_sub.add_parser("preview", help="Render a deck design JSON to preview HTML.")
    preview.add_argument("--design", required=True, help="Path to *_design.json.")
    preview.add_argument("--output", required=True, help="Output HTML path.")
    preview.set_defaults(func=_cmd_preview)
