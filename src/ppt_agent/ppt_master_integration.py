"""Local ppt-master integration helpers.

This module prepares handoff packages for a local ppt-master workflow. It never
imports ppt-master, installs dependencies, calls an LLM, opens PowerPoint, or
runs ppt-master scripts.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from ppt_agent.models import Deck, StrictModel
from ppt_agent.ppt_master_adapter import export_deck_ir_to_ppt_master_markdown


PPT_MASTER_UNAVAILABLE_WARNING = (
    "ppt-master installation is not available. Clone ppt-master locally and set "
    "PPT_MASTER_DIR, or pass --ppt-master-dir to this command."
)
EXPECTED_PPT_MASTER_REPO = "github.com/hugohe3/ppt-master"
EXPECTED_PPT_MASTER_CLONE_URL = "https://github.com/hugohe3/ppt-master.git"


class PptMasterInstallation(StrictModel):
    root_path: Path | None = None
    skill_path: Path | None = None
    scripts_path: Path | None = None
    is_available: bool
    missing_paths: list[str] = Field(default_factory=list)
    version_info: str | None = None


class PptMasterPackageManifest(StrictModel):
    source_path: Path
    run_prompt_path: Path
    readme_path: Path
    ppt_master_root: Path | None = None
    created_at: str
    is_available: bool
    missing_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    topic: str | None = None
    audience: str | None = None
    version_info: str | None = None


class PptMasterJobPackage(StrictModel):
    output_dir: Path
    source_path: Path
    run_prompt_path: Path
    readme_path: Path
    manifest_path: Path
    installation: PptMasterInstallation
    warnings: list[str] = Field(default_factory=list)


class PptMasterSetupCheck(StrictModel):
    root_path: Path
    is_available: bool
    missing_paths: list[str] = Field(default_factory=list)
    skill_path: Path
    scripts_path: Path
    has_requirements: bool
    has_readme: bool
    has_readme_cn: bool
    is_git_repo: bool
    git_remote_origin: str | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    is_expected_repo: bool
    warnings: list[str] = Field(default_factory=list)
    suggested_commands: list[str] = Field(default_factory=list)


def detect_ppt_master_installation(root_path: str | Path | None = None) -> PptMasterInstallation:
    """Detect whether a local ppt-master checkout has the expected workflow files."""

    root = _candidate_root(root_path)
    if root is None:
        return PptMasterInstallation(
            is_available=False,
            missing_paths=["PPT_MASTER_DIR"],
        )

    root = root.expanduser().resolve(strict=False)
    skill_path = root / "skills" / "ppt-master" / "SKILL.md"
    scripts_path = root / "skills" / "ppt-master" / "scripts"
    requirements_path = root / "requirements.txt"
    pyproject_path = root / "pyproject.toml"

    missing_paths: list[str] = []
    if not root.is_dir():
        missing_paths.append(str(root))
    if not skill_path.is_file():
        missing_paths.append(str(skill_path))
    if not scripts_path.is_dir():
        missing_paths.append(str(scripts_path))
    if not requirements_path.is_file() and not pyproject_path.is_file():
        missing_paths.append(f"{requirements_path} or {pyproject_path}")

    return PptMasterInstallation(
        root_path=root,
        skill_path=skill_path,
        scripts_path=scripts_path,
        is_available=not missing_paths,
        missing_paths=missing_paths,
        version_info=_read_version_info(root),
    )


def check_ppt_master_setup(root_path: str | Path | None = None) -> PptMasterSetupCheck:
    """Inspect a local ppt-master checkout without changing it."""

    root = _candidate_root(root_path) or Path("ppt-master")
    root = root.expanduser().resolve(strict=False)
    skill_path = root / "skills" / "ppt-master" / "SKILL.md"
    scripts_path = root / "skills" / "ppt-master" / "scripts"
    requirements_path = root / "requirements.txt"
    pyproject_path = root / "pyproject.toml"
    readme_path = root / "README.md"
    readme_cn_path = root / "README_CN.md"

    installation = detect_ppt_master_installation(root)
    is_git_repo = _git_bool(root, "rev-parse", "--is-inside-work-tree")
    git_remote_origin = _git_text(root, "remote", "get-url", "origin") if is_git_repo else None
    git_branch = _git_text(root, "branch", "--show-current") if is_git_repo else None
    git_commit = _git_text(root, "rev-parse", "HEAD") if is_git_repo else None
    has_expected_remote = _is_expected_remote(git_remote_origin)
    is_expected_repo = bool(installation.is_available and is_git_repo and has_expected_remote)

    warnings: list[str] = []
    if not root.is_dir():
        warnings.append("ppt-master directory does not exist.")
    if not installation.is_available:
        warnings.append("ppt-master required workflow files are missing.")
    if root.is_dir() and not is_git_repo:
        warnings.append("ppt-master directory is not a git repository.")
    if is_git_repo and not has_expected_remote:
        warnings.append(
            f"git remote origin does not look like {EXPECTED_PPT_MASTER_REPO}; confirm this is the correct repository."
        )
    if root.is_dir() and is_git_repo and has_expected_remote and not installation.is_available:
        warnings.append("Repository remote looks correct, but the ppt-master skill structure is incomplete.")

    return PptMasterSetupCheck(
        root_path=root,
        is_available=installation.is_available,
        missing_paths=installation.missing_paths,
        skill_path=skill_path,
        scripts_path=scripts_path,
        has_requirements=requirements_path.is_file(),
        has_readme=readme_path.is_file(),
        has_readme_cn=readme_cn_path.is_file(),
        is_git_repo=is_git_repo,
        git_remote_origin=git_remote_origin,
        git_branch=git_branch,
        git_commit=git_commit,
        is_expected_repo=is_expected_repo,
        warnings=warnings,
        suggested_commands=_suggested_commands(root, is_expected_repo=is_expected_repo, root_exists=root.is_dir()),
    )


def create_ppt_master_job_package(
    deck_ir: Deck | Mapping[str, Any],
    output_dir: str | Path,
    *,
    ppt_master_root: str | Path | None = None,
    style_notes: str | Iterable[str] | None = None,
    audience: str | None = None,
    topic: str | None = None,
) -> PptMasterJobPackage:
    """Create a local handoff package for a ppt-master workflow."""

    installation = detect_ppt_master_installation(ppt_master_root)
    package_dir = Path(output_dir).expanduser().resolve(strict=False)
    package_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if not installation.is_available:
        warnings.append(PPT_MASTER_UNAVAILABLE_WARNING)

    source_path = package_dir / "source.md"
    export_deck_ir_to_ppt_master_markdown(
        deck_ir,
        source_path,
        style_notes=style_notes,
        topic=topic,
        audience=audience,
    )

    run_prompt_path = package_dir / "run_prompt.md"
    readme_path = package_dir / "README.md"
    manifest_path = package_dir / "manifest.json"

    run_prompt_path.write_text(
        _build_run_prompt(
            source_path=source_path,
            package_dir=package_dir,
            installation=installation,
            topic=topic,
            audience=audience,
        ),
        encoding="utf-8",
    )
    readme_path.write_text(
        _build_package_readme(
            source_path=source_path,
            run_prompt_path=run_prompt_path,
            package_dir=package_dir,
            installation=installation,
            warnings=warnings,
        ),
        encoding="utf-8",
    )

    manifest = PptMasterPackageManifest(
        source_path=source_path,
        run_prompt_path=run_prompt_path,
        readme_path=readme_path,
        ppt_master_root=installation.root_path,
        created_at=datetime.now(UTC).isoformat(),
        is_available=installation.is_available,
        missing_paths=installation.missing_paths,
        warnings=warnings,
        topic=topic,
        audience=audience,
        version_info=installation.version_info,
    )
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return PptMasterJobPackage(
        output_dir=package_dir,
        source_path=source_path,
        run_prompt_path=run_prompt_path,
        readme_path=readme_path,
        manifest_path=manifest_path,
        installation=installation,
        warnings=warnings,
    )


def _candidate_root(root_path: str | Path | None) -> Path | None:
    if root_path is not None:
        return Path(root_path)
    env_value = os.getenv("PPT_MASTER_DIR")
    if env_value:
        return Path(env_value)
    return None


def _read_version_info(root: Path) -> str | None:
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = payload.get("project")
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    version = project.get("version")
    if isinstance(name, str) and isinstance(version, str):
        return f"{name} {version}"
    if isinstance(version, str):
        return version
    return None


def _git_text(root: Path, *args: str) -> str | None:
    if not root.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _git_bool(root: Path, *args: str) -> bool:
    return _git_text(root, *args) == "true"


def _is_expected_remote(remote_url: str | None) -> bool:
    if not remote_url:
        return False
    normalized = remote_url.lower().replace("git@github.com:", "github.com/")
    normalized = normalized.removesuffix(".git")
    return EXPECTED_PPT_MASTER_REPO in normalized


def _suggested_commands(root: Path, *, is_expected_repo: bool, root_exists: bool) -> list[str]:
    if not root_exists:
        return [
            f"cd {root.parent}",
            f"git clone {EXPECTED_PPT_MASTER_CLONE_URL}",
        ]
    if is_expected_repo:
        return [
            f"cd {root}",
            "git status",
            "git pull",
        ]
    return [
        "Confirm whether this directory is the intended ppt-master checkout.",
        f"If not, clone {EXPECTED_PPT_MASTER_CLONE_URL} into the expected location.",
    ]


def _path_text(path: Path) -> str:
    return str(path.resolve(strict=False))


def _skill_path_text(installation: PptMasterInstallation) -> str:
    if installation.skill_path is not None:
        return _path_text(installation.skill_path)
    if installation.root_path is not None:
        return _path_text(installation.root_path / "skills" / "ppt-master" / "SKILL.md")
    return "$PPT_MASTER_DIR/skills/ppt-master/SKILL.md"


def _root_text(installation: PptMasterInstallation) -> str:
    if installation.root_path is not None:
        return _path_text(installation.root_path)
    return "$PPT_MASTER_DIR"


def _build_run_prompt(
    *,
    source_path: Path,
    package_dir: Path,
    installation: PptMasterInstallation,
    topic: str | None,
    audience: str | None,
) -> str:
    skill_path = _skill_path_text(installation)
    export_path = package_dir / "exports"
    topic_line = topic or "Use the Topic section in source.md."
    audience_line = audience or "Use the Audience section in source.md."

    return f"""# PPT Master Local Job Prompt

