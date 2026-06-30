"""Batch orchestration for long-deck dry runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from ppt_agent.export import write_model_json
from ppt_agent.generation import (
    BatchGenerationArtifact,
    BatchGenerationRequest,
    DeckBrief,
    build_batch_generation_request,
    build_deterministic_deck_brief,
    generate_batch_deck_with_model,
    validate_batch_deck_ir_against_batch_range,
)
from ppt_agent.long_deck import merge_batch_deck_irs
from ppt_agent.long_deck_qa import evaluate_long_deck_consistency
from ppt_agent.models import Deck, StrictModel
from ppt_agent.planning import (
    BatchPlan,
    LongDeckPlan,
    LongDeckPlanningRequest,
    build_deterministic_deck_plan,
    build_deterministic_long_deck_plan,
    get_batch_context,
)
from ppt_agent.qa import analyze_deck


LongDeckRunStatus = Literal["succeeded", "partial_failed", "failed"]
BatchRunStatus = Literal["succeeded", "failed"]
AttemptStatus = Literal["succeeded", "failed"]


class LongDeckRunRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=21, le=100)
    language: str = Field(default="zh-CN", min_length=1)
    deck_type: str = Field(..., min_length=1)
    user_requirements: str = Field(..., min_length=1)
    batch_size: int = Field(default=10, ge=1)
    max_batch_attempts: int = Field(default=1, ge=1, le=3)
    min_batch_qa_score: int | None = Field(default=None, ge=0, le=100)
    output_dir: Path | None = None
    continue_on_error: bool = False


class BatchAttemptRecord(StrictModel):
    attempt_index: int = Field(..., ge=1)
    status: AttemptStatus
    qa_score: int | None = Field(default=None, ge=0, le=100)
    error_message: str | None = None


class BatchAttemptsArtifact(StrictModel):
    batch_id: str = Field(..., min_length=1)
    attempts: list[BatchAttemptRecord] = Field(default_factory=list)


class BatchRunReport(StrictModel):
    batch_id: str = Field(..., min_length=1)
    start_slide: int = Field(..., ge=1)
    end_slide: int = Field(..., ge=1)
    status: BatchRunStatus
    deck_ir_path: Path | None = None
    qa_report_path: Path | None = None
    attempts_path: Path | None = None
    status_path: Path | None = None
    error_message: str | None = None


class LongDeckRunReport(StrictModel):
    run_id: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=21, le=100)
    batch_size: int = Field(..., ge=1)
    total_batches: int = Field(..., ge=1)
    completed_batches: list[str] = Field(default_factory=list)
    failed_batches: list[str] = Field(default_factory=list)
    status: LongDeckRunStatus
    batch_reports: list[BatchRunReport] = Field(default_factory=list)
    merged_deck_ir_path: Path | None = None
    long_deck_qa_path: Path | None = None
    long_deck_plan_path: Path | None = None
    run_report_path: Path | None = None
    error_message: str | None = None


def _output_dir_for_request(request: LongDeckRunRequest, run_id: str) -> Path:
    if request.output_dir is not None:
        return request.output_dir
    return Path("artifacts") / "long_deck_runs" / run_id


def _batch_file(output_dir: Path, batch_id: str, suffix: str) -> Path:
    return output_dir / "batches" / f"{batch_id}_{suffix}.json"


def _build_long_deck_plan_for_run(request: LongDeckRunRequest) -> tuple[DeckBrief, LongDeckPlan]:
    seed_slide_count = min(request.slide_count, 10)
    brief = build_deterministic_deck_brief(
        topic=request.topic,
        audience=request.audience,
        slide_count=seed_slide_count,
        user_requirements=request.user_requirements,
        language=request.language,
    )
    deck_plan = build_deterministic_deck_plan(brief)
    long_planning_request = LongDeckPlanningRequest(
        topic=request.topic,
        audience=request.audience,
        slide_count=request.slide_count,
        language=request.language,
        deck_type=request.deck_type,
        purpose=request.deck_type,
        content_focus=request.user_requirements,
        must_include=brief.must_include,
        must_avoid=brief.must_avoid,
        user_requirements_raw=request.user_requirements,
    )
    long_deck_plan = build_deterministic_long_deck_plan(
        long_planning_request,
        deck_plan=deck_plan,
        batch_size=request.batch_size,
    )
    return brief, long_deck_plan


def _status_for_failure(completed_batches: list[str]) -> LongDeckRunStatus:
    return "partial_failed" if completed_batches else "failed"


def _write_run_report(
    report: LongDeckRunReport,
    output_dir: Path,
) -> LongDeckRunReport:
    report_path = output_dir / "long_deck_run_report.json"
    report = report.model_copy(update={"run_report_path": report_path})
    write_model_json(report, report_path)
    return report


def _final_report(
    *,
    run_id: str,
    request: LongDeckRunRequest,
    long_deck_plan: LongDeckPlan,
    output_dir: Path,
    completed_batches: list[str],
    failed_batches: list[str],
    batch_reports: list[BatchRunReport],
    status: LongDeckRunStatus,
    merged_deck_ir_path: Path | None = None,
    long_deck_qa_path: Path | None = None,
    long_deck_plan_path: Path | None = None,
    error_message: str | None = None,
) -> LongDeckRunReport:
    report = LongDeckRunReport(
        run_id=run_id,
        slide_count=request.slide_count,
        batch_size=request.batch_size,
        total_batches=len(long_deck_plan.batches),
        completed_batches=completed_batches,
        failed_batches=failed_batches,
        status=status,
        batch_reports=batch_reports,
        merged_deck_ir_path=merged_deck_ir_path,
        long_deck_qa_path=long_deck_qa_path,
        long_deck_plan_path=long_deck_plan_path,
        error_message=error_message,
    )
    return _write_run_report(report, output_dir)


def _write_batch_report(report: BatchRunReport, output_dir: Path) -> BatchRunReport:
    status_path = _batch_file(output_dir, report.batch_id, "status")
    report = report.model_copy(update={"status_path": status_path})
    write_model_json(report, status_path)
    return report


def _run_one_batch(
    *,
    request: LongDeckRunRequest,
    model: Any,
    long_deck_plan: LongDeckPlan,
    batch: BatchPlan,
    output_dir: Path,
) -> tuple[BatchRunReport, BatchGenerationArtifact | None]:
    batch_request: BatchGenerationRequest = build_batch_generation_request(
        long_deck_plan,
        batch.batch_id,
    )
    attempts: list[BatchAttemptRecord] = []
    attempts_path = _batch_file(output_dir, batch.batch_id, "attempts")
    last_error: str | None = None

    for attempt_index in range(1, request.max_batch_attempts + 1):
        try:
            deck = generate_batch_deck_with_model(model, batch_request)
            deck = validate_batch_deck_ir_against_batch_range(
                deck,
                get_batch_context(long_deck_plan, batch.batch_id),
            )
            qa_report = analyze_deck(deck)
            if (
                request.min_batch_qa_score is not None
                and qa_report.score < request.min_batch_qa_score
            ):
                raise ValueError(
                    f"Batch '{batch.batch_id}' QA score {qa_report.score} is below "
                    f"min_batch_qa_score {request.min_batch_qa_score}."
                )

            deck_ir_path = write_model_json(
                deck,
                _batch_file(output_dir, batch.batch_id, "deck_ir"),
            )
            qa_report_path = write_model_json(
                qa_report,
                _batch_file(output_dir, batch.batch_id, "qa_report"),
            )
            attempts.append(
                BatchAttemptRecord(
                    attempt_index=attempt_index,
                    status="succeeded",
                    qa_score=qa_report.score,
                )
            )
            write_model_json(
                BatchAttemptsArtifact(batch_id=batch.batch_id, attempts=attempts),
                attempts_path,
            )
            batch_report = _write_batch_report(
                BatchRunReport(
                    batch_id=batch.batch_id,
                    start_slide=batch.start_slide,
                    end_slide=batch.end_slide,
                    status="succeeded",
                    deck_ir_path=deck_ir_path,
                    qa_report_path=qa_report_path,
                    attempts_path=attempts_path,
                ),
                output_dir,
            )
            return batch_report, BatchGenerationArtifact(batch_id=batch.batch_id, deck_ir=deck)
        except Exception as exc:
            last_error = str(exc)
            attempts.append(
                BatchAttemptRecord(
                    attempt_index=attempt_index,
                    status="failed",
                    error_message=last_error,
                )
            )

    write_model_json(
        BatchAttemptsArtifact(batch_id=batch.batch_id, attempts=attempts),
        attempts_path,
    )
    batch_report = _write_batch_report(
        BatchRunReport(
            batch_id=batch.batch_id,
            start_slide=batch.start_slide,
            end_slide=batch.end_slide,
            status="failed",
            attempts_path=attempts_path,
            error_message=last_error,
        ),
        output_dir,
    )
    return batch_report, None


def run_long_deck_batch_generation(
    request: LongDeckRunRequest,
    model: Any,
) -> LongDeckRunReport:
    """Run a deterministic long-deck batch generation dry run."""

    run_id = f"long_deck_{uuid4().hex[:12]}"
    output_dir = _output_dir_for_request(request, run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    _brief, long_deck_plan = _build_long_deck_plan_for_run(request)
    long_deck_plan_path = write_model_json(
        long_deck_plan,
        output_dir / "generated_long_deck_plan.json",
    )

    completed_batches: list[str] = []
    failed_batches: list[str] = []
    batch_reports: list[BatchRunReport] = []
    batch_artifacts: list[BatchGenerationArtifact] = []

    for batch in long_deck_plan.batches:
        batch_report, batch_artifact = _run_one_batch(
            request=request,
            model=model,
            long_deck_plan=long_deck_plan,
            batch=batch,
            output_dir=output_dir,
        )
        batch_reports.append(batch_report)
        if batch_artifact is None:
            failed_batches.append(batch.batch_id)
            if not request.continue_on_error:
                return _final_report(
                    run_id=run_id,
                    request=request,
                    long_deck_plan=long_deck_plan,
                    output_dir=output_dir,
                    completed_batches=completed_batches,
                    failed_batches=failed_batches,
                    batch_reports=batch_reports,
                    status=_status_for_failure(completed_batches),
                    long_deck_plan_path=long_deck_plan_path,
                    error_message=batch_report.error_message,
                )
            continue

        completed_batches.append(batch.batch_id)
        batch_artifacts.append(batch_artifact)

    if failed_batches:
        return _final_report(
            run_id=run_id,
            request=request,
            long_deck_plan=long_deck_plan,
            output_dir=output_dir,
            completed_batches=completed_batches,
            failed_batches=failed_batches,
            batch_reports=batch_reports,
            status=_status_for_failure(completed_batches),
            long_deck_plan_path=long_deck_plan_path,
            error_message="One or more batches failed; merge and long-deck QA were skipped.",
        )

    try:
        merged_deck: Deck = merge_batch_deck_irs(long_deck_plan, batch_artifacts)
        merged_deck_ir_path = write_model_json(
            merged_deck,
            output_dir / "generated_long_deck_ir.json",
        )
        long_deck_qa_report = evaluate_long_deck_consistency(merged_deck, long_deck_plan)
        long_deck_qa_path = write_model_json(
            long_deck_qa_report,
            output_dir / "generated_long_deck_qa.json",
        )
    except Exception as exc:
        return _final_report(
            run_id=run_id,
            request=request,
            long_deck_plan=long_deck_plan,
            output_dir=output_dir,
            completed_batches=completed_batches,
            failed_batches=["merge_or_qa"],
            batch_reports=batch_reports,
            status="partial_failed",
            long_deck_plan_path=long_deck_plan_path,
            error_message=str(exc),
        )

    return _final_report(
        run_id=run_id,
        request=request,
        long_deck_plan=long_deck_plan,
        output_dir=output_dir,
        completed_batches=completed_batches,
        failed_batches=[],
        batch_reports=batch_reports,
        status="succeeded",
        merged_deck_ir_path=merged_deck_ir_path,
        long_deck_qa_path=long_deck_qa_path,
        long_deck_plan_path=long_deck_plan_path,
    )
