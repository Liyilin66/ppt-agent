from __future__ import annotations

import json

from ppt_agent.generation import BatchDeckSchemaValidationError
from ppt_agent.long_deck_orchestrator import LongDeckRunRequest, run_long_deck_batch_generation
from ppt_agent.long_deck_quality import LongDeckQualityGateReport
from ppt_agent.models import Deck

import ppt_agent.long_deck_orchestrator as orchestrator


def _run_request(tmp_path) -> LongDeckRunRequest:
    return LongDeckRunRequest(
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=30,
        language="zh-CN",
        deck_type="technical_product_share",
        user_requirements="讲责任边界、技术边界、工作流、指标和风险治理，不要营销口号。",
        batch_size=10,
        output_dir=tmp_path,
    )


def _batch_deck(batch_request, *, slide_count_override: int | None = None) -> Deck:
    batch = batch_request.batch_context
    long_plan = batch_request.long_deck_plan
    requested_count = batch.end_slide - batch.start_slide + 1
    slide_count = requested_count if slide_count_override is None else slide_count_override
    slides = []
    for offset in range(slide_count):
        slide_number = batch.start_slide + offset
        layout = "two_column"
        if slide_number == 1:
            layout = "title_slide"
        elif slide_number == long_plan.slide_count:
            layout = "closing_slide"
        title_prefix = "接下来：" if slide_number == batch.start_slide and slide_number != 1 else ""
        slides.append(
            {
                "slide_id": f"slide_{slide_number:03d}",
                "title": f"{title_prefix}{batch.batch_id} Slide {slide_number}",
                "layout": layout,
                "elements": [
                    {
                        "element_id": f"s{slide_number:03d}_e01",
                        "type": "text",
                        "bbox": {"x": 0.8, "y": 0.8, "width": 8.0, "height": 1.0},
                        "text": (
                            f"接下来 {batch.batch_id} covers slide {slide_number} with a unique "
                            "product judgment, workflow control point, metric, or risk action."
                        ),
                    }
                ],
            }
        )

    return Deck.model_validate(
        {
            "deck_id": f"{batch.batch_id}_deck",
            "title": batch_request.topic,
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": slides,
        }
    )


def test_run_long_deck_batch_generation_writes_three_batch_reports_in_order(
    tmp_path,
    monkeypatch,
) -> None:
    seen_batches: list[str] = []

    def fake_generate(_model, batch_request):
        seen_batches.append(batch_request.batch_context.batch_id)
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)
    messages: list[str] = []

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        progress_logger=messages.append,
    )

    assert report.status == "succeeded"
    assert report.total_batches == 3
    assert [batch.batch_id for batch in report.batch_reports] == ["batch_01", "batch_02", "batch_03"]
    assert seen_batches == ["batch_01", "batch_02", "batch_03"]
    assert report.completed_batches == ["batch_01", "batch_02", "batch_03"]
    assert report.failed_batches == []
    assert all(batch_report.deck_ir_path and batch_report.deck_ir_path.exists() for batch_report in report.batch_reports)
    assert all(batch_report.qa_report_path and batch_report.qa_report_path.exists() for batch_report in report.batch_reports)
    assert all(batch_report.attempts_path and batch_report.attempts_path.exists() for batch_report in report.batch_reports)
    assert all(batch_report.status_path and batch_report.status_path.exists() for batch_report in report.batch_reports)


def test_run_long_deck_batch_generation_writes_merge_and_qa_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "generate_batch_deck_with_model",
        lambda _model, batch_request: _batch_deck(batch_request),
    )

    report = run_long_deck_batch_generation(_run_request(tmp_path), object())

    assert report.status == "succeeded"
    assert report.merged_deck_ir_path is not None
    assert report.merged_deck_ir_path.exists()
    assert report.long_deck_qa_path is not None
    assert report.long_deck_qa_path.exists()
    assert report.long_deck_quality_gate_path is not None
    assert report.long_deck_quality_gate_path.exists()
    assert report.run_report_path is not None
    assert report.run_report_path.exists()
    merged = json.loads(report.merged_deck_ir_path.read_text(encoding="utf-8"))
    qa = json.loads(report.long_deck_qa_path.read_text(encoding="utf-8"))
    quality_gate = json.loads(report.long_deck_quality_gate_path.read_text(encoding="utf-8"))
    assert merged["slides"][0]["slide_id"] == "slide_001"
    assert merged["slides"][-1]["slide_id"] == "slide_030"
    assert "score" in qa
    assert quality_gate["status"] == "passed"


