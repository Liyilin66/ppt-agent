"""Visual project bootstrap helpers for local ppt-master workflows.

This module creates a job-local scaffold that an external AI IDE can continue
inside. It does not generate SVG slides, call a model, run ppt-master scripts,
open PowerPoint, install dependencies, or copy ppt-master source code.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.job_store import ArtifactRecord, JobStore
from ppt_agent.models import StrictModel
from ppt_agent.ppt_master_integration import detect_ppt_master_installation
from ppt_agent.ppt_master_output import PPT_MASTER_OUTPUT_PPTX_FILENAME


PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT = "ppt_master_visual_project_manifest"
PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT = "ppt_master_project_instructions"
PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME = "ppt_master_visual_project_manifest.json"
PROJECT_MANIFEST_FILENAME = "project_manifest.json"
PROJECT_INSTRUCTIONS_FILENAME = "PROJECT_INSTRUCTIONS.md"
DEFAULT_VISUAL_PROJECT_NAME = "ppt_master_visual_project"

PptMasterVisualProjectStatus = Literal[
    "created",
    "already_exists",
    "missing_package",
    "ppt_master_unavailable",
    "failed",
]


class PptMasterVisualProject(StrictModel):
    job_id: str
    job_dir: Path
    package_dir: Path
    ppt_master_root: Path | None = None
    project_dir: Path
    source_path: Path
    run_prompt_path: Path
    project_source_path: Path
    project_prompt_path: Path
    project_instructions_path: Path
    project_manifest_path: Path
    job_manifest_path: Path
    expected_svg_output_dir: Path
    expected_svg_final_dir: Path
    expected_pptx_path: Path
    status: PptMasterVisualProjectStatus
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    created_at: str


class PptMasterVisualProjectRegistrationResult(StrictModel):
    manifest_artifact: ArtifactRecord
    instructions_artifact: ArtifactRecord | None = None


def bootstrap_ppt_master_visual_project(
    job_id: str,
    job_dir: Path,
    ppt_master_root: Path | None = None,
    project_name: str | None = None,
    overwrite: bool = False,
) -> PptMasterVisualProject:
    """Create a job-local ppt-master visual project scaffold."""

    resolved_job_dir = Path(job_dir).expanduser().resolve(strict=False)
    package_dir = resolved_job_dir / "ppt_master_package"
    source_path = package_dir / "source.md"
    run_prompt_path = package_dir / "run_prompt.md"
    output_dir = resolved_job_dir / "ppt_master_output"
    project_dir = output_dir / _project_name(project_name)
    paths = _project_paths(
        job_id=job_id,
        job_dir=resolved_job_dir,
        package_dir=package_dir,
        source_path=source_path,
        run_prompt_path=run_prompt_path,
        project_dir=project_dir,
        ppt_master_root=None,
        status="failed",
        warnings=[],
    )

    package_warnings = _package_warnings(source_path, run_prompt_path)
    if package_warnings:
        return _write_job_manifest(paths.model_copy(update={
            "status": "missing_package",
            "warnings": package_warnings,
            "next_steps": [
                "Create or recover ppt_master_package/source.md and run_prompt.md before bootstrapping.",
            ],
        }))

    installation = detect_ppt_master_installation(ppt_master_root)
    paths = paths.model_copy(update={"ppt_master_root": installation.root_path})
    if not installation.is_available:
        return _write_job_manifest(paths.model_copy(update={
            "status": "ppt_master_unavailable",
            "warnings": installation.missing_paths,
            "next_steps": [
                "Clone ppt-master locally and set PPT_MASTER_DIR, or pass --ppt-master-dir.",
            ],
        }))

    if project_dir.exists() and not overwrite:
        return _write_job_manifest(paths.model_copy(update={
            "status": "already_exists",
            "next_steps": _next_steps(paths, installation.skill_path),
        }))

    try:
        if project_dir.exists() and overwrite:
            shutil.rmtree(project_dir)
        _create_scaffold(paths, installation.skill_path)
    except OSError as exc:
        return _write_job_manifest(paths.model_copy(update={
            "status": "failed",
            "warnings": [str(exc)],
            "next_steps": ["Inspect filesystem permissions and retry bootstrap."],
        }))

    return _write_project_and_job_manifests(paths.model_copy(update={
        "status": "created",
        "next_steps": _next_steps(paths, installation.skill_path),
    }))


def read_ppt_master_visual_project_manifest(path: Path) -> PptMasterVisualProject | None:
    try:
        return PptMasterVisualProject.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_bootstrapped_visual_project(job_dir: Path) -> Path | None:
    """Return the most likely bootstrap project directory for a job, if any."""

    resolved_job_dir = Path(job_dir).expanduser().resolve(strict=False)
    manifest_path = resolved_job_dir / PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME
    manifest = read_ppt_master_visual_project_manifest(manifest_path)
    if manifest is not None and manifest.project_dir.is_dir():
        return manifest.project_dir.resolve(strict=False)

    output_dir = resolved_job_dir / "ppt_master_output"
    if not output_dir.is_dir():
        return None
    candidates: list[Path] = []
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        if (child / PROJECT_INSTRUCTIONS_FILENAME).is_file() or (child / PROJECT_MANIFEST_FILENAME).is_file():
            candidates.append(child.resolve(strict=False))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def register_ppt_master_visual_project_artifacts(
    store: JobStore,
    *,
    job_id: str,
    job_dir: Path,
) -> PptMasterVisualProjectRegistrationResult:
    resolved_job_dir = Path(job_dir).expanduser().resolve(strict=False)
    manifest_path = resolved_job_dir / PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME
    manifest = read_ppt_master_visual_project_manifest(manifest_path)
    manifest_artifact = _ensure_artifact(
        store,
        job_id=job_id,
        name=PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT,
        kind="json",
        path=manifest_path,
    )
    instructions_artifact = None
    if manifest is not None and manifest.project_instructions_path.is_file():
        instructions_artifact = _ensure_artifact(
            store,
            job_id=job_id,
            name=PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT,
            kind="md",
            path=manifest.project_instructions_path,
        )
    return PptMasterVisualProjectRegistrationResult(
        manifest_artifact=manifest_artifact,
        instructions_artifact=instructions_artifact,
    )


def _project_paths(
    *,
    job_id: str,
    job_dir: Path,
    package_dir: Path,
    source_path: Path,
    run_prompt_path: Path,
    project_dir: Path,
    ppt_master_root: Path | None,
    status: PptMasterVisualProjectStatus,
    warnings: list[str],
) -> PptMasterVisualProject:
    return PptMasterVisualProject(
        job_id=job_id,
        job_dir=job_dir,
        package_dir=package_dir,
        ppt_master_root=ppt_master_root,
        project_dir=project_dir,
        source_path=source_path,
        run_prompt_path=run_prompt_path,
        project_source_path=project_dir / "inputs" / "source.md",
        project_prompt_path=project_dir / "inputs" / "run_prompt.md",
        project_instructions_path=project_dir / PROJECT_INSTRUCTIONS_FILENAME,
        project_manifest_path=project_dir / PROJECT_MANIFEST_FILENAME,
        job_manifest_path=job_dir / PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME,
        expected_svg_output_dir=project_dir / "svg_output",
        expected_svg_final_dir=project_dir / "svg_final",
        expected_pptx_path=job_dir / "ppt_master_output" / PPT_MASTER_OUTPUT_PPTX_FILENAME,
        status=status,
        warnings=warnings,
        next_steps=[],
        created_at=_now(),
    )


def _create_scaffold(project: PptMasterVisualProject, skill_path: Path | None) -> None:
    for directory in [
        project.project_dir / "inputs",
        project.expected_svg_output_dir,
        project.expected_svg_final_dir,
        project.project_dir / "exports",
        project.project_dir / "logs",
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project.source_path, project.project_source_path)
    shutil.copy2(project.run_prompt_path, project.project_prompt_path)
    project.project_instructions_path.write_text(
        _build_project_instructions(project, skill_path),
        encoding="utf-8",
    )


def _write_project_and_job_manifests(project: PptMasterVisualProject) -> PptMasterVisualProject:
    project.project_manifest_path.write_text(
        json.dumps(project.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return _write_job_manifest(project)


def _write_job_manifest(project: PptMasterVisualProject) -> PptMasterVisualProject:
    project.job_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    project.job_manifest_path.write_text(
        json.dumps(project.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return project


def _build_project_instructions(project: PptMasterVisualProject, skill_path: Path | None) -> str:
    skill_text = str(skill_path) if skill_path is not None else "$PPT_MASTER_DIR/skills/ppt-master/SKILL.md"
    return f"""# PPT Master Visual Project Instructions

