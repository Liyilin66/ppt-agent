"""Register an existing local ppt-master output directory as job artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ppt_agent.job_store import JobStore
from ppt_agent.ppt_master_output import (
    PPT_MASTER_OUTPUT_MANIFEST_FILENAME,
    detect_ppt_master_output,
    register_ppt_master_output_artifacts,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_ID = "9c14de6f3ee14062ab955f7413f19fa7"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "jobs" / DEFAULT_JOB_ID / "ppt_master_output"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "jobs.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register locally generated ppt-master output files as ppt-agent job artifacts."
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="Job id that owns the PPT Master output.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing generated_by_ppt_master.pptx and generation_notes.md.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the ppt-agent jobs SQLite database.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    store = JobStore(args.db_path)

    job = store.get_job(args.job_id)
    if job is None:
        print(f"Job not found: {args.job_id}", file=sys.stderr)
        return 2
    if job.job_type != "long_deck":
        print(
            f"Job '{args.job_id}' is not a long_deck job and cannot own a PPT Master output registration.",
            file=sys.stderr,
        )
        return 2

    manifest = detect_ppt_master_output(output_dir)
    if manifest.job_id is not None and manifest.job_id != args.job_id:
        print(
            f"Output directory appears to belong to job '{manifest.job_id}', not '{args.job_id}': {output_dir}",
            file=sys.stderr,
        )
        return 2
    if not manifest.detected or manifest.pptx_path is None:
        first_warning = manifest.warnings[0] if manifest.warnings else (
            f"Missing {output_dir / 'generated_by_ppt_master.pptx'}"
        )
        print(
            f"Could not register PPT Master output: {first_warning}\n"
            "Make sure ppt-master has already generated generated_by_ppt_master.pptx in the output directory.",
            file=sys.stderr,
        )
        return 2

    try:
        result = register_ppt_master_output_artifacts(
            store,
            job_id=args.job_id,
            output_dir=output_dir,
        )
    except (OSError, ValueError) as exc:
        print(f"Could not register PPT Master output: {exc}", file=sys.stderr)
        return 2

    print(f"ppt_master_output_manifest: {result.manifest_path}")
    print(f"ppt_master_output_pptx: {result.manifest.pptx_path}")
    if result.manifest.notes_path is not None:
        print(f"ppt_master_output_notes: {result.manifest.notes_path}")
    print(
        f"registered_artifacts: "
        f"pptx={result.pptx_artifact.artifact_id if result.pptx_artifact else 'none'}, "
        f"notes={result.notes_artifact.artifact_id if result.notes_artifact else 'none'}, "
        f"manifest={result.manifest_artifact.artifact_id}"
    )
    print(f"manifest_filename: {PPT_MASTER_OUTPUT_MANIFEST_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