You are working inside the local ppt-master repository.

First read and follow this workflow contract:

```text
{skill_path}
```

Use this source document:

```text
{_path_text(source_path)}
```

Presentation target:
- Topic: {topic_line}
- Audience: {audience_line}
- Generate a 16:9 Chinese technical product sharing presentation.
- Keep the 30-page structure and chapter progression from source.md.
- Produce an editable PPTX; do not render whole slides as flat images.
- Use a very pale blue-green background direction.
- Keep each slide focused on one clear point.
- Avoid repeated card-only layouts; vary page rhythm and visual structure.
- Do not output risk / impact / mitigation as visible body labels.
- Do not output Option A / Option B as visible body labels.
- Do not output 判断点 1 / 判断点 2 / 判断点 3.
- Do not write generation instructions, workflow notes, or prompt text into audience-facing slide content.

Suggested output location:
- Prefer `{_path_text(export_path)}` for this package, or ppt-master's own `exports/` directory if the workflow requires it.

Operational boundaries:
- Do not copy ppt-master source code into ppt-agent.
- Do not ask the ppt-agent user to enter API keys.
- If local ppt-master dependencies or credentials are missing, stop and report the missing local setup instead of inventing outputs.
"""


def _build_package_readme(
    *,
    source_path: Path,
    run_prompt_path: Path,
    package_dir: Path,
    installation: PptMasterInstallation,
    warnings: list[str],
) -> str:
    warning_block = "\n".join(f"- {warning}" for warning in warnings) or "- None"
    availability = "available" if installation.is_available else "not available"

    return f"""# PPT Master Job Package

