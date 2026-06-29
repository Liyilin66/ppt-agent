import json
import time
from pathlib import Path

import pytest

from ppt_agent.generation import (
    BatchGenerationArtifact,
    DeckBrief,
    DeckGenerationRequest,
    GenerationAttempt,
    GenerationResult,
    build_batch_generation_request,
)
from ppt_agent.load import load_deck
from ppt_agent.models import Deck
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline
from ppt_agent.planning import (
    LongDeckPlanningRequest,
    build_deterministic_deck_plan,
    build_deterministic_long_deck_plan,
)
from ppt_agent.qa import analyze_deck
from ppt_agent.runtime import JobTimeoutError, LLMCallTimeoutError

import ppt_agent.pipeline as pipeline


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


class FakeStructuredModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        if len(self.responses) > 1:
            response = self.responses.pop(0)
        else:
            response = self.responses[0]
        if isinstance(response, BaseException):
            raise response
        return response


class FakeModel:
    def __init__(self, responses):
        self.structured_model = FakeStructuredModel(responses)

    def with_structured_output(self, schema):
        return self.structured_model


def _generation_result(accepted: bool) -> GenerationResult:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    qa_report = analyze_deck(deck)
    plan_brief = DeckBrief(topic="AI in Education", audience="university students", slide_count=3)
    deck_plan = build_deterministic_deck_plan(plan_brief)
    return GenerationResult(
        deck=deck,
        qa_report=qa_report,
        attempts=[
            GenerationAttempt(
                attempt_index=1,
                deck=deck,
                qa_report=qa_report,
                accepted=accepted,
            )
        ],
        accepted=accepted,
        deck_plan=deck_plan,
        plan_source="deterministic",
    )


def _request(tmp_path: Path, patch_path: Path | None = None) -> BuildPipelineRequest:
    return BuildPipelineRequest(
        generation_request=DeckGenerationRequest(
            topic="AI in Education",
            audience="university students",
            slide_count=3,
        ),
        theme_path=EXAMPLES_DIR / "theme.json",
        output_dir=tmp_path,
        min_qa_score=80,
        max_attempts=2,
        patch_path=patch_path,
    )


def _long_deck_request() -> LongDeckPlanningRequest:
    return LongDeckPlanningRequest(
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=30,
        language="zh-CN",
        purpose="技术产品分享",
        content_focus="责任边界、工作流、指标和风险治理",
        must_include=["closing section must stay actionable"],
        must_avoid=["marketing slogans"],
        user_requirements_raw="做一份 30 页长 deck 规划，按 section 和 batch 组织。",
    )


def _deck_plan_payload(slide_count: int = 3) -> dict:
    layouts = ["title_slide", "two_column", "closing_slide"]
    roles = ["cover", "context", "summary"]
    return {
        "topic": "AI Agent 产品经理",
        "audience": "IT 硕士学生",
        "slide_count": slide_count,
        "slides": [
            {
                "slide_index": index,
                "slide_role": roles[index - 1],
                "key_message": f"Message {index}",
                "content_goal": f"Goal {index}",
                "recommended_layout": layouts[index - 1],
                "content_items": 1 if index != 2 else 2,
                "must_not_repeat": [],
            }
            for index in range(1, slide_count + 1)
        ],
    }


def _artifact_names(result) -> list[str]:
    return [artifact.name for artifact in result.artifacts]


def _batch_artifact(long_plan, batch_id: str) -> BatchGenerationArtifact:
    batch = next(batch for batch in long_plan.batches if batch.batch_id == batch_id)
    template = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    slides = []
    for slide_number in range(batch.start_slide, batch.end_slide + 1):
        layout = "two_column"
        if slide_number == 1:
            layout = "title_slide"
        elif slide_number == long_plan.slide_count:
            layout = "closing_slide"
        slides.append(
            {
                "slide_id": f"slide_{slide_number:03d}",
                "title": f"Slide {slide_number}",
                "layout": layout,
                "elements": [
                    {
                        "element_id": f"s{slide_number:03d}_e01",
                        "type": "text",
                        "bbox": {"x": 0.8, "y": 0.8, "width": 7.0, "height": 0.8},
                        "text": f"Point for slide {slide_number}",
                    }
                ],
            }
        )
    return BatchGenerationArtifact(
        batch_id=batch_id,
        deck_ir=Deck.model_validate(
            {
                "deck_id": "generated_long_deck",
                "title": long_plan.topic,
                "theme_name": template.theme_name,
                "canvas_width_in": template.canvas_width_in,
                "canvas_height_in": template.canvas_height_in,
                "slides": slides,
            }
        ),
    )