This directory is a job-local visual project scaffold created by ppt-agent.
ppt-agent has not generated SVG slides, called a model, run ppt-master, opened PowerPoint, or installed dependencies.

## Workflow Contract

1. First read and follow the local ppt-master skill contract:

```text
{skill_text}
```

2. Use this source document as the content source:

```text
{project.project_source_path}
```

3. Use this prompt as the generation requirements:

```text
{project.project_prompt_path}
```

## Target Output

- Generate a 30-page 16:9 Chinese technical product sharing presentation.
- Create editable SVG slides, one slide per page.
- Write authored SVG files into:

```text
{project.expected_svg_output_dir}
```

- After finalize/post-processing, SVG files should be available in:

```text
{project.expected_svg_final_dir}
```

- Final editable PPTX should be exported to either:

```text
{project.project_dir / "exports" / PPT_MASTER_OUTPUT_PPTX_FILENAME}
```

or the ppt-agent job output path:

```text
{project.expected_pptx_path}
```

## Content Guardrails

- Do not include template placeholder wording in audience-visible slide text.
- Do not output risk / impact / mitigation as visible body labels.
- Do not output Option A / Option B as visible body labels.
- Do not output 判断点 1 / 判断点 2 / 判断点 3.
- Do not write generation instructions, workflow notes, or prompt text into audience-facing slide content.
- Keep the visual style as a technical product sharing deck, not marketing material.
- Use a very pale blue-green background direction.
- Keep each page focused on one clear point.

