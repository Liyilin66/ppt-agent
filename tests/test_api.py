import json
from pathlib import Path

from fastapi.testclient import TestClient

import ppt_agent.api as api
from ppt_agent.generation import GenerationAttempt, GenerationResult
from ppt_agent.job_store import JobStore
from ppt_agent.load import load_deck, load_patch
from ppt_agent.long_deck_orchestrator import BatchRunReport, LongDeckRunReport
from ppt_agent.long_deck_render import LongDeckRenderReport
from ppt_agent.patch import build_patchable_elements_report
from ppt_agent.pipeline import BuildArtifact, BuildPipelineResult
from ppt_agent.qa import analyze_deck
from ppt_agent.patch import apply_patch


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _client(tmp_path: Path) -> TestClient:
    return TestClient(api.create_app(data_dir=tmp_path))


def _job_payload() -> dict:
    return {
        "topic": "AI in Education",
        "audience": "university students",
        "slides": 3,
        "theme_path": str(EXAMPLES_DIR / "theme.json"),
        "min_qa_score": 80,
        "max_attempts": 2,
    }


def _long_deck_payload() -> dict:
    return {
        "topic": "AI 产品经理如何设计 Agent 产品",
        "audience": "准备进入 AI 产品岗位的 IT 硕士学生",
        "slide_count": 30,
        "language": "zh-CN",
        "deck_type": "technical_product_share",
        "user_requirements": "讲技术边界、用户需求分析、工作流设计、评估指标和落地风险。",
        "batch_size": 2,
        "max_batch_attempts": 1,
    }


def _generation_result(accepted: bool = True) -> GenerationResult:
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


def _fake_pipeline_result(output_dir: Path, accepted: bool = True) -> BuildPipelineResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    generation_result = _generation_result(accepted=accepted)

    deck_path = output_dir / "generated_deck_ir.json"
    patchable_elements_path = output_dir / "patchable_elements.json"
    qa_path = output_dir / "generated_qa_report.json"
    attempts_path = output_dir / "generated_attempts.json"
    pptx_path = output_dir / "generated_deck.pptx"

    deck_path.write_text(generation_result.deck.model_dump_json(indent=2), encoding="utf-8")
    patchable_elements_path.write_text(
        build_patchable_elements_report(generation_result.deck).model_dump_json(indent=2),
        encoding="utf-8",
    )
    qa_path.write_text(generation_result.qa_report.model_dump_json(indent=2), encoding="utf-8")
    attempts_path.write_text(generation_result.model_dump_json(indent=2), encoding="utf-8")
    pptx_path.write_bytes(b"fake pptx")

    return BuildPipelineResult(
        generation_result=generation_result,
        artifacts=[
            BuildArtifact(name="generated_deck_ir", kind="json", path=deck_path),
            BuildArtifact(name="patchable_elements", kind="json", path=patchable_elements_path),
            BuildArtifact(name="generated_qa_report", kind="json", path=qa_path),
            BuildArtifact(name="generated_attempts", kind="json", path=attempts_path),
            BuildArtifact(name="generated_deck", kind="pptx", path=pptx_path),
        ],
        accepted=accepted,
        status_code=0 if accepted else 2,
        messages=[] if accepted else ["Generated Deck IR did not meet the QA score gate."],
    )