def _long_deck_ir_for_qa(long_plan) -> Deck:
    slides: list[dict] = []
    for section in long_plan.sections:
        for local_index, slide_number in enumerate(range(section.start_slide, section.end_slide + 1)):
            batch = next(
                batch
                for batch in long_plan.batches
                if batch.start_slide <= slide_number <= batch.end_slide
            )
            layout = section.preferred_layouts[min(local_index, len(section.preferred_layouts) - 1)]
            if slide_number == 1:
                layout = "title_slide"
            elif slide_number == long_plan.slide_count:
                layout = "closing_slide"

            title_prefix = "接下来：" if slide_number == batch.start_slide and slide_number != 1 else ""
            body_prefix = "接下来承接上一批的判断。 " if slide_number == batch.start_slide and slide_number != 1 else ""
            text_parts = []
            if local_index < len(section.key_messages):
                text_parts.append(section.key_messages[local_index])
            else:
                text_parts.append(section.key_questions[(local_index - len(section.key_messages)) % len(section.key_questions)])
            if local_index == 0 and section.must_include:
                text_parts.extend(section.must_include)
            if slide_number == long_plan.slide_count:
                text_parts.append("下一步：列出禁止清单，设计失败回退路径，并标注人工接管点。")
            text_parts.append(f"Slide {slide_number} keeps the section on {section.title}.")

            slides.append(
                {
                    "slide_id": f"slide_{slide_number:03d}",
                    "title": f"{title_prefix}{section.title} {local_index + 1}",
                    "layout": layout,
                    "elements": [
                        {
                            "element_id": f"s{slide_number:03d}_e01",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.8, "width": 8.0, "height": 1.0},
                            "text": body_prefix + " ".join(text_parts),
                        }
                    ],
                }
            )
    return Deck.model_validate(
        {
            "deck_id": "generated_long_deck",
            "title": long_plan.topic,
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": slides,
        }
    )


def test_run_build_pipeline_accepted_outputs_generated_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))

    result = run_build_pipeline(object(), _request(tmp_path))

    assert result.accepted is True
    assert result.status_code == 0
    assert result.messages == []
    assert _artifact_names(result) == [
        "generated_deck_plan",
        "generated_deck_ir",
        "patchable_elements",
        "generated_qa_report",
        "generated_attempts",
        "generated_deck",
    ]
    assert (tmp_path / "generated_deck_plan.json").exists()
    assert (tmp_path / "generated_deck_ir.json").exists()
    assert (tmp_path / "patchable_elements.json").exists()
    assert (tmp_path / "generated_qa_report.json").exists()
    assert (tmp_path / "generated_attempts.json").exists()
    assert (tmp_path / "generated_deck.pptx").exists()


def test_run_build_pipeline_writes_brief_artifact_when_available(tmp_path: Path, monkeypatch) -> None:
    def generation_with_fallback_brief(*args, **kwargs):
        result = _generation_result(True)
        return result.model_copy(
            update={
                "brief": DeckBrief(
                    topic="AI Agent 产品经理",
                    audience="IT 硕士学生",
                    slide_count=3,
                    user_requirements_raw="不要营销材料",
                ),
                "brief_source": "fallback",
                "brief_fallback_used": True,
                "brief_error_message": "LLM call timed out in stage 'build_brief' after 120 seconds.",
            }
        )

    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", generation_with_fallback_brief)

    result = run_build_pipeline(object(), _request(tmp_path))
    brief_artifact = json.loads((tmp_path / "generated_deck_brief.json").read_text(encoding="utf-8"))

    assert result.accepted is True
    assert "generated_deck_brief" in _artifact_names(result)
    assert "generated_deck_plan" in _artifact_names(result)
    assert brief_artifact["brief_source"] == "fallback"
    assert brief_artifact["brief_fallback_used"] is True
    assert brief_artifact["brief"]["topic"] == "AI Agent 产品经理"
    assert "build_brief" in brief_artifact["brief_error_message"]


