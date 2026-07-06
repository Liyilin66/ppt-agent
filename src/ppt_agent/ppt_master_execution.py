"""Execution bridge planning for local ppt-master workflows.

This module creates a job-local execution plan for an external ppt-master run.
It never runs ppt-master, installs dependencies, calls a model, opens
PowerPoint, or changes Deck IR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.models import StrictModel
from ppt_agent.ppt_master_integration import PptMasterInstallation, detect_ppt_master_installation
from ppt_agent.ppt_master_output import (
    PPT_MASTER_OUTPUT_NOTES_FILENAME,
    PPT_MASTER_OUTPUT_PPTX_FILENAME,
)
from ppt_agent.ppt_master_project import find_bootstrapped_visual_project


PPT_MASTER_EXECUTION_PLAN_ARTIFACT = "ppt_master_execution_plan"
PPT_MASTER_EXECUTION_PLAN_FILENAME = "ppt_master_execution_plan.json"
PptMasterExecutionStatus = Literal[
    "missing_package",
    "ppt_master_unavailable",
    "waiting_for_external_ppt_master_run",
    "output_detected",
]


class PptMasterExecutionPlan(StrictModel):
    job_id: str
    job_dir: Path
    ppt_master_root: Path | None = None
    package_dir: Path
    source_path: Path
    run_prompt_path: Path
    output_dir: Path
    project_dir: Path | None = None
    expected_pptx_path: Path
    expected_notes_path: Path
    skill_path: Path | None = None
    status: PptMasterExecutionStatus
    warnings: list[str] = Field(default_factory=list)
    suggested_steps: list[str] = Field(default_factory=list)
    created_at: str


def prepare_ppt_master_execution(
    job_id: str,
    job_dir: Path,
    ppt_master_root: Path | None = None,
) -> PptMasterExecutionPlan:
    """Prepare a local execution plan for a job's ppt-master package.

    The plan is intentionally a bridge artifact: it records what an external AI
    IDE or operator should do in the local ppt-master repo, but does not execute
    that workflow.
    """

    resolved_job_dir = Path(job_dir).expanduser().resolve(strict=False)
    package_dir = resolved_job_dir / "ppt_master_package"
    source_path = package_dir / "source.md"
    run_prompt_path = package_dir / "run_prompt.md"
    output_dir = resolved_job_dir / "ppt_master_output"
    expected_pptx_path = output_dir / PPT_MASTER_OUTPUT_PPTX_FILENAME
    expected_notes_path = output_dir / PPT_MASTER_OUTPUT_NOTES_FILENAME
    project_dir = find_bootstrapped_visual_project(resolved_job_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    installation = detect_ppt_master_installation(ppt_master_root)
    warnings = _package_warnings(source_path, run_prompt_path)
    if not installation.is_available:
        warnings.extend(installation.missing_paths)

    if warnings and (not source_path.is_file() or not run_prompt_path.is_file()):
        status: PptMasterExecutionStatus = "missing_package"
    elif expected_pptx_path.is_file():
        status = "output_detected"
    elif not installation.is_available:
        status = "ppt_master_unavailable"
    else:
        status = "waiting_for_external_ppt_master_run"

    plan = PptMasterExecutionPlan(
        job_id=job_id,
        job_dir=resolved_job_dir,
        ppt_master_root=installation.root_path,
        package_dir=package_dir,
        source_path=source_path,
        run_prompt_path=run_prompt_path,
        output_dir=output_dir,
        project_dir=project_dir,
        expected_pptx_path=expected_pptx_path,
        expected_notes_path=expected_notes_path,
        skill_path=installation.skill_path,
        status=status,
        warnings=warnings,
        suggested_steps=_suggested_steps(
            job_id=job_id,
            installation=installation,
            run_prompt_path=run_prompt_path,
            source_path=source_path,
            output_dir=output_dir,
            project_dir=project_dir,
        ),
        created_at=datetime.now(UTC).isoformat(),
    )
    plan_path = resolved_job_dir / PPT_MASTER_EXECUTION_PLAN_FILENAME
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return plan


def _package_warnings(source_path: Path, run_prompt_path: Path) -> list[str]:
    warnings: list[str] = []
    if not source_path.is_file():
        warnings.append(f"Missing ppt-master source document: {source_path}")
    if not run_prompt_path.is_file():
        warnings.append(f"Missing ppt-master run prompt: {run_prompt_path}")
    return warnings


def _suggested_steps(
    *,
    job_id: str,
    installation: PptMasterInstallation,
    run_prompt_path: Path,
    source_path: Path,
    output_dir: Path,
    project_dir: Path | None,
) -> list[str]:
    root_text = str(installation.root_path) if installation.root_path is not None else "$PPT_MASTER_DIR"
    skill_text = (
        str(installation.skill_path)
        if installation.skill_path is not None
        else "$PPT_MASTER_DIR/skills/ppt-master/SKILL.md"
    )
    steps = [
        f"Open the local ppt-master repository: cd {root_text}",
        f"Read the ppt-master workflow contract first: {skill_text}",
        f"Use this run prompt: {run_prompt_path}",
        f"Use this source document: {source_path}",
        f"Write generated_by_ppt_master.pptx and generation_notes.md into: {output_dir}",
        (
            "After the external ppt-master run finishes, register the output: "
            f"uv run python scripts/register_ppt_master_output.py --job-id {job_id} --output-dir {output_dir}"
        ),
    ]
    if project_dir is not None:
        steps.insert(0, f"Open the prepared visual project scaffold: cd {project_dir}")
        steps.insert(1, f"Follow its PROJECT_INSTRUCTIONS.md: {project_dir / 'PROJECT_INSTRUCTIONS.md'}")
    return steps