def test_run_long_deck_batch_generation_emits_progress_logs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "generate_batch_deck_with_model",
        lambda _model, batch_request: _batch_deck(batch_request),
    )
    messages: list[str] = []

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        progress_logger=messages.append,
    )

    assert report.status == "succeeded"
    assert any("Starting long deck run: 30 slides, batch_size=10, total_batches=3" in message for message in messages)
    assert any("Starting batch_01 slides 1-10" in message for message in messages)
    assert any(message.startswith("Completed batch_01 in ") for message in messages)
    assert "Merging 3 batches" in messages
    assert "Running long deck QA" in messages
    assert "Running long deck hard quality gate" in messages
    assert "Long deck run succeeded" in messages


def test_run_long_deck_batch_generation_report_is_serializable(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "generate_batch_deck_with_model",
        lambda _model, batch_request: _batch_deck(batch_request),
    )

    report = run_long_deck_batch_generation(_run_request(tmp_path), object())
    payload = json.loads(report.model_dump_json())
    file_payload = json.loads(report.run_report_path.read_text(encoding="utf-8"))

    assert payload["run_id"] == report.run_id
    assert file_payload["run_id"] == report.run_id
    assert file_payload["status"] == "succeeded"


def test_run_long_deck_batch_generation_preserves_successful_artifacts_after_failure(
    tmp_path,
    monkeypatch,
) -> None:
    seen_batches: list[str] = []

    def fake_generate(_model, batch_request):
        batch_id = batch_request.batch_context.batch_id
        seen_batches.append(batch_id)
        if batch_id == "batch_02":
            return _batch_deck(batch_request, slide_count_override=1)
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)
    messages: list[str] = []

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        progress_logger=messages.append,
    )

    assert report.status == "partial_failed"
    assert report.completed_batches == ["batch_01"]
    assert report.failed_batches == ["batch_02"]
    assert seen_batches == ["batch_01", "batch_02"]
    assert len(report.batch_reports) == 2
    assert report.batch_reports[0].deck_ir_path is not None
    assert report.batch_reports[0].deck_ir_path.exists()
    assert report.batch_reports[0].qa_report_path is not None
    assert report.batch_reports[0].qa_report_path.exists()
    assert report.batch_reports[1].status == "failed"
    assert report.batch_reports[1].attempts_path is not None
    assert report.batch_reports[1].attempts_path.exists()
    assert report.batch_reports[1].status_path is not None
    assert report.batch_reports[1].status_path.exists()
    assert report.merged_deck_ir_path is None
    assert report.long_deck_qa_path is None


def test_run_long_deck_batch_generation_fails_quality_gate_before_render(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "generate_batch_deck_with_model",
        lambda _model, batch_request: _batch_deck(batch_request),
    )
    monkeypatch.setattr(
        orchestrator,
        "evaluate_long_deck_quality_gate",
        lambda _deck: LongDeckQualityGateReport(
            status="failed_quality_gate",
            score=68,
            issues=[],
            blocked_codes=["instruction_leakage"],
            blocked_slide_ids=["slide_021"],
            blocked_element_ids=["s021_e01"],
            message="Long-deck hard quality gate failed because audience-visible instruction leakage or matrix placeholder content was detected.",
        ),
    )
    messages: list[str] = []

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        progress_logger=messages.append,
    )

    assert report.status == "failed_quality_gate"
    assert report.merged_deck_ir_path is not None
    assert report.merged_deck_ir_path.exists()
    assert report.long_deck_qa_path is not None
    assert report.long_deck_qa_path.exists()
    assert report.long_deck_quality_gate_path is not None
    assert report.long_deck_quality_gate_path.exists()
    assert report.error_type == "failed_quality_gate"
    assert report.suggestion == "Fix instruction leakage or placeholder matrix content before rendering PPTX."
    assert "Running long deck hard quality gate" in messages
    assert "Long deck run failed_quality_gate" in messages


