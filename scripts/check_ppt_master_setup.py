"""Check local ppt-master repository setup without modifying it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ppt_agent.ppt_master_integration import check_ppt_master_setup


DEFAULT_PPT_MASTER_DIR = Path("/Users/jay/Documents/ppt-master")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect local ppt-master setup and print update suggestions."
    )
    parser.add_argument(
        "--ppt-master-dir",
        default=str(DEFAULT_PPT_MASTER_DIR),
        help="Local ppt-master repository root.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = check_ppt_master_setup(args.ppt_master_dir)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    _print_human_report(report.model_dump(mode="json"))
    return 0


def _print_human_report(report: dict) -> None:
    fields = [
        "root_path",
        "is_available",
        "missing_paths",
        "skill_path",
        "scripts_path",
        "has_requirements",
        "has_readme",
        "has_readme_cn",
        "is_git_repo",
        "git_remote_origin",
        "git_branch",
        "git_commit",
        "is_expected_repo",
    ]
    for field in fields:
        print(f"{field}: {report[field]}")

    print("warnings:")
    warnings = report["warnings"]
    if warnings:
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("- none")

    print("suggested_commands:")
    for command in report["suggested_commands"]:
        print(f"- {command}")


if __name__ == "__main__":
    raise SystemExit(main())