def _fake_pipeline_result_with_patch(output_dir: Path, accepted: bool = True) -> BuildPipelineResult:
    result = _fake_pipeline_result(output_dir, accepted=accepted)
    deck = result.generation_result.deck
    patch = load_patch(EXAMPLES_DIR / "sample_patch.json")
    patch_result = apply_patch(deck, patch)

    patched_deck_path = output_dir / "patched_deck_ir.json"
    patch_report_path = output_dir / "patch_report.json"
    patched_pptx_path = output_dir / "patched_deck.pptx"

    patched_deck_path.write_text(patch_result.deck.model_dump_json(indent=2), encoding="utf-8")
    patch_report_path.write_text(
        patch_result.model_copy(
            update={
                "input_patch_path": str(EXAMPLES_DIR / "sample_patch.json"),
                "output_pptx_path": str(patched_pptx_path),
            }
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    patched_pptx_path.write_bytes(b"fake patched pptx")

    return result.model_copy(
        update={
            "patch_result": patch_result,
            "artifacts": [
                *result.artifacts,
                BuildArtifact(name="patched_deck_ir", kind="json", path=patched_deck_path),
                BuildArtifact(name="patch_report", kind="json", path=patch_report_path),
                BuildArtifact(name="patched_deck", kind="pptx", path=patched_pptx_path),
            ],
        }
    )


def _install_fake_backend(monkeypatch, accepted: bool = True) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_build_pipeline(model, request, **kwargs):
        return _fake_pipeline_result(request.output_dir, accepted=accepted)

    monkeypatch.setattr(api, "run_build_pipeline", fake_run_build_pipeline)


def _fake_long_deck_run_report(request) -> LongDeckRunReport:
    output_dir = request.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = output_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    plan_path = output_dir / "generated_long_deck_plan.json"
    merged_path = output_dir / "generated_long_deck_ir.json"
    qa_path = output_dir / "generated_long_deck_qa.json"
    run_report_path = output_dir / "long_deck_run_report.json"
    batch_status_path = batch_dir / "batch_01_status.json"
    batch_deck_path = batch_dir / "batch_01_deck_ir.json"
    batch_qa_path = batch_dir / "batch_01_qa_report.json"
    batch_attempts_path = batch_dir / "batch_01_attempts.json"

    plan_path.write_text('{"sections":[],"batches":[]}\n', encoding="utf-8")
    merged_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    qa_path.write_text('{"score":0.82,"passed":true}\n', encoding="utf-8")
    batch_deck_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    batch_qa_path.write_text('{"score":82}\n', encoding="utf-8")
    batch_attempts_path.write_text('{"attempts":[]}\n', encoding="utf-8")
    batch_status_path.write_text('{"status":"succeeded"}\n', encoding="utf-8")

    batch_report = BatchRunReport(
        batch_id="batch_01",
        start_slide=1,
        end_slide=request.batch_size,
        status="succeeded",
        deck_ir_path=batch_deck_path,
        qa_report_path=batch_qa_path,
        attempts_path=batch_attempts_path,
        status_path=batch_status_path,
    )
    report = LongDeckRunReport(
        run_id="test-long-deck-run",
        slide_count=request.slide_count,
        batch_size=request.batch_size,
        total_batches=15,
        completed_batches=["batch_01"],
        failed_batches=[],
        status="succeeded",
        batch_reports=[batch_report],
        merged_deck_ir_path=merged_path,
        long_deck_qa_path=qa_path,
        long_deck_plan_path=plan_path,
        run_report_path=run_report_path,
    )
    run_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _install_fake_long_deck_backend(monkeypatch, captured: dict | None = None) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_long_deck_batch_generation(request, model, *, progress_logger=None):
        if captured is not None:
            captured["request"] = request
            captured["model"] = model
        if progress_logger is not None:
            progress_logger("Starting long deck run: 30 slides, batch_size=2, total_batches=15")
            progress_logger("Starting batch_01 slides 1-2")
            progress_logger("Merging 15 batches")
            progress_logger("Running long deck QA")
            progress_logger("Long deck run succeeded")
        return _fake_long_deck_run_report(request)

    def fake_render_long_deck_ir_to_pptx(
        input_deck_ir_path,
        output_pptx_path,
        report_path,
        *,
        theme_path,
        assets_dir=None,
    ):
        if captured is not None:
            captured["render_input"] = input_deck_ir_path
            captured["render_output"] = output_pptx_path
        Path(output_pptx_path).write_bytes(b"fake long deck pptx")
        report = LongDeckRenderReport(
            status="succeeded",
            input_deck_ir_path=Path(input_deck_ir_path),
            output_pptx_path=Path(output_pptx_path),
            slide_count=30,
            generated_at="2026-06-30T00:00:00+00:00",
        )
        Path(report_path).write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report

    monkeypatch.setattr(api, "run_long_deck_batch_generation", fake_run_long_deck_batch_generation)
    monkeypatch.setattr(api, "render_long_deck_ir_to_pptx", fake_render_long_deck_ir_to_pptx)


def test_health_endpoint(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_page_returns_html_with_generate_button(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert ">生成 PPT</button>" in response.text


def test_index_page_contains_chinese_job_labels(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    for text in [
        "演示主题",
        "目标观众",
        "页数",
        "详细要求",
        "最低 QA 分数",
        "最大尝试次数",
        "Patch 文件路径",
        "任务状态",
        "生成文件",
    ]:
        assert text in response.text


def test_index_page_contains_long_deck_experimental_entry(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    for text in [
        "长 PPT实验模式",
        "当前支持 30 页",
        "mini-batch generation",
        "默认 batch_size=2",
        "耗时较长",
        "experimental",
        "生成 30 页长 PPT",
    ]:
        assert text in response.text
    assert 'id="longDeckForm"' in response.text
    assert 'id="long_slide_count" name="slide_count" type="number" min="30" max="30" value="30"' in response.text


def test_index_page_keeps_required_job_field_names(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    for field_name in [
        "topic",
        "audience",
        "slides",
        "min_qa_score",
        "max_attempts",
        "patch_path",
        "user_requirements",
    ]:
        assert f'name="{field_name}"' in response.text


def test_index_page_contains_progress_stage_fields(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert "currentStage" in response.text
    assert "elapsedSeconds" in response.text
    assert "generate_deck_chunk_" in response.text
    assert "正在快速解析需求" in response.text
    assert "正在快速规划大纲" in response.text
    assert "正在生成 Deck：第" in response.text
    assert "已生成，但未通过 QA" in response.text
    assert "已生成，但 Patch 需要修正" in response.text
    assert "已生成，但 QA 和 Patch 仍需修正" in response.text
    assert "任务运行时间较长，请检查后端日志" in response.text
    assert "generating_batch_" in response.text
    assert "正在生成长 PPT：batch" in response.text
    assert "正在渲染长 PPT PPTX" in response.text


def test_index_page_patch_path_is_optional_json_placeholder(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert 'id="patch_path" name="patch_path"' in response.text
    assert 'name="patch_path" placeholder="可选：examples/sample_patch.json"' in response.text
    assert 'name="patch_path" value=' not in response.text
    assert "sample_patch.js\"" not in response.text
    assert "sample_patch.js<" not in response.text


def test_create_job_without_api_key_returns_clear_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = _client(tmp_path).post("/api/jobs", json=_job_payload())

    assert response.status_code == 503
    assert "OPENAI_API_KEY is not set" in response.json()["detail"]


def test_create_long_deck_job_without_api_key_returns_clear_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = _client(tmp_path).post("/api/long-deck-jobs", json=_long_deck_payload())

    assert response.status_code == 503
    assert "OPENAI_API_KEY is not set" in response.json()["detail"]


def test_create_job_success_returns_job_id(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch)

    response = _client(tmp_path).post("/api/jobs", json=_job_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["status"] == "pending"


def test_create_long_deck_job_rejects_non_30_slide_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    response = _client(tmp_path).post(
        "/api/long-deck-jobs",
        json={**_long_deck_payload(), "slide_count": 50},
    )

    assert response.status_code == 422


def test_create_long_deck_job_defaults_batch_size_to_two(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    _install_fake_long_deck_backend(monkeypatch, captured)
    payload = _long_deck_payload()
    payload.pop("batch_size")

    response = _client(tmp_path).post("/api/long-deck-jobs", json=payload)

    assert response.status_code == 202
    assert captured["request"].batch_size == 2
    assert captured["request"].slide_count == 30


def test_create_job_accepts_user_requirements(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())
    captured_request = {}

    def fake_run_build_pipeline(model, request, **kwargs):
        captured_request["request"] = request
        return _fake_pipeline_result(request.output_dir, accepted=True)

    monkeypatch.setattr(api, "run_build_pipeline", fake_run_build_pipeline)
    payload = {
        **_job_payload(),
        "user_requirements": "做一份中文课堂展示，提醒学术诚信风险。",
    }

    response = _client(tmp_path).post("/api/jobs", json=payload)

    assert response.status_code == 202
    generation_request = captured_request["request"].generation_request
    assert generation_request.language == "zh-CN"
    assert generation_request.user_requirements == "做一份中文课堂展示，提醒学术诚信风险。"
    assert generation_request.use_llm_brief is False
    assert generation_request.use_llm_plan is False


def test_long_deck_job_writes_ir_pptx_and_registers_artifacts(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    _install_fake_long_deck_backend(monkeypatch, captured)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]
    artifact_names = {artifact["name"] for artifact in artifacts}

    assert body["status"] == "succeeded"
    assert body["accepted"] is True
    assert body["qa_score"] == 82
    assert body["current_stage"] == "completed"
    assert captured["render_output"].name == "generated_long_deck.pptx"
    assert {
        "generated_long_deck_plan",
        "generated_long_deck_ir",
        "generated_long_deck_qa",
        "generated_long_deck",
        "long_deck_run_report",
        "long_deck_render_report",
        "batch_01_status",
        "batch_01_deck_ir",
        "batch_01_qa_report",
        "batch_01_attempts",
    } <= artifact_names

    deck_ir_artifact = next(artifact for artifact in artifacts if artifact["name"] == "generated_long_deck_ir")
    assert client.get(deck_ir_artifact["download_url"]).status_code == 200


def test_long_deck_job_status_keeps_batch_stage_on_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_long_deck_batch_generation(request, model, *, progress_logger=None):
        if progress_logger is not None:
            progress_logger("Starting batch_01 slides 1-2")
        raise RuntimeError("provider stopped")

    monkeypatch.setattr(api, "run_long_deck_batch_generation", fake_run_long_deck_batch_generation)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert body["current_stage"] == "generating_batch_01_of_15"
    assert "provider stopped" in body["error_message"]


def test_job_status_can_be_queried(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "succeeded"
    assert body["accepted"] is True
    assert isinstance(body["qa_score"], int)
    assert body["current_stage"] == "complete_job"
    assert body["last_updated_at"] == body["updated_at"]
    assert body["elapsed_seconds"] >= 0


def test_job_qa_gate_failure_is_completed_with_artifacts_not_runtime_failed(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch, accepted=False)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]

    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]

    assert body["status"] == "succeeded"
    assert body["accepted"] is False
    assert "QA score gate" in body["error_message"]
    assert {artifact["name"] for artifact in artifacts} >= {
        "generated_deck_ir",
        "patchable_elements",
        "generated_qa_report",
        "generated_attempts",
        "generated_deck",
    }


def test_job_failure_marks_failed_instead_of_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_build_pipeline(model, request, **kwargs):
        raise RuntimeError("provider failed with api_key=sk-testsecret123456789")

    monkeypatch.setattr(api, "run_build_pipeline", fake_run_build_pipeline)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]

    body = client.get(f"/api/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert body["accepted"] is False
    assert "provider failed" in body["error_message"]
    assert "sk-testsecret" not in body["error_message"]


def test_job_timeout_marks_failed_with_clear_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_build_pipeline(model, request, **kwargs):
        raise TimeoutError("Job timed out while running stage 'generate_deck' after 600 seconds.")

    monkeypatch.setattr(api, "run_build_pipeline", fake_run_build_pipeline)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]

    body = client.get(f"/api/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert "timed out" in body["error_message"]
    assert "generate_deck" in body["error_message"]


def test_job_status_expires_stale_running_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api, "JOB_TIMEOUT_SECONDS", -1)
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job()
    store.update_job(job.job_id, status="running", current_stage="generate_deck")

    body = client.get(f"/api/jobs/{job.job_id}").json()

    assert body["status"] == "failed"
    assert "timed out" in body["error_message"]
    assert "generate_deck" in body["error_message"]


def test_job_with_missing_patch_path_fails_fast(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())
    client = _client(tmp_path)
    payload = {
        **_job_payload(),
        "patch_path": str(tmp_path / "missing_patch.json"),
    }

    job_id = client.post("/api/jobs", json=payload).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert "Patch file not found" in body["error_message"]
    assert body["current_stage"] == "apply_patch"


def test_artifacts_can_be_listed(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]

    response = client.get(f"/api/jobs/{job_id}/artifacts")

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert {artifact["name"] for artifact in artifacts} == {
        "generated_deck_ir",
        "patchable_elements",
        "generated_qa_report",
        "generated_attempts",
        "generated_deck",
    }
    assert all(artifact["download_url"].startswith("/api/artifacts/") for artifact in artifacts)


def test_artifacts_include_patch_outputs_when_patch_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_build_pipeline(model, request, **kwargs):
        return _fake_pipeline_result_with_patch(request.output_dir, accepted=True)

    monkeypatch.setattr(api, "run_build_pipeline", fake_run_build_pipeline)
    client = _client(tmp_path)
    payload = {
        **_job_payload(),
        "patch_path": str(EXAMPLES_DIR / "sample_patch.json"),
    }

    job_id = client.post("/api/jobs", json=payload).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]

    assert body["status"] == "succeeded"
    assert body["accepted"] is True
    assert {artifact["name"] for artifact in artifacts} >= {
        "generated_deck_ir",
        "patchable_elements",
        "patch_report",
        "patched_deck",
        "patched_deck_ir",
    }


def test_registered_artifact_can_be_downloaded(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]
    deck_artifact = next(artifact for artifact in artifacts if artifact["name"] == "generated_deck_ir")

    response = client.get(deck_artifact["download_url"])

    assert response.status_code == 200
    assert json.loads(response.content)["deck_id"] == "sample_clean_business_deck"


def test_artifact_download_rejects_unregistered_file(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch)
    client = _client(tmp_path)
    job_id = client.post("/api/jobs", json=_job_payload()).json()["job_id"]
    unregistered = tmp_path / "jobs" / job_id / "unregistered.json"
    unregistered.write_text("{}", encoding="utf-8")

    response = client.get("/api/artifacts/unregistered.json")

    assert response.status_code == 404


def test_artifact_download_rejects_registered_path_outside_jobs_root(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    artifact = store.add_artifact(job.job_id, name="outside", kind="json", path=outside)

    response = client.get(f"/api/artifacts/{artifact.artifact_id}")

    assert response.status_code == 404


def test_missing_job_returns_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/jobs/missing-job")

    assert response.status_code == 404


def test_missing_artifact_returns_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/artifacts/missing-artifact")

    assert response.status_code == 404
