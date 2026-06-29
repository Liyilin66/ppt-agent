import json
import time
from pathlib import Path

import pytest

from ppt_agent.generation import DeckBrief, DeckGenerationRequest, GenerationAttempt, GenerationResult
from ppt_agent.load import load_deck
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline
from ppt_agent.planning import build_deterministic_deck_plan
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


def test_run_build_pipeline_accepted_outputs_generated_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))

    result = run_build_pipeline(object(), _request(tmp_path))

    assert result.accepted is True
    assert result.status_code == 0
    assert result.messages == []
    assert _artifact_names(result) == [
        "generated_deck_plan",
        "generated_deck_ir",
        "generated_qa_report",
        "generated_attempts",
        "generated_deck",
    ]
    assert (tmp_path / "generated_deck_plan.json").exists()
    assert (tmp_path / "generated_deck_ir.json").exists()
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
    assert (tmp_path / "patch_result.json").exists()
    assert (tmp_path / "patched_deck.pptx").exists()
    patched = json.loads((tmp_path / "patched_deck_ir.json").read_text(encoding="utf-8"))
    assert patched["slides"][0]["elements"][0]["text"] == "Updated Q3 Operating Review"


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

    assert result.accepted is False
    assert result.status_code == 2
    assert result.patch_result is not None
    assert result.patch_result.issues[0].code == "SLIDE_NOT_FOUND"
    assert "Patch completed with 1 issue" in result.messages[0]
    assert (tmp_path / "patched_deck_ir.json").exists()
    assert (tmp_path / "patch_result.json").exists()
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
