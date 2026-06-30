"""Batch orchestration for long-deck dry runs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import perf_counter
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
from ppt_agent.load import load_deck
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
from ppt_agent.runtime import sanitize_error_message


LongDeckRunStatus = Literal["succeeded", "partial_failed", "failed", "cancelled", "partial_cancelled"]
BatchRunStatus = Literal["succeeded", "failed"]
AttemptStatus = Literal["succeeded", "failed"]
PROVIDER_TIMEOUT_ERROR_TYPE = "provider_timeout"
PROVIDER_TIMEOUT_SUGGESTION = "Reduce batch_size to 2, wait 120 seconds, then retry."
PROVIDER_TIMEOUT_MARKERS = (
    "524",
    "timeout",
    "retryable",
    "proxy read timeout",
    "origin_response_timeout",
)
ProgressLogger = Callable[[str], None]
CancelChecker = Callable[[], bool]


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
    resume: bool = False


class BatchAttemptRecord(StrictModel):
    attempt_index: int = Field(..., ge=1)
    status: AttemptStatus
    qa_score: int | None = Field(default=None, ge=0, le=100)
    error_message: str | None = None
    error_type: str | None = None
    retryable: bool = False
    suggestion: str | None = None
    validation_error_type: str | None = None
    forbidden_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    raw_response_preview: str | None = None


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
    error_type: str | None = None
    retryable: bool = False
    suggestion: str | None = None
    validation_error_type: str | None = None
    forbidden_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    raw_response_preview: str | None = None


class LongDeckRunReport(StrictModel):
    run_id: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=21, le=100)
    batch_size: int = Field(..., ge=1)
    total_batches: int = Field(..., ge=1)
    completed_batches: list[str] = Field(default_factory=list)
    failed_batches: list[str] = Field(default_factory=list)
    cancelled_batches: list[str] = Field(default_factory=list)
    status: LongDeckRunStatus
    batch_reports: list[BatchRunReport] = Field(default_factory=list)
    merged_deck_ir_path: Path | None = None
    long_deck_qa_path: Path | None = None
    long_deck_plan_path: Path | None = None
    run_report_path: Path | None = None
    error_message: str | None = None
    error_type: str | None = None
    retryable: bool = False
    suggestion: str | None = None


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


def _status_for_cancel(completed_batches: list[str]) -> LongDeckRunStatus:
    return "partial_cancelled" if completed_batches else "cancelled"


def _classify_batch_error(error_message: str | None) -> tuple[str | None, bool, str | None]:
    normalized = (error_message or "").lower()
    if any(marker in normalized for marker in PROVIDER_TIMEOUT_MARKERS):
        return PROVIDER_TIMEOUT_ERROR_TYPE, True, PROVIDER_TIMEOUT_SUGGESTION
    return None, False, None


def _log_progress(progress_logger: ProgressLogger | None, message: str) -> None:
    if progress_logger is not None:
        progress_logger(message)


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
    cancelled_batches: list[str] | None = None,
    batch_reports: list[BatchRunReport],
    status: LongDeckRunStatus,
    merged_deck_ir_path: Path | None = None,
    long_deck_qa_path: Path | None = None,
    long_deck_plan_path: Path | None = None,
    error_message: str | None = None,
    error_type: str | None = None,
    retryable: bool = False,
    suggestion: str | None = None,
) -> LongDeckRunReport:
    report = LongDeckRunReport(
        run_id=run_id,
        slide_count=request.slide_count,
        batch_size=request.batch_size,
        total_batches=len(long_deck_plan.batches),
        completed_batches=completed_batches,
        failed_batches=failed_batches,
        cancelled_batches=cancelled_batches or [],
        status=status,
        batch_reports=batch_reports,
        merged_deck_ir_path=merged_deck_ir_path,
        long_deck_qa_path=long_deck_qa_path,
        long_deck_plan_path=long_deck_plan_path,
        error_message=error_message,
        error_type=error_type,
        retryable=retryable,
        suggestion=suggestion,
    )
    return _write_run_report(report, output_dir)


def _resume_batch_if_complete(
    *,
    output_dir: Path,
    batch: BatchPlan,
    long_deck_plan: LongDeckPlan,
) -> tuple[BatchRunReport, BatchGenerationArtifact] | None:
    status_path = _batch_file(output_dir, batch.batch_id, "status")
    deck_ir_path = _batch_file(output_dir, batch.batch_id, "deck_ir")
    if not status_path.is_file() or not deck_ir_path.is_file():
        return None

    try:
        status_report = BatchRunReport.model_validate_json(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if status_report.status != "succeeded":
        return None

    try:
        deck = load_deck(deck_ir_path)
        deck = validate_batch_deck_ir_against_batch_range(
            deck,
            get_batch_context(long_deck_plan, batch.batch_id),
        )
    except Exception:
        return None

    report = status_report.model_copy(
        update={
            "deck_ir_path": deck_ir_path,
            "qa_report_path": _batch_file(output_dir, batch.batch_id, "qa_report"),
            "attempts_path": _batch_file(output_dir, batch.batch_id, "attempts"),
            "status_path": status_path,
        }
    )
    return report, BatchGenerationArtifact(batch_id=batch.batch_id, deck_ir=deck)


def _write_batch_report(report: BatchRunReport, output_dir: Path) -> BatchRunReport:
    status_path = _batch_file(output_dir, report.batch_id, "status")
    report = report.model_copy(update={"status_path": status_path})
    write_model_json(report, status_path)
    return report


def _field_list_from_exception(exc: Exception, attribute: str) -> list[str]:
    values = getattr(exc, attribute, [])
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value).strip()]


def _validation_error_type_from_exception(exc: Exception) -> str | None:
    value = getattr(exc, "validation_error_type", None)
    return str(value) if value else None


def _raw_response_preview_from_exception(exc: Exception) -> str | None:
    value = getattr(exc, "raw_response_preview", None)
    if value is None:
        return None
    return sanitize_error_message(value)


def _suggestion_from_exception(exc: Exception) -> str | None:
    value = getattr(exc, "suggestion", None)
    if value is None:
        return None
    return sanitize_error_message(value)


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
    last_error_type: str | None = None
    last_retryable = False
    last_suggestion: str | None = None
    last_validation_error_type: str | None = None
    last_forbidden_fields: list[str] = []
    last_missing_fields: list[str] = []
    last_raw_response_preview: str | None = None

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
            last_error = sanitize_error_message(exc)
            last_error_type, last_retryable, classified_suggestion = _classify_batch_error(last_error)
            last_suggestion = classified_suggestion or _suggestion_from_exception(exc)
            last_validation_error_type = _validation_error_type_from_exception(exc)
            last_forbidden_fields = _field_list_from_exception(exc, "forbidden_fields")
            last_missing_fields = _field_list_from_exception(exc, "missing_fields")
            last_raw_response_preview = _raw_response_preview_from_exception(exc)
            attempts.append(
                BatchAttemptRecord(
                    attempt_index=attempt_index,
                    status="failed",
                    error_message=last_error,
                    error_type=last_error_type,
                    retryable=last_retryable,
                    suggestion=last_suggestion,
                    validation_error_type=last_validation_error_type,
                    forbidden_fields=last_forbidden_fields,
                    missing_fields=last_missing_fields,
                    raw_response_preview=last_raw_response_preview,
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
            error_type=last_error_type,
            retryable=last_retryable,
            suggestion=last_suggestion,
            validation_error_type=last_validation_error_type,
            forbidden_fields=last_forbidden_fields,
            missing_fields=last_missing_fields,
            raw_response_preview=last_raw_response_preview,
        ),
        output_dir,
    )
    return batch_report, None


def run_long_deck_batch_generation(
    request: LongDeckRunRequest,
    model: Any,
    *,
    progress_logger: ProgressLogger | None = None,
    cancel_checker: CancelChecker | None = None,
) -> LongDeckRunReport:
    """Run a deterministic long-deck batch generation dry run."""

    run_id = f"long_deck_{uuid4().hex[:12]}"
    output_dir = _output_dir_for_request(request, run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    _brief, long_deck_plan = _build_long_deck_plan_for_run(request)
    _log_progress(
        progress_logger,
        (
            f"Starting long deck run: {request.slide_count} slides, "
            f"batch_size={request.batch_size}, total_batches={len(long_deck_plan.batches)}"
        ),
    )
    long_deck_plan_path = write_model_json(
        long_deck_plan,
        output_dir / "generated_long_deck_plan.json",
    )

    completed_batches: list[str] = []
    failed_batches: list[str] = []
    batch_reports: list[BatchRunReport] = []
    batch_artifacts: list[BatchGenerationArtifact] = []

    for batch in long_deck_plan.batches:
        if cancel_checker is not None and cancel_checker():
            pending_batch_ids = [
                pending.batch_id
                for pending in long_deck_plan.batches
                if pending.batch_id not in {*completed_batches, *failed_batches}
            ]
            report = _final_report(
                run_id=run_id,
                request=request,
                long_deck_plan=long_deck_plan,
                output_dir=output_dir,
                completed_batches=completed_batches,
                failed_batches=failed_batches,
                cancelled_batches=pending_batch_ids,
                batch_reports=batch_reports,
                status=_status_for_cancel(completed_batches),
                long_deck_plan_path=long_deck_plan_path,
                error_message="Long deck run cancelled at a batch boundary.",
            )
            _log_progress(progress_logger, f"Long deck run {report.status}")
            return report

        if request.resume:
            resumed = _resume_batch_if_complete(
                output_dir=output_dir,
                batch=batch,
                long_deck_plan=long_deck_plan,
            )
            if resumed is not None:
                batch_report, batch_artifact = resumed
                completed_batches.append(batch.batch_id)
                batch_reports.append(batch_report)
                batch_artifacts.append(batch_artifact)
                _log_progress(progress_logger, f"Skipping {batch.batch_id} from existing succeeded artifacts")
                continue

        _log_progress(progress_logger, f"Starting {batch.batch_id} slides {batch.start_slide}-{batch.end_slide}")
        batch_started_at = perf_counter()
        batch_report, batch_artifact = _run_one_batch(
            request=request,
            model=model,
            long_deck_plan=long_deck_plan,
            batch=batch,
            output_dir=output_dir,
        )
        batch_duration = perf_counter() - batch_started_at
        batch_reports.append(batch_report)
        if batch_artifact is None:
            failed_batches.append(batch.batch_id)
            _log_progress(
                progress_logger,
                f"Failed {batch.batch_id}: error_type={batch_report.error_type or 'unknown'}",
            )
            if not request.continue_on_error:
                report = _final_report(
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
                    error_type=batch_report.error_type,
                    retryable=batch_report.retryable,
                    suggestion=batch_report.suggestion,
                )
                _log_progress(progress_logger, f"Long deck run {report.status}")
                return report
            continue

        completed_batches.append(batch.batch_id)
        batch_artifacts.append(batch_artifact)
        _log_progress(progress_logger, f"Completed {batch.batch_id} in {batch_duration:.1f}s")

    if failed_batches:
        report = _final_report(
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
            error_type=next(
                (batch_report.error_type for batch_report in batch_reports if batch_report.error_type),
                None,
            ),
            retryable=any(batch_report.retryable for batch_report in batch_reports),
            suggestion=next(
                (batch_report.suggestion for batch_report in batch_reports if batch_report.suggestion),
                None,
            ),
        )
        _log_progress(progress_logger, f"Long deck run {report.status}")
        return report

    try:
        _log_progress(progress_logger, f"Merging {len(batch_artifacts)} batches")
        merged_deck: Deck = merge_batch_deck_irs(long_deck_plan, batch_artifacts)
        merged_deck_ir_path = write_model_json(
            merged_deck,
            output_dir / "generated_long_deck_ir.json",
        )
        _log_progress(progress_logger, "Running long deck QA")
        long_deck_qa_report = evaluate_long_deck_consistency(merged_deck, long_deck_plan)
        long_deck_qa_path = write_model_json(
            long_deck_qa_report,
            output_dir / "generated_long_deck_qa.json",
        )
    except Exception as exc:
        report = _final_report(
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
        _log_progress(progress_logger, f"Long deck run {report.status}")
        return report

    report = _final_report(
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
    _log_progress(progress_logger, f"Long deck run {report.status}")
    return report
