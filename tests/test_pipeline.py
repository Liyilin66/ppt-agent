import json
import time
from pathlib import Path

import pytest

from ppt_agent.generation import DeckGenerationRequest, GenerationAttempt, GenerationResult
from ppt_agent.load import load_deck
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline
from ppt_agent.qa import analyze_deck
from ppt_agent.runtime import JobTimeoutError

import ppt_agent.pipeline as pipeline


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _generation_result(accepted: bool) -> GenerationResult:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    qa_report = analyze_deck(deck)
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


def _artifact_names(result) -> list[str]:
    return [artifact.name for artifact in result.artifacts]


def test_run_build_pipeline_accepted_outputs_generated_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "generate_deck_with_quality_gate", lambda *args, **kwargs: _generation_result(True))

    result = run_build_pipeline(object(), _request(tmp_path))

    assert result.accepted is True
    assert result.status_code == 0
    assert result.messages == []
    assert _artifact_names(result) == [
        "generated_deck_ir",
        "generated_qa_report",
        "generated_attempts",
        "generated_deck",
    ]
    assert (tmp_path / "generated_deck_ir.json").exists()
    assert (tmp_path / "generated_qa_report.json").exists()
    assert (tmp_path / "generated_attempts.json").exists()
    assert (tmp_path / "generated_deck.pptx").exists()


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