def test_run_build_pipeline_writes_optional_long_deck_plan_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))

    request = _request(tmp_path).model_copy(
        update={
            "long_deck_request": _long_deck_request(),
            "long_deck_batch_size": 10,
        }
    )

    result = run_build_pipeline(object(), request)
    long_plan_artifact = json.loads((tmp_path / "generated_long_deck_plan.json").read_text(encoding="utf-8"))

    assert result.accepted is True
    assert "generated_long_deck_plan" in _artifact_names(result)
    assert long_plan_artifact["slide_count"] == 30
    assert len(long_plan_artifact["batches"]) == 3
    assert long_plan_artifact["batches"][0]["start_slide"] == 1
    assert long_plan_artifact["batches"][0]["end_slide"] == 10
    assert long_plan_artifact["sections"][-1]["section_id"].endswith("conclusion_action")


def test_run_build_pipeline_writes_optional_batch_deck_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))
    long_plan = build_deterministic_long_deck_plan(_long_deck_request(), batch_size=10)
    batch_request = build_batch_generation_request(long_plan, "batch_02")
    monkeypatch.setattr(
        pipeline,
        "generate_batch_deck_with_model",
        lambda *args, **kwargs: load_deck(EXAMPLES_DIR / "sample_slide_ir.json"),
    )

    request = _request(tmp_path).model_copy(update={"batch_generation_request": batch_request})

    result = run_build_pipeline(object(), request)

    assert result.accepted is True
    assert "generated_batch_batch_02_deck_ir" in _artifact_names(result)
    assert (tmp_path / "generated_batch_batch_02_deck_ir.json").exists()


def test_run_build_pipeline_writes_optional_merged_long_deck_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))
    long_plan = build_deterministic_long_deck_plan(_long_deck_request(), batch_size=15)
    batch_artifacts = [
        _batch_artifact(long_plan, "batch_02"),
        _batch_artifact(long_plan, "batch_01"),
    ]

    request = _request(tmp_path).model_copy(
        update={
            "long_deck_plan": long_plan,
            "long_deck_batch_artifacts": batch_artifacts,
        }
    )

    result = run_build_pipeline(object(), request)
    merged_artifact = json.loads((tmp_path / "generated_long_deck_ir.json").read_text(encoding="utf-8"))

    assert result.accepted is True
    assert "generated_long_deck_ir" in _artifact_names(result)
    assert merged_artifact["slides"][0]["slide_id"] == "slide_001"
    assert merged_artifact["slides"][-1]["slide_id"] == "slide_030"


def test_run_build_pipeline_writes_optional_long_deck_qa_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))
    long_plan = build_deterministic_long_deck_plan(_long_deck_request(), batch_size=10)
    request = _request(tmp_path).model_copy(
        update={
            "long_deck_plan": long_plan,
            "long_deck_ir": _long_deck_ir_for_qa(long_plan),
            "long_deck_qa_enabled": True,
        }
    )

    result = run_build_pipeline(object(), request)
    qa_artifact = json.loads((tmp_path / "generated_long_deck_qa.json").read_text(encoding="utf-8"))

    assert result.accepted is True
    assert "generated_long_deck_qa" in _artifact_names(result)
    assert qa_artifact["passed"] is True
    assert qa_artifact["score"] >= 0.75