def test_run_long_deck_batch_generation_resume_skips_succeeded_batch(
    tmp_path,
    monkeypatch,
) -> None:
    first_seen_batches: list[str] = []

    def first_fake_generate(_model, batch_request):
        batch_id = batch_request.batch_context.batch_id
        first_seen_batches.append(batch_id)
        if batch_id == "batch_02":
            raise RuntimeError("provider failed")
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", first_fake_generate)
    first_report = run_long_deck_batch_generation(_run_request(tmp_path), object())

    assert first_report.status == "partial_failed"
    assert first_report.completed_batches == ["batch_01"]
    assert first_seen_batches == ["batch_01", "batch_02"]

    resumed_seen_batches: list[str] = []

    def resumed_fake_generate(_model, batch_request):
        resumed_seen_batches.append(batch_request.batch_context.batch_id)
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", resumed_fake_generate)
    resumed_report = run_long_deck_batch_generation(
        _run_request(tmp_path).model_copy(update={"resume": True}),
        object(),
    )

    assert resumed_report.status == "succeeded"
    assert resumed_seen_batches == ["batch_02", "batch_03"]
    assert resumed_report.completed_batches == ["batch_01", "batch_02", "batch_03"]
    assert resumed_report.merged_deck_ir_path is not None
    assert resumed_report.merged_deck_ir_path.exists()
    assert resumed_report.long_deck_qa_path is not None
    assert resumed_report.long_deck_qa_path.exists()


def test_run_long_deck_batch_generation_resume_regenerates_missing_deck_ir(
    tmp_path,
    monkeypatch,
) -> None:
    def first_fake_generate(_model, batch_request):
        if batch_request.batch_context.batch_id == "batch_02":
            raise RuntimeError("provider failed")
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", first_fake_generate)
    first_report = run_long_deck_batch_generation(_run_request(tmp_path), object())
    assert first_report.completed_batches == ["batch_01"]
    first_report.batch_reports[0].deck_ir_path.unlink()

    resumed_seen_batches: list[str] = []

    def resumed_fake_generate(_model, batch_request):
        resumed_seen_batches.append(batch_request.batch_context.batch_id)
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", resumed_fake_generate)
    resumed_report = run_long_deck_batch_generation(
        _run_request(tmp_path).model_copy(update={"resume": True}),
        object(),
    )

    assert resumed_report.status == "succeeded"
    assert resumed_seen_batches == ["batch_01", "batch_02", "batch_03"]


def test_run_long_deck_batch_generation_cancel_stops_at_batch_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    seen_batches: list[str] = []

    def fake_generate(_model, batch_request):
        seen_batches.append(batch_request.batch_context.batch_id)
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)

    def cancel_after_first_batch() -> bool:
        return len(seen_batches) >= 1

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        cancel_checker=cancel_after_first_batch,
    )

    assert report.status == "partial_cancelled"
    assert report.completed_batches == ["batch_01"]
    assert report.cancelled_batches == ["batch_02", "batch_03"]
    assert seen_batches == ["batch_01"]
    assert report.batch_reports[0].deck_ir_path is not None
    assert report.batch_reports[0].deck_ir_path.exists()
    assert report.merged_deck_ir_path is None
    assert report.long_deck_qa_path is None


def test_run_long_deck_batch_generation_cancel_before_first_batch(
    tmp_path,
    monkeypatch,
) -> None:
    seen_batches: list[str] = []

    def fake_generate(_model, batch_request):
        seen_batches.append(batch_request.batch_context.batch_id)
        return _batch_deck(batch_request)

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)
    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        cancel_checker=lambda: True,
    )

    assert report.status == "cancelled"
    assert report.completed_batches == []
    assert report.cancelled_batches == ["batch_01", "batch_02", "batch_03"]
    assert seen_batches == []


