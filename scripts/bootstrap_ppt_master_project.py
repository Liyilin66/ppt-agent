"""Bootstrap a job-local PPT Master visual project scaffold."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ppt_agent.job_store import JobStore
from ppt_agent.ppt_master_project import (
    PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME,
    bootstrap_ppt_master_visual_project,
    register_ppt_master_visual_project_artifacts,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_ID = "02619bd8da5e49449f3b940a0f84771c"
DEFAULT_JOB_DIR = REPO_ROOT / "data" / "jobs" / DEFAULT_JOB_ID
DEFAULT_DB_PATH = REPO_ROOT / "data" / "jobs.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a PPT Master visual project scaffold for an existing long-deck job package."
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
        "--project-name",
        default=None,
        help="Optional project directory name under ppt_master_output/.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing scaffold directory with the same project name.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the ppt-agent jobs SQLite database, used for artifact registration.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    job_dir = Path(args.job_dir)
    ppt_master_dir = Path(args.ppt_master_dir) if args.ppt_master_dir else None

    try:
        project = bootstrap_ppt_master_visual_project(
            args.job_id,
            job_dir,
            ppt_master_root=ppt_master_dir,
            project_name=args.project_name,
            overwrite=args.overwrite,
        )
    except OSError as exc:
        print(f"Could not bootstrap PPT Master visual project: {exc}", file=sys.stderr)
        return 2

    print(f"ppt_master_visual_project_manifest: {project.job_manifest_path}")
    print(f"status: {project.status}")
    print(f"project_dir: {project.project_dir}")
    print(f"project_instructions_path: {project.project_instructions_path}")
    print(f"expected_svg_output_dir: {project.expected_svg_output_dir}")
    print(f"expected_svg_final_dir: {project.expected_svg_final_dir}")
    print(f"expected_pptx_path: {project.expected_pptx_path}")
    print("next_steps:")
    for step in project.next_steps:
        print(f"- {step}")

    if project.warnings:
        print("warnings:")
        for warning in project.warnings:
            print(f"- {warning}")

    _try_register_project(args.job_id, job_dir, Path(args.db_path))
    if project.status in {"failed", "missing_package", "ppt_master_unavailable"}:
        return 2
    return 0


def _try_register_project(job_id: str, job_dir: Path, db_path: Path) -> None:
    store = JobStore(db_path)
    job = store.get_job(job_id)
    if job is None:
        print(f"visual_project_artifacts: skipped because job was not found in {db_path}.")
        return
    if job.job_type != "long_deck":
        print(f"visual_project_artifacts: skipped because job '{job_id}' is not a long_deck job.")
        return
    result = register_ppt_master_visual_project_artifacts(store, job_id=job_id, job_dir=job_dir)
    print(
        "visual_project_artifacts: "
        f"manifest={result.manifest_artifact.artifact_id}, "
        f"instructions={result.instructions_artifact.artifact_id if result.instructions_artifact else 'none'}, "
        f"manifest_filename={PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