def test_run_build_pipeline_defaults_to_deterministic_brief_and_plan(tmp_path: Path) -> None:
    request = BuildPipelineRequest(
        generation_request=DeckGenerationRequest(
            topic="AI Agent 产品经理",
            audience="IT 硕士学生",
            slide_count=3,
            user_requirements="中文分享 PPT，讲技术边界和工作流设计，不要营销材料。",
        ),
        theme_path=EXAMPLES_DIR / "theme.json",
        output_dir=tmp_path,
        min_qa_score=0,
        max_attempts=1,
    )
    model = FakeModel([load_deck(EXAMPLES_DIR / "sample_slide_ir.json")])

    result = run_build_pipeline(model, request, llm_timeout_seconds=120)
    attempts = json.loads((tmp_path / "generated_attempts.json").read_text(encoding="utf-8"))
    brief_artifact = json.loads((tmp_path / "generated_deck_brief.json").read_text(encoding="utf-8"))
    plan_artifact = json.loads((tmp_path / "generated_deck_plan.json").read_text(encoding="utf-8"))

    assert result.status_code == 0
    assert result.generation_result.brief_source == "deterministic"
    assert result.generation_result.brief_fallback_used is False
    assert result.generation_result.plan_source == "deterministic"
    assert result.generation_result.plan_fallback_used is False
    assert attempts["brief_source"] == "deterministic"
    assert attempts["plan_source"] == "deterministic"
    assert brief_artifact["brief_source"] == "deterministic"
    assert plan_artifact["plan_source"] == "deterministic"
    assert "营销材料" in brief_artifact["brief"]["must_avoid"]
    assert all("Extract a DeckBrief" not in prompt for prompt in model.structured_model.prompts)
    assert all("Create a DeckPlan" not in prompt for prompt in model.structured_model.prompts)


def test_run_build_pipeline_continues_with_fallback_brief_after_timeout(tmp_path: Path) -> None:
    request = BuildPipelineRequest(
        generation_request=DeckGenerationRequest(
            topic="AI Agent 产品经理",
            audience="IT 硕士学生",
            slide_count=3,
            user_requirements="中文分享 PPT，讲技术边界和工作流设计，不要营销材料。",
            use_llm_brief=True,
        ),
        theme_path=EXAMPLES_DIR / "theme.json",
        output_dir=tmp_path,
        min_qa_score=0,
        max_attempts=1,
    )
    model = FakeModel(
        [
            LLMCallTimeoutError("LLM call timed out in stage 'build_brief' after 120 seconds."),
            load_deck(EXAMPLES_DIR / "sample_slide_ir.json"),
        ]
    )

    result = run_build_pipeline(model, request, llm_timeout_seconds=120)
    attempts = json.loads((tmp_path / "generated_attempts.json").read_text(encoding="utf-8"))
    brief_artifact = json.loads((tmp_path / "generated_deck_brief.json").read_text(encoding="utf-8"))
    plan_artifact = json.loads((tmp_path / "generated_deck_plan.json").read_text(encoding="utf-8"))

    assert result.status_code == 0
    assert result.generation_result.brief_source == "fallback"
    assert result.generation_result.brief_fallback_used is True
    assert result.generation_result.plan_source == "deterministic"
    assert attempts["brief_source"] == "fallback"
    assert attempts["brief_fallback_used"] is True
    assert attempts["plan_source"] == "deterministic"
    assert plan_artifact["plan_source"] == "deterministic"
    assert brief_artifact["brief"]["topic"] == "AI Agent 产品经理"
    assert "营销材料" in brief_artifact["brief"]["must_avoid"]


def test_run_build_pipeline_rejected_marks_failure_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(False))

    result = run_build_pipeline(object(), _request(tmp_path))

    assert result.accepted is False
    assert result.status_code == 2
    assert "did not meet the QA score gate" in result.messages[0]
    assert (tmp_path / "generated_deck_ir.json").exists()
    assert (tmp_path / "generated_deck.pptx").exists()