This directory is a handoff package from ppt-agent to a local ppt-master workflow.
ppt-agent generated these files but did not run ppt-master, call a model, open PowerPoint, or create a real PPTX.

## Files

- `source.md`: source document generated from validated Deck IR.
- `run_prompt.md`: prompt to paste into Claude Code, Codex, or CodeBuddy while working in the local ppt-master repo.
- `manifest.json`: machine-readable package metadata and installation detection result.
- `README.md`: this guide.

## Detected ppt-master

- Status: {availability}
- Root: {_root_text(installation)}
- Skill: {_skill_path_text(installation)}

Warnings:
{warning_block}

## How to Use

1. Clone or install ppt-master locally if it is not already present.
2. Set `PPT_MASTER_DIR` to the local ppt-master root, or pass `--ppt-master-dir` when creating this package.
3. Open the local ppt-master repo in Claude Code, Codex, or CodeBuddy.
4. Open `{_path_text(run_prompt_path)}` and give that prompt to the assistant in the ppt-master repo.
5. The prompt points ppt-master to `{_path_text(source_path)}` as the source document.
6. Suggested output can go under `{_path_text(package_dir / "exports")}` or under ppt-master's own `exports/`.

This spike does not embed ppt-master, copy its source code, or guarantee ppt-master output quality.
"""
