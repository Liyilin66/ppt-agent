"""Deterministic local export bridge for existing ppt-master projects.

The runner only handles scriptable post-processing/export from an already
generated ppt-master project. It never calls a model, creates SVG slides, opens
PowerPoint, installs dependencies, or updates the ppt-master repository.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.job_store import ArtifactRecord, JobStore
from ppt_agent.models import StrictModel
from ppt_agent.ppt_master_integration import detect_ppt_master_installation
from ppt_agent.ppt_master_output import (
    PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
    PPT_MASTER_OUTPUT_NOTES_FILENAME,
    PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
    PPT_MASTER_OUTPUT_PPTX_FILENAME,
    register_ppt_master_output_artifacts,
)


PPT_MASTER_RUNNER_RESULT_ARTIFACT = "ppt_master_runner_result"
PPT_MASTER_RUNNER_RESULT_FILENAME = "ppt_master_runner_result.json"
DEFAULT_EXPORT_TIMEOUT_SECONDS = 600

PptMasterRunnerStatus = Literal[
    "succeeded",
    "requires_external_ai_generation",
    "ppt_master_unavailable",
    "missing_package",
    "missing_project",
    "missing_export_inputs",
    "export_failed",
]


class PptMasterRunnerResult(StrictModel):
    job_id: str
    status: PptMasterRunnerStatus
    ppt_master_root: Path | None = None
    project_dir: Path | None = None
    output_dir: Path
    pptx_path: Path | None = None
    notes_path: Path | None = None
    slide_count: int | None = Field(default=None, ge=0)
    registered: bool = False
    artifacts: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    commands: list[list[str]] = Field(default_factory=list)
    command_log: list[str] = Field(default_factory=list)
    message: str
    created_at: str


def run_ppt_master_local_export(
    job_id: str,
    job_dir: Path,
    ppt_master_root: Path | None = None,
    project_dir: Path | None = None,
    register_output: bool = True,
    *,
    store: JobStore | None = None,
    timeout_seconds: int = DEFAULT_EXPORT_TIMEOUT_SECONDS,
    python_executable: str | None = None,
) -> PptMasterRunnerResult:
    """Run deterministic ppt-master export scripts for an existing project.

    A visual project must already exist under ``ppt_master_output/`` or be
    supplied with ``project_dir``. If the project/SVGs are missing, the runner
    reports ``requires_external_ai_generation`` instead of pretending it can
    create visual content.
    """

    resolved_job_dir = Path(job_dir).expanduser().resolve(strict=False)
    output_dir = resolved_job_dir / "ppt_master_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = resolved_job_dir / "ppt_master_package"
    result_path = resolved_job_dir / PPT_MASTER_RUNNER_RESULT_FILENAME

    installation = detect_ppt_master_installation(ppt_master_root)
    if not installation.is_available or installation.scripts_path is None:
        return _write_result(
            result_path,
            _base_result(
                job_id=job_id,
                status="ppt_master_unavailable",
                installation_root=installation.root_path,
                output_dir=output_dir,
                message="Local ppt-master is unavailable. Clone it locally or set PPT_MASTER_DIR.",
                warnings=installation.missing_paths,
            ),
        )

    if not _has_package(package_dir):
        return _write_result(
            result_path,
            _base_result(
                job_id=job_id,
                status="missing_package",
                installation_root=installation.root_path,
                output_dir=output_dir,
                message="This job does not have a complete ppt_master_package/.",
                warnings=_package_warnings(package_dir),
            ),
        )

    resolved_project_dir = _select_project_dir(output_dir, project_dir)
    if resolved_project_dir is None:
        return _write_result(
            result_path,
            _base_result(
                job_id=job_id,
                status="requires_external_ai_generation",
                installation_root=installation.root_path,
                output_dir=output_dir,
                message=(
                    "No ppt-master visual project was found. Use run_prompt.md in the local "
                    "AI IDE / ppt-master skill first, then run local export again."
                ),
            ),
        )

    export_source = _detect_export_source(resolved_project_dir)
    if export_source is None:
        return _write_result(
            result_path,
            _base_result(
                job_id=job_id,
                status="requires_external_ai_generation",
                installation_root=installation.root_path,
                project_dir=resolved_project_dir,
                output_dir=output_dir,
                message=(
                    "The ppt-master project has no exportable SVG slides yet. Use the "
                    "external AI IDE / ppt-master skill to generate SVG pages first."
                ),
                warnings=[
                    f"No .svg files found in {resolved_project_dir / 'svg_output'} or "
                    f"{resolved_project_dir / 'svg_final'}."
                ],
            ),
        )

    pptx_path = output_dir / PPT_MASTER_OUTPUT_PPTX_FILENAME
    notes_path = output_dir / PPT_MASTER_OUTPUT_NOTES_FILENAME
    command_log: list[str] = []
    warnings: list[str] = []
    commands: list[list[str]] = []

    if pptx_path.is_file():
        warnings.append(f"Existing PPTX detected; deterministic export was skipped: {pptx_path}")
        slide_count = _read_slide_count(pptx_path, warnings)
        if not notes_path.is_file():
            _write_generation_notes(
                notes_path,
                status="succeeded",
                project_dir=resolved_project_dir,
                pptx_path=pptx_path,
                command_log=command_log,
                skipped_existing=True,
            )
        else:
            warnings.append(f"Existing generation notes preserved: {notes_path}")
        registered, artifacts = _register_output(
            store=store,
            job_id=job_id,
            output_dir=output_dir,
            register_output=register_output,
            warnings=warnings,
        )
        return _write_result(
            result_path,
            _base_result(
                job_id=job_id,
                status="succeeded",
                installation_root=installation.root_path,
                project_dir=resolved_project_dir,
                output_dir=output_dir,
                pptx_path=pptx_path,
                notes_path=notes_path,
                slide_count=slide_count,
                registered=registered,
                artifacts=artifacts,
                warnings=warnings,
                commands=commands,
                command_log=command_log,
                message="PPT Master output already existed and is ready for registration/download.",
            ),
        )

    python = python_executable or sys.executable
    commands = _build_export_commands(
        python_executable=python,
        scripts_path=installation.scripts_path,
        project_dir=resolved_project_dir,
        pptx_path=pptx_path,
        export_source=export_source,
    )
    if export_source == "svg_final":
        warnings.append("svg_output is missing or empty; exporting from existing svg_final without finalize.")

    skill_dir = installation.scripts_path.parent
    for command in commands:
        completed = _run_command(command, cwd=skill_dir, timeout_seconds=timeout_seconds)
        command_log.append(_format_command_result(command, completed))
        if completed.returncode != 0:
            error = (
                f"ppt-master export command failed with exit code {completed.returncode}: "
                f"{' '.join(command)}"
            )
            return _write_result(
                result_path,
                _base_result(
                    job_id=job_id,
                    status="export_failed",
                    installation_root=installation.root_path,
                    project_dir=resolved_project_dir,
                    output_dir=output_dir,
                    errors=[error, _clip(completed.stderr or completed.stdout)],
                    warnings=warnings,
                    commands=commands,
                    command_log=command_log,
                    message="Deterministic ppt-master export failed. See errors and command_log.",
                ),
            )

    if not pptx_path.is_file():
        return _write_result(
            result_path,
            _base_result(
                job_id=job_id,
                status="export_failed",
                installation_root=installation.root_path,
                project_dir=resolved_project_dir,
                output_dir=output_dir,
                errors=[f"Export command completed but PPTX was not found: {pptx_path}"],
                warnings=warnings,
                commands=commands,
                command_log=command_log,
                message="Deterministic ppt-master export did not publish the expected PPTX.",
            ),
        )

    slide_count = _read_slide_count(pptx_path, warnings)
    _write_generation_notes(
        notes_path,
        status="succeeded",
        project_dir=resolved_project_dir,
        pptx_path=pptx_path,
        command_log=command_log,
        skipped_existing=False,
    )
    registered, artifacts = _register_output(
        store=store,
        job_id=job_id,
        output_dir=output_dir,
        register_output=register_output,
        warnings=warnings,
    )
    return _write_result(
        result_path,
        _base_result(
            job_id=job_id,
            status="succeeded",
            installation_root=installation.root_path,
            project_dir=resolved_project_dir,
            output_dir=output_dir,
            pptx_path=pptx_path,
            notes_path=notes_path,
            slide_count=slide_count,
            registered=registered,
            artifacts=artifacts,
            warnings=warnings,
            commands=commands,
            command_log=command_log,
            message="Deterministic ppt-master local export succeeded.",
        ),
    )


def read_ppt_master_runner_result(path: Path) -> PptMasterRunnerResult | None:
    try:
        return PptMasterRunnerResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def register_ppt_master_runner_result_artifact(
    store: JobStore,
    *,
    job_id: str,
    job_dir: Path,
) -> ArtifactRecord:
    result_path = Path(job_dir).expanduser().resolve(strict=False) / PPT_MASTER_RUNNER_RESULT_FILENAME
    for artifact in reversed(store.list_artifacts(job_id)):
        if artifact.name != PPT_MASTER_RUNNER_RESULT_ARTIFACT:
            continue
        if artifact.path.expanduser().resolve(strict=False) == result_path:
            return artifact
    return store.add_artifact(
        job_id,
        name=PPT_MASTER_RUNNER_RESULT_ARTIFACT,
        kind="json",
        path=result_path,
    )


def _base_result(
    *,
    job_id: str,
    status: PptMasterRunnerStatus,
    installation_root: Path | None,
    output_dir: Path,
    message: str,
    project_dir: Path | None = None,
    pptx_path: Path | None = None,
    notes_path: Path | None = None,
    slide_count: int | None = None,
    registered: bool = False,
    artifacts: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    commands: list[list[str]] | None = None,
    command_log: list[str] | None = None,
) -> PptMasterRunnerResult:
    return PptMasterRunnerResult(
        job_id=job_id,
        status=status,
        ppt_master_root=installation_root,
        project_dir=project_dir,
        output_dir=output_dir,
        pptx_path=pptx_path,
        notes_path=notes_path,
        slide_count=slide_count,
        registered=registered,
        artifacts=artifacts or {},
        warnings=warnings or [],
        errors=errors or [],
        commands=commands or [],
        command_log=command_log or [],
        message=message,
        created_at=datetime.now(UTC).isoformat(),
    )


def _write_result(path: Path, result: PptMasterRunnerResult) -> PptMasterRunnerResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return result


def _has_package(package_dir: Path) -> bool:
    return (
        package_dir.is_dir()
        and (package_dir / "source.md").is_file()
        and (package_dir / "run_prompt.md").is_file()
    )


def _package_warnings(package_dir: Path) -> list[str]:
    warnings: list[str] = []
    if not package_dir.is_dir():
        warnings.append(f"Missing ppt_master_package directory: {package_dir}")
    for filename in ("source.md", "run_prompt.md"):
        path = package_dir / filename
        if not path.is_file():
            warnings.append(f"Missing ppt-master package file: {path}")
    return warnings


def _select_project_dir(output_dir: Path, project_dir: Path | None) -> Path | None:
    if project_dir is not None:
        resolved = Path(project_dir).expanduser().resolve(strict=False)
        return resolved if resolved.is_dir() else None
    candidates: list[Path] = []
    if not output_dir.is_dir():
        return None
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        if (
            (child / "svg_output").is_dir()
            or (child / "svg_final").is_dir()
            or (child / "spec_lock.md").is_file()
            or (child / "sources").is_dir()
        ):
            candidates.append(child.resolve(strict=False))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _detect_export_source(project_dir: Path) -> Literal["svg_output", "svg_final"] | None:
    svg_output = project_dir / "svg_output"
    if svg_output.is_dir() and any(svg_output.glob("*.svg")):
        return "svg_output"
    svg_final = project_dir / "svg_final"
    if svg_final.is_dir() and any(svg_final.glob("*.svg")):
        return "svg_final"
    return None


def _build_export_commands(
    *,
    python_executable: str,
    scripts_path: Path,
    project_dir: Path,
    pptx_path: Path,
    export_source: Literal["svg_output", "svg_final"],
) -> list[list[str]]:
    commands: list[list[str]] = []
    quality_target = project_dir if export_source == "svg_output" else project_dir / "svg_final"
    commands.append([python_executable, str(scripts_path / "svg_quality_checker.py"), str(quality_target)])
    if export_source == "svg_output":
        commands.append([python_executable, str(scripts_path / "finalize_svg.py"), str(project_dir)])
    export_command = [
        python_executable,
        str(scripts_path / "svg_to_pptx.py"),
        str(project_dir),
        "--only",
        "native",
        "-o",
        str(pptx_path),
    ]
    if export_source == "svg_final":
        export_command.extend(["-s", "final"])
    commands.append(export_command)
    return commands


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=f"Command timed out after {timeout_seconds} seconds.",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))


def _format_command_result(command: list[str], completed: subprocess.CompletedProcess[str]) -> str:
    return (
        f"$ {' '.join(command)}\n"
        f"exit_code: {completed.returncode}\n"
        f"stdout:\n{_clip(completed.stdout)}\n"
        f"stderr:\n{_clip(completed.stderr)}"
    )


def _register_output(
    *,
    store: JobStore | None,
    job_id: str,
    output_dir: Path,
    register_output: bool,
    warnings: list[str],
) -> tuple[bool, dict[str, str]]:
    if not register_output:
        return False, {}
    if store is None:
        warnings.append("Output registration skipped because no JobStore was provided.")
        return False, {}
    job = store.get_job(job_id)
    if job is None:
        warnings.append("Output registration skipped because the job was not found in the JobStore.")
        return False, {}
    if job.job_type != "long_deck":
        warnings.append("Output registration skipped because the job is not a long_deck job.")
        return False, {}

    registration = register_ppt_master_output_artifacts(store, job_id=job_id, output_dir=output_dir)
    artifacts = {
        PPT_MASTER_OUTPUT_PPTX_ARTIFACT: registration.pptx_artifact.artifact_id
        if registration.pptx_artifact is not None
        else "",
        PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT: registration.manifest_artifact.artifact_id,
    }
    if registration.notes_artifact is not None:
        artifacts[PPT_MASTER_OUTPUT_NOTES_ARTIFACT] = registration.notes_artifact.artifact_id
    return True, {key: value for key, value in artifacts.items() if value}


def _write_generation_notes(
    notes_path: Path,
    *,
    status: str,
    project_dir: Path,
    pptx_path: Path,
    command_log: list[str],
    skipped_existing: bool,
) -> None:
    action = "Existing PPTX was detected; export commands were not rerun." if skipped_existing else (
        "Deterministic ppt-master post-processing/export scripts were run."
    )
    commands_text = "\n\n".join(command_log) if command_log else "No export commands were run."
    notes_path.write_text(
        "# PPT Master Local Runner Notes\n\n"
        f"- Status: {status}\n"
        f"- Project: {project_dir}\n"
        f"- PPTX: {pptx_path}\n"
        "- Editable claim: Native DrawingML shapes generated by ppt-master export path.\n"
        "- Model calls: none by ppt-agent local runner.\n"
        f"- Action: {action}\n\n"
        "## Command Log\n\n"
        f"{commands_text}\n",
        encoding="utf-8",
    )


def _read_slide_count(pptx_path: Path, warnings: list[str]) -> int | None:
    try:
        from pptx import Presentation

        return len(Presentation(pptx_path).slides)
    except Exception as exc:  # pragma: no cover - dependency/runtime failures are environment-specific.
        warnings.append(f"Could not read slide count from PPTX: {exc}")
        return None


def _clip(value: str | None, *, limit: int = 4000) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... [truncated]"