def test_run_build_pipeline_with_patch_outputs_patched_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))

    result = run_build_pipeline(object(), _request(tmp_path, patch_path=EXAMPLES_DIR / "sample_patch.json"))

    assert result.accepted is True
    assert result.status_code == 0
    assert result.patch_result is not None
    assert (tmp_path / "patched_deck_ir.json").exists()
    assert (tmp_path / "patch_report.json").exists()
    assert (tmp_path / "patched_deck.pptx").exists()
    patched = json.loads((tmp_path / "patched_deck_ir.json").read_text(encoding="utf-8"))
    patch_report = json.loads((tmp_path / "patch_report.json").read_text(encoding="utf-8"))
    assert patched["slides"][0]["elements"][0]["text"] == "Updated Q3 Operating Review"
    assert patch_report["accepted"] is True
    assert patch_report["success"] is True
    assert patch_report["applied_count"] == 3
    assert patch_report["issues"] == []
    assert patch_report["changed_elements"]
    assert patch_report["output_pptx_path"].endswith("patched_deck.pptx")


def test_run_build_pipeline_with_patch_issue_writes_last_legal_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))
    bad_patch = tmp_path / "bad_patch.json"
    bad_patch.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op": "update_text",
                        "slide_id": "missing_slide",
                        "element_id": "s1_title",
                        "text": "No-op",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_build_pipeline(object(), _request(tmp_path, patch_path=bad_patch))

    assert result.accepted is True
    assert result.status_code == 2
    assert result.patch_result is not None
    assert result.patch_result.issues[0].code == "SLIDE_NOT_FOUND"
    assert "Patch failed with 1 issue" in result.messages[0]
    assert (tmp_path / "patched_deck_ir.json").exists()
    assert (tmp_path / "patch_report.json").exists()
    assert (tmp_path / "patched_deck.pptx").exists()


def test_run_build_pipeline_with_invalid_patch_json_writes_patch_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))
    invalid_patch = tmp_path / "invalid_patch.json"
    invalid_patch.write_text("{not valid json}", encoding="utf-8")

    result = run_build_pipeline(object(), _request(tmp_path, patch_path=invalid_patch))
    patch_report = json.loads((tmp_path / "patch_report.json").read_text(encoding="utf-8"))

    assert result.accepted is True
    assert result.status_code == 2
    assert result.patch_result is not None
    assert patch_report["issues"][0]["code"] == "INVALID_PATCH_JSON"
    assert patch_report["accepted"] is False
    assert patch_report["success"] is False
    assert (tmp_path / "patched_deck.pptx").exists()


def test_run_build_pipeline_missing_patch_fails_before_generation(tmp_path: Path, monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("generation should not run for an invalid patch path")

    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", fail_if_called)

    with pytest.raises(ValueError, match="Patch file not found"):
        run_build_pipeline(object(), _request(tmp_path, patch_path=tmp_path / "missing_patch.json"))


def test_run_build_pipeline_rejects_non_json_patch(tmp_path: Path, monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("generation should not run for an invalid patch extension")

    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", fail_if_called)
    patch_path = tmp_path / "sample_patch.js"
    patch_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Patch file must be a"):
        run_build_pipeline(object(), _request(tmp_path, patch_path=patch_path))


def test_run_build_pipeline_timeout_guard_fails_before_generation(tmp_path: Path, monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("generation should not run after total job timeout")

    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", fail_if_called)

    with pytest.raises(JobTimeoutError, match="timed out"):
        run_build_pipeline(
            object(),
            _request(tmp_path),
            job_timeout_seconds=1,
            started_at_monotonic=time.monotonic() - 2,
        )


def test_run_build_pipeline_emits_stage_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))
    events = []

    result = run_build_pipeline(
        object(),
        _request(tmp_path),
        stage_observer=lambda stage, event, metadata: events.append((stage, event, metadata)),
    )

    assert result.accepted is True
    assert ("generate_deck", "start") in [(stage, event) for stage, event, _metadata in events]
    assert ("render_pptx", "finish") in [(stage, event) for stage, event, _metadata in events]
    assert any(metadata.get("duration_ms") is not None for _stage, event, metadata in events if event == "finish")
