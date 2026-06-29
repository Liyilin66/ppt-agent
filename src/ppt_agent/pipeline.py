"""Reusable build pipeline service for Deck IR generation and rendering."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Literal, TypeVar

from pydantic import Field

from ppt_agent.export import write_model_json
from ppt_agent.generation import DeckGenerationRequest, GenerationResult, generate_deck_with_quality_gate
from ppt_agent.load import load_patch, load_theme
from ppt_agent.models import StrictModel
from ppt_agent.patch import PatchResult, apply_patch
from ppt_agent.renderer import render_deck_to_pptx
from ppt_agent.runtime import JobTimeoutError, StageObserver, observed_stage


T = TypeVar("T")


class BuildPipelineRequest(StrictModel):
    generation_request: DeckGenerationRequest
    theme_path: Path
    output_dir: Path
    min_qa_score: int = Field(default=80, ge=0, le=100)
    max_attempts: int = Field(default=2, ge=1)
    assets_dir: Path | None = None
    patch_path: Path | None = None


class BuildArtifact(StrictModel):
    name: str = Field(..., min_length=1)
    path: Path
    kind: Literal["json", "pptx"]


class BuildPipelineResult(StrictModel):
    generation_result: GenerationResult
    patch_result: PatchResult | None = None
    artifacts: list[BuildArtifact] = Field(default_factory=list)
    accepted: bool
    status_code: int
    messages: list[str] = Field(default_factory=list)


def _artifact(name: str, path: Path, kind: Literal["json", "pptx"]) -> BuildArtifact:
    return BuildArtifact(name=name, path=path, kind=kind)


def _validate_patch_path(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError(f"Patch file must be a .json file: {path}")
    if not path.is_file():
        raise ValueError(f"Patch file not found: {path}")


def _ensure_job_timeout(started_at: float, timeout_seconds: float | None, stage_name: str) -> None:
    if timeout_seconds is None:
        return
    elapsed = time.monotonic() - started_at
    if elapsed > timeout_seconds:
        raise JobTimeoutError(
            f"Job timed out while running stage '{stage_name}' after {timeout_seconds:g} seconds."
        )


@contextmanager
def _pipeline_stage(
    observer: StageObserver | None,
    started_at: float,
    timeout_seconds: float | None,
    stage_name: str,
    **metadata: Any,
) -> Generator[None, None, None]:
    _ensure_job_timeout(started_at, timeout_seconds, stage_name)
    with observed_stage(observer, stage_name, **metadata):
        yield
    _ensure_job_timeout(started_at, timeout_seconds, stage_name)


def _run_stage(
    observer: StageObserver | None,
    started_at: float,
    timeout_seconds: float | None,
    stage_name: str,
    call: Callable[[], T],
    **metadata: Any,
) -> T:
    with _pipeline_stage(observer, started_at, timeout_seconds, stage_name, **metadata):
        return call()


def run_build_pipeline(
    model: Any,
    request: BuildPipelineRequest,
    *,
    stage_observer: StageObserver | None = None,
    llm_timeout_seconds: float | None = None,
    job_timeout_seconds: float | None = None,
    started_at_monotonic: float | None = None,
) -> BuildPipelineResult:
    """Run the product build pipeline and write all configured artifacts."""

    started_at = time.monotonic() if started_at_monotonic is None else started_at_monotonic

    if request.patch_path is not None:
        _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "apply_patch",
            lambda: _validate_patch_path(request.patch_path),
            patch_path=str(request.patch_path),
        )

    theme = load_theme(request.theme_path)
    generation_request = request.generation_request
    if generation_request.style is None:
        generation_request = generation_request.model_copy(update={"style": theme.name})

    generation_result = _run_stage(
        stage_observer,
        started_at,
        job_timeout_seconds,
        "generate_deck",
        lambda: generate_deck_with_quality_gate(
            model,
            generation_request,
            theme=theme,
            min_score=request.min_qa_score,
            max_attempts=request.max_attempts,
            timeout_seconds=llm_timeout_seconds,
            stage_observer=stage_observer,
        ),
        slide_count=generation_request.slide_count,
        use_deck_plan=True,
    )

    output_dir = request.output_dir
    artifacts: list[BuildArtifact] = []

    with _pipeline_stage(stage_observer, started_at, job_timeout_seconds, "save_artifacts"):
        deck_path = write_model_json(generation_result.deck, output_dir / "generated_deck_ir.json")
        artifacts.append(_artifact("generated_deck_ir", deck_path, "json"))

        qa_path = write_model_json(generation_result.qa_report, output_dir / "generated_qa_report.json")
        artifacts.append(_artifact("generated_qa_report", qa_path, "json"))

        attempts_path = write_model_json(generation_result, output_dir / "generated_attempts.json")
        artifacts.append(_artifact("generated_attempts", attempts_path, "json"))

    pptx_path = _run_stage(
        stage_observer,
        started_at,
        job_timeout_seconds,
        "render_pptx",
        lambda: render_deck_to_pptx(
            generation_result.deck,
            theme,
            output_dir / "generated_deck.pptx",
            assets_dir=request.assets_dir,
        ),
        slide_count=len(generation_result.deck.slides),
    )
    artifacts.append(_artifact("generated_deck", pptx_path, "pptx"))

    status_code = 0
    messages: list[str] = []
    if not generation_result.accepted:
        messages.append(
            "Build completed, but generated Deck IR did not meet the QA score gate: "
            f"{generation_result.qa_report.score} < {request.min_qa_score}"
        )
        status_code = 2

    patch_result: PatchResult | None = None
    if request.patch_path is not None:
        patch_result = _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "apply_patch",
            lambda: apply_patch(generation_result.deck, load_patch(request.patch_path)),
            patch_path=str(request.patch_path),
        )

        with _pipeline_stage(stage_observer, started_at, job_timeout_seconds, "save_artifacts"):
            patched_deck_path = write_model_json(patch_result.deck, output_dir / "patched_deck_ir.json")
            artifacts.append(_artifact("patched_deck_ir", patched_deck_path, "json"))

            patch_result_path = write_model_json(patch_result, output_dir / "patch_result.json")
            artifacts.append(_artifact("patch_result", patch_result_path, "json"))

        patched_pptx_path = _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "render_pptx",
            lambda: render_deck_to_pptx(
                patch_result.deck,
                theme,
                output_dir / "patched_deck.pptx",
                assets_dir=request.assets_dir,
            ),
            slide_count=len(patch_result.deck.slides),
        )
        artifacts.append(_artifact("patched_deck", patched_pptx_path, "pptx"))

        if patch_result.issues:
            messages.append(f"Patch completed with {len(patch_result.issues)} issue(s). See {patch_result_path}.")
            status_code = 2

    return BuildPipelineResult(
        generation_result=generation_result,
        patch_result=patch_result,
        artifacts=artifacts,
        accepted=status_code == 0,
        status_code=status_code,
        messages=messages,
    )
