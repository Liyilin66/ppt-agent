"""Prepare a job-local PPT Master execution plan without running ppt-master."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ppt_agent.job_store import JobStore
from ppt_agent.ppt_master_execution import prepare_ppt_master_execution
from ppt_agent.ppt_master_output import register_ppt_master_output_artifacts


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_ID = "02619bd8da5e49449f3b940a0f84771c"
DEFAULT_JOB_DIR = REPO_ROOT / "data" / "jobs" / DEFAULT_JOB_ID
DEFAULT_DB_PATH = REPO_ROOT / "data" / "jobs.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a PPT Master execution plan for an existing long-deck job package."
    )
    parser.add_argument(
        "--job-id",
        default=DEFAULT_JOB_ID,
        help="Job id that owns the PPT Master package.",
    )
    parser.add_argument(
        "--job-dir",
        default=str(DEFAULT_JOB_DIR),
        help="Job directory containing ppt_master_package/.",
    )
    parser.add_argument(
        "--ppt-master-dir",
        default=None,
        help="Local ppt-master repository root. Falls back to PPT_MASTER_DIR when omitted.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the ppt-agent jobs SQLite database, used only for optional output registration.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    job_dir = Path(args.job_dir)
    ppt_master_dir = Path(args.ppt_master_dir) if args.ppt_master_dir else None

    try:
        plan = prepare_ppt_master_execution(
            args.job_id,
            job_dir,
            ppt_master_root=ppt_master_dir,
        )
    except OSError as exc:
        print(f"Could not prepare PPT Master execution plan: {exc}", file=sys.stderr)
        return 2

    plan_path = job_dir.expanduser().resolve(strict=False) / "ppt_master_execution_plan.json"
    print(f"ppt_master_execution_plan: {plan_path}")
    print(f"status: {plan.status}")
    print(f"output_dir: {plan.output_dir}")
    print(f"expected_pptx_path: {plan.expected_pptx_path}")
    print("suggested_steps:")
    for step in plan.suggested_steps:
        print(f"- {step}")

    if plan.status == "output_detected":
        _try_register_output(args.job_id, plan.output_dir, Path(args.db_path))
    elif plan.status == "waiting_for_external_ppt_master_run":
        print("next: Run the local ppt-master workflow externally, then register the output.")
    elif plan.status == "ppt_master_unavailable":
        print("warning: Local ppt-master is unavailable. Clone it locally or set PPT_MASTER_DIR.", file=sys.stderr)
    elif plan.status == "missing_package":
        print("warning: This job does not have a complete ppt_master_package/.", file=sys.stderr)

    return 0


def _try_register_output(job_id: str, output_dir: Path, db_path: Path) -> None:
    store = JobStore(db_path)
    job = store.get_job(job_id)
    if job is None:
        print(f"output_detected: registration skipped because job was not found in {db_path}.")
        return
    if job.job_type != "long_deck":
        print(f"output_detected: registration skipped because job '{job_id}' is not a long_deck job.")
        return
    result = register_ppt_master_output_artifacts(store, job_id=job_id, output_dir=output_dir)
    print(
        "registered_output_artifacts: "
        f"pptx={result.pptx_artifact.artifact_id if result.pptx_artifact else 'none'}, "
        f"notes={result.notes_artifact.artifact_id if result.notes_artifact else 'none'}, "
        f"manifest={result.manifest_artifact.artifact_id}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
