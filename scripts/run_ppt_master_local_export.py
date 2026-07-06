"""Run deterministic PPT Master local export for an existing visual project."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ppt_agent.job_store import JobStore
from ppt_agent.ppt_master_runner import (
    PPT_MASTER_RUNNER_RESULT_FILENAME,
    register_ppt_master_runner_result_artifact,
    run_ppt_master_local_export,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_ID = "02619bd8da5e49449f3b940a0f84771c"
DEFAULT_JOB_DIR = REPO_ROOT / "data" / "jobs" / DEFAULT_JOB_ID
DEFAULT_DB_PATH = REPO_ROOT / "data" / "jobs.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic ppt-master post-processing/export scripts for an existing "
            "job-local ppt-master visual project."
        )
    )
    parser.add_argument(
        "--job-id",
        default=DEFAULT_JOB_ID,
        help="Job id that owns the PPT Master package and output directory.",
    )
    parser.add_argument(
        "--job-dir",
        default=str(DEFAULT_JOB_DIR),
        help="Job directory containing ppt_master_package/ and ppt_master_output/.",
    )
    parser.add_argument(
        "--ppt-master-dir",
        default=None,
        help="Local ppt-master repository root. Falls back to PPT_MASTER_DIR when omitted.",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Optional explicit ppt-master project directory containing svg_output/ or svg_final/.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the ppt-agent jobs SQLite database, used for output artifact registration.",
    )
    parser.add_argument(
        "--no-register",
        action="store_true",
        help="Run export detection/export without registering output artifacts.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    job_dir = Path(args.job_dir)
    ppt_master_dir = Path(args.ppt_master_dir) if args.ppt_master_dir else None
    project_dir = Path(args.project_dir) if args.project_dir else None
    store = JobStore(args.db_path)

    try:
        result = run_ppt_master_local_export(
            args.job_id,
            job_dir,
            ppt_master_root=ppt_master_dir,
            project_dir=project_dir,
            register_output=not args.no_register,
            store=store,
        )
    except OSError as exc:
        print(f"Could not run PPT Master local export: {exc}", file=sys.stderr)
        return 2

    result_path = job_dir.expanduser().resolve(strict=False) / PPT_MASTER_RUNNER_RESULT_FILENAME
    print(f"ppt_master_runner_result: {result_path}")
    print(f"status: {result.status}")
    print(f"message: {result.message}")
    print(f"project_dir: {result.project_dir or 'none'}")
    print(f"output_dir: {result.output_dir}")
    print(f"pptx_path: {result.pptx_path or 'none'}")
    print(f"slide_count: {result.slide_count if result.slide_count is not None else 'unknown'}")
    print(f"registered: {str(result.registered).lower()}")

    if result.status == "requires_external_ai_generation":
        print(
            "next: Use run_prompt.md in the local AI IDE / ppt-master skill to generate "
            "the visual project first, then run this command again."
        )
    elif result.status == "ppt_master_unavailable":
        print("warning: Local ppt-master is unavailable. Clone it locally or set PPT_MASTER_DIR.", file=sys.stderr)
    elif result.status == "missing_package":
        print("warning: This job does not have a complete ppt_master_package/.", file=sys.stderr)
    elif result.status == "export_failed":
        print("error: Deterministic ppt-master export failed. See ppt_master_runner_result.json.", file=sys.stderr)

    if result.warnings:
        print("warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    if result.errors:
        print("errors:", file=sys.stderr)
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)

    _try_register_runner_result(args.job_id, job_dir, store)
    return 0 if result.status != "export_failed" else 2


def _try_register_runner_result(job_id: str, job_dir: Path, store: JobStore) -> None:
    job = store.get_job(job_id)
    if job is None or job.job_type != "long_deck":
        print("runner_result_artifact: skipped because the job is not a registered long_deck job.")
        return
    artifact = register_ppt_master_runner_result_artifact(store, job_id=job_id, job_dir=job_dir)
    print(f"runner_result_artifact: {artifact.artifact_id}")


if __name__ == "__main__":
    raise SystemExit(main())