## Hand Back to ppt-agent

After SVG slides exist, return to ppt-agent and run:

```bash
uv run python scripts/run_ppt_master_local_export.py --job-id {project.job_id} --job-dir {project.job_dir}
```

The local export runner will only run deterministic post-processing/export steps. It still will not call a model.
"""


def _next_steps(project: PptMasterVisualProject, skill_path: Path | None) -> list[str]:
    skill_text = str(skill_path) if skill_path is not None else "$PPT_MASTER_DIR/skills/ppt-master/SKILL.md"
    return [
        f"Open the prepared visual project: cd {project.project_dir}",
        f"Read the ppt-master skill contract first: {skill_text}",
        f"Follow PROJECT_INSTRUCTIONS.md: {project.project_instructions_path}",
        f"Generate SVG slides into: {project.expected_svg_output_dir}",
        f"After SVGs exist, run: uv run python scripts/run_ppt_master_local_export.py --job-id {project.job_id} --job-dir {project.job_dir}",
    ]


def _package_warnings(source_path: Path, run_prompt_path: Path) -> list[str]:
    warnings: list[str] = []
    if not source_path.is_file():
        warnings.append(f"Missing ppt-master source document: {source_path}")
    if not run_prompt_path.is_file():
        warnings.append(f"Missing ppt-master run prompt: {run_prompt_path}")
    return warnings


def _project_name(project_name: str | None) -> str:
    if not project_name:
        return DEFAULT_VISUAL_PROJECT_NAME
    name = Path(project_name).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return name or DEFAULT_VISUAL_PROJECT_NAME


def _ensure_artifact(
    store: JobStore,
    *,
    job_id: str,
    name: str,
    kind: str,
    path: Path,
) -> ArtifactRecord:
    resolved_path = path.expanduser().resolve(strict=False)
    for artifact in reversed(store.list_artifacts(job_id)):
        if artifact.name != name:
            continue
        if artifact.kind == kind and artifact.path.expanduser().resolve(strict=False) == resolved_path:
            return artifact
    return store.add_artifact(job_id, name=name, kind=kind, path=resolved_path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