def test_run_long_deck_batch_generation_records_schema_validation_failure_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    def fake_generate(_model, batch_request):
        raise BatchDeckSchemaValidationError(
            "Batch failed Deck schema validation with api_key=[redacted]",
            forbidden_fields=["batch_id", "slides.0.slide_number"],
            missing_fields=["deck_id", "slides.0.elements"],
            raw_response_preview='{"api_key":"sk-testsecret123","batch_id":"batch_01"}',
        )

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)
    messages: list[str] = []

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        progress_logger=messages.append,
    )

    assert report.status == "failed"
    assert report.failed_batches == ["batch_01"]
    batch_report = report.batch_reports[0]
    assert batch_report.validation_error_type == "deck_schema_validation_failed"
    assert batch_report.forbidden_fields == ["batch_id", "slides.0.slide_number"]
    assert batch_report.missing_fields == ["deck_id", "slides.0.elements"]
    assert batch_report.raw_response_preview is not None
    assert "sk-testsecret123" not in batch_report.raw_response_preview
    assert "[redacted]" in batch_report.raw_response_preview

    attempts = json.loads(batch_report.attempts_path.read_text(encoding="utf-8"))
    status = json.loads(batch_report.status_path.read_text(encoding="utf-8"))
    attempt = attempts["attempts"][0]
    assert attempt["validation_error_type"] == "deck_schema_validation_failed"
    assert attempt["forbidden_fields"] == ["batch_id", "slides.0.slide_number"]
    assert attempt["missing_fields"] == ["deck_id", "slides.0.elements"]
    assert "sk-testsecret123" not in attempt["raw_response_preview"]
    assert status["validation_error_type"] == "deck_schema_validation_failed"


def test_run_long_deck_batch_generation_records_shape_schema_validation_failure_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    suggestion = (
        "Do not output decorative shape elements in batch Deck IR. "
        "Use text elements and layout templates instead."
    )

    def fake_generate(_model, batch_request):
        raise BatchDeckSchemaValidationError(
            "Batch failed Deck schema validation: shape.shape Field required",
            validation_error_type="shape_schema_validation_failed",
            forbidden_fields=[
                "slides.0.elements.1.shape.shape_type",
                "slides.0.elements.1.shape.fill_color",
                "slides.0.elements.1.shape.line_color",
            ],
            missing_fields=["slides.0.elements.1.shape.shape"],
            suggestion=suggestion,
            raw_response_preview='{"type":"shape","shape_type":"rounded_rect","fill_color":"#EEF9F8"}',
        )

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)

    report = run_long_deck_batch_generation(_run_request(tmp_path), object())

    assert report.status == "failed"
    batch_report = report.batch_reports[0]
    assert batch_report.validation_error_type == "shape_schema_validation_failed"
    assert batch_report.suggestion == suggestion
    assert batch_report.retryable is False

    attempts = json.loads(batch_report.attempts_path.read_text(encoding="utf-8"))
    status = json.loads(batch_report.status_path.read_text(encoding="utf-8"))
    attempt = attempts["attempts"][0]
    assert attempt["validation_error_type"] == "shape_schema_validation_failed"
    assert attempt["suggestion"] == suggestion
    assert "shape_type" in attempt["forbidden_fields"][0]
    assert status["validation_error_type"] == "shape_schema_validation_failed"
    assert status["suggestion"] == suggestion


def test_run_long_deck_batch_generation_classifies_provider_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    def fake_generate(_model, batch_request):
        raise RuntimeError(
            "Error code: 524 origin_response_timeout Proxy Read Timeout "
            "retryable: true retry_after: 120"
        )

    monkeypatch.setattr(orchestrator, "generate_batch_deck_with_model", fake_generate)
    messages: list[str] = []

    report = run_long_deck_batch_generation(
        _run_request(tmp_path),
        object(),
        progress_logger=messages.append,
    )

    assert report.status == "failed"
    assert report.error_type == "provider_timeout"
    assert report.retryable is True
    assert report.suggestion == "Reduce batch_size to 2, wait 120 seconds, then retry."
    assert "Error code: 524" in report.error_message
    assert "Failed batch_01: error_type=provider_timeout" in messages
    assert "Long deck run failed" in messages

    batch_report = report.batch_reports[0]
    assert batch_report.error_type == "provider_timeout"
    assert batch_report.retryable is True
    assert batch_report.suggestion == report.suggestion
    assert "origin_response_timeout" in batch_report.error_message

    attempts = json.loads(batch_report.attempts_path.read_text(encoding="utf-8"))
    status = json.loads(batch_report.status_path.read_text(encoding="utf-8"))
    run_report = json.loads(report.run_report_path.read_text(encoding="utf-8"))
    attempt = attempts["attempts"][0]
    assert attempt["error_type"] == "provider_timeout"
    assert attempt["retryable"] is True
    assert attempt["suggestion"] == report.suggestion
    assert status["error_type"] == "provider_timeout"
    assert run_report["error_type"] == "provider_timeout"
    assert run_report["retryable"] is True
