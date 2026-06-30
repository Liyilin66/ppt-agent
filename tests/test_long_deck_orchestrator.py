from __future__ import annotations

import json

from ppt_agent.long_deck_orchestrator import LongDeckRunRequest, run_long_deck_batch_generation
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

    report = run_long_deck_batch_generation(_run_request(tmp_path), object())

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
    assert report.run_report_path is not None
    assert report.run_report_path.exists()
    merged = json.loads(report.merged_deck_ir_path.read_text(encoding="utf-8"))
    qa = json.loads(report.long_deck_qa_path.read_text(encoding="utf-8"))
    assert merged["slides"][0]["slide_id"] == "slide_001"
    assert merged["slides"][-1]["slide_id"] == "slide_030"
    assert "score" in qa


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

    report = run_long_deck_batch_generation(_run_request(tmp_path), object())

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
