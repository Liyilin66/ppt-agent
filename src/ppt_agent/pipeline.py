"""Reusable build pipeline service for Deck IR generation and rendering."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Literal, TypeVar

from pydantic import Field, ValidationError

from ppt_agent.export import write_model_json
from ppt_agent.generation import (
    BatchGenerationArtifact,
    BatchGenerationRequest,
    DeckBriefArtifact,
    DeckGenerationRequest,
    DeckPlanArtifact,
    GenerationResult,
    generate_batch_deck_with_model,
    generate_deck_with_quality_gate,
)
from ppt_agent.long_deck import merge_batch_deck_irs
from ppt_agent.long_deck_qa import LongDeckQAReport, evaluate_long_deck_consistency
from ppt_agent.load import load_patch, load_theme
from ppt_agent.models import Deck, StrictModel
from ppt_agent.patch import (
    PatchResult,
    apply_patch,
    build_patch_failure_result,
    build_patchable_elements_report,
)
from ppt_agent.planning import LongDeckPlan, LongDeckPlanningRequest, build_deterministic_long_deck_plan
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
    long_deck_request: LongDeckPlanningRequest | None = None
    long_deck_plan: LongDeckPlan | None = None
    long_deck_batch_size: int = Field(default=10, ge=1)
    batch_generation_request: BatchGenerationRequest | None = None
    long_deck_batch_artifacts: list[BatchGenerationArtifact] | None = None
    long_deck_ir: Deck | None = None
    long_deck_qa_enabled: bool = False


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

    resolved_long_deck_plan = request.long_deck_plan
    if request.long_deck_request is not None and resolved_long_deck_plan is None:
        resolved_long_deck_plan = _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "build_long_deck_plan",
            lambda: build_deterministic_long_deck_plan(
                request.long_deck_request,
                batch_size=request.long_deck_batch_size,
            ),
            slide_count=request.long_deck_request.slide_count,
            batch_size=request.long_deck_batch_size,
            use_deck_plan=False,
        )

    batch_deck = None
    if request.batch_generation_request is not None:
        batch_request = request.batch_generation_request
        batch_deck = _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "generate_batch_deck",
            lambda: generate_batch_deck_with_model(
                model,
                batch_request,
                timeout_seconds=llm_timeout_seconds,
                stage_observer=stage_observer,
            ),
            batch_id=batch_request.batch_context.batch_id,
            start_slide=batch_request.batch_context.start_slide,
            end_slide=batch_request.batch_context.end_slide,
            use_deck_plan=False,
        )

    merged_long_deck = None
    if request.long_deck_batch_artifacts is not None:
        if resolved_long_deck_plan is None:
            raise ValueError(
                "long_deck_batch_artifacts requires long_deck_plan or long_deck_request."
            )
        merged_long_deck = _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "merge_long_deck",
            lambda: merge_batch_deck_irs(
                resolved_long_deck_plan,
                request.long_deck_batch_artifacts or [],
            ),
            slide_count=resolved_long_deck_plan.slide_count,
            batch_count=len(request.long_deck_batch_artifacts),
            use_deck_plan=False,
        )

    long_deck_qa_report: LongDeckQAReport | None = None
    if request.long_deck_qa_enabled:
        if resolved_long_deck_plan is None:
            raise ValueError("long_deck_qa_enabled requires long_deck_plan or long_deck_request.")
        resolved_long_deck_ir = request.long_deck_ir or merged_long_deck
        if resolved_long_deck_ir is None:
            raise ValueError("long_deck_qa_enabled requires long_deck_ir or long_deck_batch_artifacts.")
        long_deck_qa_report = _run_stage(
            stage_observer,
            started_at,
            job_timeout_seconds,
            "evaluate_long_deck_qa",
            lambda: evaluate_long_deck_consistency(
                resolved_long_deck_ir,
                resolved_long_deck_plan,
            ),
            slide_count=resolved_long_deck_plan.slide_count,
            use_deck_plan=False,
        )

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
        if generation_result.brief is not None:
            brief_path = write_model_json(
                DeckBriefArtifact(
                    brief=generation_result.brief,
                    brief_source=generation_result.brief_source,
                    brief_fallback_used=generation_result.brief_fallback_used,
                    brief_error_message=generation_result.brief_error_message,
                ),
                output_dir / "generated_deck_brief.json",
            )
            artifacts.append(_artifact("generated_deck_brief", brief_path, "json"))

        if generation_result.deck_plan is not None:
            plan_path = write_model_json(
                DeckPlanArtifact(
                    deck_plan=generation_result.deck_plan,
                    plan_source=generation_result.plan_source,
                    plan_fallback_used=generation_result.plan_fallback_used,
                    plan_error_message=generation_result.plan_error_message,
                ),
                output_dir / "generated_deck_plan.json",
            )
            artifacts.append(_artifact("generated_deck_plan", plan_path, "json"))

        if resolved_long_deck_plan is not None:
            long_plan_path = write_model_json(
                resolved_long_deck_plan,
                output_dir / "generated_long_deck_plan.json",
            )
            artifacts.append(_artifact("generated_long_deck_plan", long_plan_path, "json"))

        if batch_deck is not None and request.batch_generation_request is not None:
            batch_id = request.batch_generation_request.batch_context.batch_id
            batch_path = write_model_json(
                batch_deck,
                output_dir / f"generated_batch_{batch_id}_deck_ir.json",
            )
            artifacts.append(_artifact(f"generated_batch_{batch_id}_deck_ir", batch_path, "json"))

        if merged_long_deck is not None:
            merged_long_deck_path = write_model_json(
                merged_long_deck,
                output_dir / "generated_long_deck_ir.json",
            )
            artifacts.append(_artifact("generated_long_deck_ir", merged_long_deck_path, "json"))

        if long_deck_qa_report is not None:
            long_deck_qa_path = write_model_json(
                long_deck_qa_report,
                output_dir / "generated_long_deck_qa.json",
            )
            artifacts.append(_artifact("generated_long_deck_qa", long_deck_qa_path, "json"))

        deck_path = write_model_json(generation_result.deck, output_dir / "generated_deck_ir.json")
        artifacts.append(_artifact("generated_deck_ir", deck_path, "json"))

        patchable_elements_path = write_model_json(
            build_patchable_elements_report(generation_result.deck),
            output_dir / "patchable_elements.json",
        )
        artifacts.append(_artifact("patchable_elements", patchable_elements_path, "json"))

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
        try:
            patch = _run_stage(
                stage_observer,
                started_at,
                job_timeout_seconds,
                "apply_patch",
                lambda: load_patch(request.patch_path),
                patch_path=str(request.patch_path),
            )
        except json.JSONDecodeError as exc:
            patch_result = build_patch_failure_result(
                generation_result.deck,
                code="INVALID_PATCH_JSON",
                message=f"Patch file '{request.patch_path}' is not valid JSON: {exc}",
                input_patch_path=str(request.patch_path),
            )
        except ValidationError as exc:
            patch_result = build_patch_failure_result(
                generation_result.deck,
                code="PATCH_SCHEMA_VALIDATION_FAILED",
                message=f"Patch file '{request.patch_path}' does not match the SlidePatch schema: {exc}",
                input_patch_path=str(request.patch_path),
            )
        else:
            patch_result = _run_stage(
                stage_observer,
                started_at,
                job_timeout_seconds,
                "apply_patch",
                lambda: apply_patch(generation_result.deck, patch),
                patch_path=str(request.patch_path),
            )

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

        patch_result = patch_result.model_copy(
            update={
                "input_patch_path": str(request.patch_path),
                "output_pptx_path": str(patched_pptx_path),
            }
        )

        with _pipeline_stage(stage_observer, started_at, job_timeout_seconds, "save_artifacts"):
            patched_deck_path = write_model_json(patch_result.deck, output_dir / "patched_deck_ir.json")
            artifacts.append(_artifact("patched_deck_ir", patched_deck_path, "json"))

            patch_report_path = write_model_json(patch_result, output_dir / "patch_report.json")
            artifacts.append(_artifact("patch_report", patch_report_path, "json"))

        artifacts.append(_artifact("patched_deck", patched_pptx_path, "pptx"))

        if patch_result.issues:
            issue_label = "completed" if patch_result.applied_count > 0 else "failed"
            messages.append(f"Patch {issue_label} with {len(patch_result.issues)} issue(s). See {patch_report_path}.")
            status_code = 2

    return BuildPipelineResult(
        generation_result=generation_result,
        patch_result=patch_result,
        artifacts=artifacts,
        accepted=generation_result.accepted,
        status_code=status_code,
        messages=messages,
    )
