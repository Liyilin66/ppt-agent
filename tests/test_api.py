import json
import subprocess
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
from ppt_agent.ppt_master_execution import PPT_MASTER_EXECUTION_PLAN_ARTIFACT
from ppt_agent.qa import analyze_deck
from ppt_agent.patch import apply_patch
from ppt_agent.ppt_master_output import (
    PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
    PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
    register_ppt_master_output_artifacts,
)
from ppt_agent.ppt_master_project import (
    PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT,
    PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT,
)
from ppt_agent.ppt_master_runner import PPT_MASTER_RUNNER_RESULT_ARTIFACT


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


def _mock_expected_ppt_master_root(tmp_path: Path) -> Path:
    root = tmp_path / "ppt-master"
    skill_dir = root / "skills" / "ppt-master"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# PPT Master Skill\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "requirements.txt").write_text("# test requirements\n", encoding="utf-8")
    (root / "README.md").write_text("# ppt-master\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/hugohe3/ppt-master.git"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _build_mock_ppt_master_output(output_dir: Path, *, slide_count: int = 3) -> Path:
    from pptx import Presentation

    output_dir.mkdir(parents=True, exist_ok=True)
    presentation = Presentation()
    while len(presentation.slides) < slide_count:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        textbox = slide.shapes.add_textbox(50, 50, 400, 80)
        textbox.text_frame.text = f"Slide {len(presentation.slides)}"
    if len(presentation.slides) > slide_count:
        while len(presentation.slides) > slide_count:
            slide_id = presentation.slides._sldIdLst[-1].rId  # type: ignore[attr-defined]
            presentation.part.drop_rel(slide_id)
            del presentation.slides._sldIdLst[-1]  # type: ignore[attr-defined]
    presentation.save(output_dir / "generated_by_ppt_master.pptx")
    (output_dir / "generation_notes.md").write_text(
        "# Notes\n\nMode: Native DrawingML shapes (directly editable)\n",
        encoding="utf-8",
    )
    (output_dir / "agent_pm_recovery_ppt169_20260701" / "svg_output").mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_mock_ppt_master_package(job_dir: Path) -> Path:
    package_dir = job_dir / "ppt_master_package"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "source.md").write_text("# Presentation Request\n", encoding="utf-8")
    (package_dir / "run_prompt.md").write_text("# PPT Master Local Job Prompt\n", encoding="utf-8")
    (package_dir / "README.md").write_text("# PPT Master Job Package\n", encoding="utf-8")
    (package_dir / "manifest.json").write_text('{"is_available": true}\n', encoding="utf-8")
    return package_dir


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
    quality_gate_path = output_dir / "generated_long_deck_quality_gate.json"
    run_report_path = output_dir / "long_deck_run_report.json"
    batch_status_path = batch_dir / "batch_01_status.json"
    batch_deck_path = batch_dir / "batch_01_deck_ir.json"
    batch_qa_path = batch_dir / "batch_01_qa_report.json"
    batch_attempts_path = batch_dir / "batch_01_attempts.json"

    plan_path.write_text('{"sections":[],"batches":[]}\n', encoding="utf-8")
    merged_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    qa_path.write_text('{"score":0.82,"passed":true}\n', encoding="utf-8")
    quality_gate_path.write_text('{"status":"passed","score":82,"issues":[],"blocked_codes":[],"blocked_slide_ids":[],"blocked_element_ids":[],"message":"passed"}\n', encoding="utf-8")
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
        long_deck_quality_gate_path=quality_gate_path,
        long_deck_plan_path=plan_path,
        run_report_path=run_report_path,
    )
    run_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _inject_recovery_source_noise(deck_ir_path: Path) -> None:
    payload = json.loads(deck_ir_path.read_text(encoding="utf-8"))
    payload["slides"][0]["title"] = "slide_id: leaked internal field"
    payload["slides"][0]["elements"][0]["text"] = "risk: 把这一点转化为明确的下一步行动"
    payload["slides"][0]["elements"][1]["text"] = (
        "Impact：先列出 Agent 不允许自动执行的动作\n"
        "判断点 1：用户任务要先被拆成可执行工作流"
    )
    payload["slides"][1]["elements"][0]["text"] = "Mitigation：用灰度和回滚控制上线质量"
    payload["slides"][1]["elements"][1]["text"] = "Option A: element_id should not leak"
    deck_ir_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _install_fake_long_deck_backend(
    monkeypatch,
    captured: dict | None = None,
    *,
    ppt_master_root: Path | None = None,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    if ppt_master_root is not None:
        monkeypatch.setenv("PPT_MASTER_DIR", str(ppt_master_root))
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_long_deck_batch_generation(request, model, *, progress_logger=None, cancel_checker=None):
        if captured is not None:
            captured["request"] = request
            captured.setdefault("requests", []).append(request)
            captured["model"] = model
            captured["cancel_checker"] = cancel_checker
        if progress_logger is not None:
            progress_logger("Starting long deck run: 30 slides, batch_size=2, total_batches=15")
            progress_logger("Starting batch_01 slides 1-2")
            progress_logger("Completed batch_01 in 0.1s")
            progress_logger("Merging 15 batches")
            progress_logger("Running long deck QA")
            progress_logger("Running long deck hard quality gate")
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


def _install_fake_long_deck_quality_gate_failure(monkeypatch, captured: dict | None = None) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_long_deck_batch_generation(request, model, *, progress_logger=None, cancel_checker=None):
        if captured is not None:
            captured["request"] = request
        if progress_logger is not None:
            progress_logger("Starting long deck run: 30 slides, batch_size=2, total_batches=15")
            progress_logger("Starting batch_01 slides 1-2")
            progress_logger("Completed batch_01 in 0.1s")
            progress_logger("Merging 15 batches")
            progress_logger("Running long deck QA")
            progress_logger("Running long deck hard quality gate")
            progress_logger("Long deck run failed_quality_gate")
        report = _fake_long_deck_run_report(request)
        if report.merged_deck_ir_path is not None:
            _inject_recovery_source_noise(report.merged_deck_ir_path)
        quality_gate_path = request.output_dir / "generated_long_deck_quality_gate.json"
        quality_gate_path.write_text(
            json.dumps(
                {
                    "status": "failed_quality_gate",
                    "score": 68,
                    "issues": [
                        {
                            "severity": "error",
                            "slide_id": "slide_021",
                            "element_id": "s021_e01",
                            "code": "instruction_leakage",
                            "message": "meta leakage",
                        }
                    ],
                    "blocked_codes": ["instruction_leakage"],
                    "blocked_slide_ids": ["slide_021"],
                    "blocked_element_ids": ["s021_e01"],
                    "message": "Long-deck hard quality gate failed because audience-visible instruction leakage or matrix placeholder content was detected.",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        report = report.model_copy(
            update={
                "status": "failed_quality_gate",
                "long_deck_quality_gate_path": quality_gate_path,
                "error_message": "Long-deck hard quality gate failed because audience-visible instruction leakage or matrix placeholder content was detected.",
                "error_type": "failed_quality_gate",
                "suggestion": "Fix instruction leakage or placeholder matrix content before rendering PPTX.",
            }
        )
        report.run_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report

    def fail_if_render_called(*args, **kwargs):
        raise AssertionError("render_long_deck_ir_to_pptx should not be called after quality gate failure")

    monkeypatch.setattr(api, "run_long_deck_batch_generation", fake_run_long_deck_batch_generation)
    monkeypatch.setattr(api, "render_long_deck_ir_to_pptx", fail_if_render_called)


def _install_fake_long_deck_timeout_before_merge(monkeypatch, captured: dict | None = None) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_long_deck_batch_generation(request, model, *, progress_logger=None, cancel_checker=None):
        if captured is not None:
            captured["request"] = request
        if progress_logger is not None:
            progress_logger("Starting long deck run: 30 slides, batch_size=2, total_batches=15")
            for batch_number in range(1, 13):
                progress_logger(f"Starting batch_{batch_number:02d} slides 1-2")
                progress_logger(f"Completed batch_{batch_number:02d} in 0.1s")
            progress_logger("Starting batch_13 slides 25-26")
        raise TimeoutError(
            f"Job timed out after {api._long_deck_job_timeout_seconds()} seconds while running stage "
            "'generating_batch_13_of_15'."
        )

    monkeypatch.setattr(api, "run_long_deck_batch_generation", fake_run_long_deck_batch_generation)


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
        "取消长 PPT任务",
        "继续/重试长 PPT",
        "PPT Master 渲染包",
        "PPT Master 执行桥",
        "PPT Master Visual Project",
        "PPT Master 本地导出",
        "PPT Master 生成结果",
        "PPT Master Source Markdown",
        "PPT Master Run Prompt",
        "PPT Master Package Manifest",
        "PPT Master Package README",
        "PPT Master Execution Plan",
        "PPT Master Visual Project Manifest",
        "PPT Master Project Instructions",
        "PPT Master Runner Result",
        "PPT Master Generated PPTX",
        "PPT Master Generation Notes",
        "PPT Master Output Manifest",
        "准备 PPT Master 执行计划",
        "准备 PPT Master Visual Project",
        "运行 PPT Master 本地导出",
        "execution status",
        "bootstrap status",
        "expected pptx",
        "runner status",
        "需要外部 AI 生成 project",
        "原因",
        "建议",
        "package_mode",
        "quality_gate",
        "Recovery package 已生成",
        "系统会从已完成 batch 后继续",
        "当前阶段不会自动运行 ppt-master",
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
    assert "正在执行长 PPT质量门禁" in response.text
    assert "正在渲染长 PPT PPTX" in response.text
    assert "质量门禁失败" in response.text
    assert "currentBatch" in response.text
    assert "totalBatches" in response.text
    assert "completedBatches" in response.text
    assert "failedBatches" in response.text
    assert "取消请求已发送" in response.text


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
    ppt_master_root = _mock_expected_ppt_master_root(tmp_path)
    _install_fake_long_deck_backend(monkeypatch, captured, ppt_master_root=ppt_master_root)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]
    artifact_names = {artifact["name"] for artifact in artifacts}

    assert body["status"] == "succeeded"
    assert body["accepted"] is True
    assert body["qa_score"] == 82
    assert body["current_stage"] == "completed"
    assert body["job_type"] == "long_deck"
    assert body["total_batches"] == 15
    assert body["completed_batches"] == 1
    assert body["failed_batches"] == 0
    assert body["current_batch"] == "batch_01"
    assert body["cancel_requested"] is False
    assert body["ppt_master_package"]["generated"] is True
    assert body["ppt_master_package"]["available"] is True
    assert body["ppt_master_package"]["is_expected_repo"] is True
    assert body["ppt_master_package"]["package_mode"] == "normal"
    assert body["ppt_master_package"]["reason"] == "normal_generated"
    assert body["ppt_master_package"]["source_quality_gate_status"] == "passed"
    assert body["ppt_master_package"]["warning"] is None
    assert body["ppt_master_package"]["ppt_master_root"] == str(ppt_master_root.resolve())
    assert body["ppt_master_package"]["missing_paths"] == []
    assert body["ppt_master_package"]["source_artifact_id"]
    assert body["ppt_master_package"]["run_prompt_artifact_id"]
    assert body["ppt_master_package"]["manifest_artifact_id"]
    assert body["ppt_master_package"]["readme_artifact_id"]
    assert captured["render_output"].name == "generated_long_deck.pptx"
    assert {
        "generated_long_deck_plan",
        "generated_long_deck_ir",
        "generated_long_deck_qa",
        "generated_long_deck_quality_gate",
        "generated_long_deck",
        "ppt_master_source",
        "ppt_master_run_prompt",
        "ppt_master_package_manifest",
        "ppt_master_package_README",
        "long_deck_run_report",
        "long_deck_render_report",
        "long_deck_request",
        "batch_01_status",
        "batch_01_deck_ir",
        "batch_01_qa_report",
        "batch_01_attempts",
    } <= artifact_names

    deck_ir_artifact = next(artifact for artifact in artifacts if artifact["name"] == "generated_long_deck_ir")
    ppt_master_artifact = next(artifact for artifact in artifacts if artifact["name"] == "ppt_master_source")
    ppt_master_prompt_artifact = next(artifact for artifact in artifacts if artifact["name"] == "ppt_master_run_prompt")
    ppt_master_manifest_artifact = next(
        artifact for artifact in artifacts if artifact["name"] == "ppt_master_package_manifest"
    )
    ppt_master_readme_artifact = next(
        artifact for artifact in artifacts if artifact["name"] == "ppt_master_package_README"
    )
    assert client.get(deck_ir_artifact["download_url"]).status_code == 200
    assert ppt_master_artifact["kind"] == "md"
    assert client.get(ppt_master_artifact["download_url"]).status_code == 200
    assert ppt_master_prompt_artifact["kind"] == "md"
    assert client.get(ppt_master_prompt_artifact["download_url"]).status_code == 200
    assert ppt_master_manifest_artifact["kind"] == "json"
    assert ppt_master_readme_artifact["kind"] == "md"
    assert client.get(ppt_master_readme_artifact["download_url"]).status_code == 200
    manifest_response = client.get(ppt_master_manifest_artifact["download_url"])
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["is_available"] is True
    assert manifest["is_expected_repo"] is True
    assert manifest["package_mode"] == "normal"


def test_long_deck_resume_endpoint_reuses_original_output_dir(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    _install_fake_long_deck_backend(monkeypatch, captured)
    client = _client(tmp_path)

    original_job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    resume_response = client.post(f"/api/long-deck-jobs/{original_job_id}/resume")

    assert resume_response.status_code == 202
    resume_job_id = resume_response.json()["job_id"]
    assert resume_job_id != original_job_id
    assert captured["requests"][-1].resume is True
    assert captured["requests"][-1].output_dir == tmp_path / "jobs" / original_job_id
    body = client.get(f"/api/jobs/{resume_job_id}").json()
    assert body["status"] == "succeeded"
    artifacts = client.get(f"/api/jobs/{resume_job_id}/artifacts").json()["artifacts"]
    assert {artifact["name"] for artifact in artifacts} >= {
        "generated_long_deck_ir",
        "generated_long_deck",
        "ppt_master_source",
        "ppt_master_run_prompt",
        "ppt_master_package_manifest",
        "ppt_master_package_README",
        "long_deck_run_report",
    }


def test_long_deck_job_quality_gate_failure_keeps_ir_artifacts_and_skips_render(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict = {}
    _install_fake_long_deck_quality_gate_failure(monkeypatch, captured)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]
    artifact_names = {artifact["name"] for artifact in artifacts}

    assert body["status"] == "failed_quality_gate"
    assert body["accepted"] is False
    assert body["current_stage"] == "failed_quality_gate"
    assert "quality gate failed" in body["error_message"].lower()
    assert body["ppt_master_package"]["generated"] is True
    assert body["ppt_master_package"]["package_mode"] == "recovery"
    assert body["ppt_master_package"]["reason"] == "quality_gate_failed_recovery_generated"
    assert body["ppt_master_package"]["source_quality_gate_status"] == "failed_quality_gate"
    assert body["ppt_master_package"]["source_artifact_id"]
    assert "不会生成旧 renderer PPTX" in body["ppt_master_package"]["message"]
    assert "recovery package" in body["ppt_master_package"]["message"]
    assert "failed the hard quality gate" in body["ppt_master_package"]["warning"]
    assert {
        "generated_long_deck_plan",
        "generated_long_deck_ir",
        "generated_long_deck_qa",
        "generated_long_deck_quality_gate",
        "ppt_master_source",
        "ppt_master_run_prompt",
        "ppt_master_package_manifest",
        "ppt_master_package_README",
        "long_deck_run_report",
        "long_deck_request",
        "batch_01_status",
        "batch_01_deck_ir",
        "batch_01_qa_report",
        "batch_01_attempts",
    } <= artifact_names
    assert "generated_long_deck" not in artifact_names
    assert "long_deck_render_report" not in artifact_names
    source_artifact = next(artifact for artifact in artifacts if artifact["name"] == "ppt_master_source")
    manifest_artifact = next(artifact for artifact in artifacts if artifact["name"] == "ppt_master_package_manifest")
    source_markdown = client.get(source_artifact["download_url"]).text
    lowered_source = source_markdown.lower()
    assert "risk:" not in lowered_source
    assert "risk：" not in lowered_source
    assert "impact:" not in lowered_source
    assert "impact：" not in lowered_source
    assert "mitigation:" not in lowered_source
    assert "mitigation：" not in lowered_source
    assert "判断点 1" not in source_markdown
    assert "Option A" not in source_markdown
    assert "把这一点转化为明确的下一步行动" not in source_markdown
    assert "先列出 Agent 不允许自动执行的动作" not in source_markdown
    assert "bbox" not in source_markdown
    assert "element_id" not in source_markdown
    assert "slide_id" not in source_markdown
    manifest = client.get(manifest_artifact["download_url"]).json()
    assert manifest["package_mode"] == "recovery"
    assert manifest["source_quality_gate_status"] == "failed_quality_gate"
    assert manifest["source_quality_gate_report_path"].endswith("generated_long_deck_quality_gate.json")
    assert manifest["warning"] == (
        "This package was generated from a Deck IR that failed the hard quality gate. "
        "It is intended for PPT Master recovery rendering, not direct renderer output."
    )


def test_cancel_endpoint_marks_running_long_deck_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="running", current_stage="generating_batch_01_of_15")

    response = client.post(f"/api/jobs/{job.job_id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["cancel_requested"] is True
    assert body["current_stage"] == "cancel_requested"
    assert store.is_cancel_requested(job.job_id) is True


def test_cancel_endpoint_rejects_short_deck_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="short_deck")
    store.update_job(job.job_id, status="running", current_stage="generate_deck")

    response = client.post(f"/api/jobs/{job.job_id}/cancel")

    assert response.status_code == 400
    assert "Only long deck jobs" in response.json()["detail"]


def test_long_deck_job_status_keeps_batch_stage_on_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(api, "_create_chat_model", lambda: object())

    def fake_run_long_deck_batch_generation(request, model, *, progress_logger=None, cancel_checker=None):
        if progress_logger is not None:
            progress_logger("Starting batch_01 slides 1-2")
        raise RuntimeError("provider stopped")

    monkeypatch.setattr(api, "run_long_deck_batch_generation", fake_run_long_deck_batch_generation)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]

    assert body["status"] == "failed"
    assert body["current_stage"] == "generating_batch_01_of_15"
    assert "provider stopped" in body["error_message"]
    assert body["ppt_master_package"]["generated"] is False
    assert body["ppt_master_package"]["reason"] == "batch_generation_failed_before_merge"
    assert body["ppt_master_package"]["available"] is None
    assert body["ppt_master_package"]["is_expected_repo"] is None
    assert body["ppt_master_package"]["ppt_master_root"] is None
    assert "Resume the job" in body["ppt_master_package"]["message"]
    assert "ppt_master_source" not in {artifact["name"] for artifact in artifacts}


def test_long_deck_job_timeout_before_merge_keeps_ppt_master_state_unknown(tmp_path: Path, monkeypatch) -> None:
    _install_fake_long_deck_timeout_before_merge(monkeypatch)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    artifacts = client.get(f"/api/jobs/{job_id}/artifacts").json()["artifacts"]

    assert body["status"] == "failed"
    assert body["current_stage"] == "generating_batch_13_of_15"
    assert "timed out after 3600 seconds" in body["error_message"]
    assert body["ppt_master_package"]["generated"] is False
    assert body["ppt_master_package"]["package_mode"] is None
    assert body["ppt_master_package"]["reason"] == "job_timeout_before_merge"
    assert body["ppt_master_package"]["available"] is None
    assert body["ppt_master_package"]["is_expected_repo"] is None
    assert body["ppt_master_package"]["ppt_master_root"] is None
    assert body["ppt_master_package"]["missing_paths"] == []
    assert "Resume the job" in body["ppt_master_package"]["message"]
    assert "PPT Master package will be generated after merge" in body["ppt_master_package"]["message"]
    assert "ppt_master_source" not in {artifact["name"] for artifact in artifacts}


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
    assert body["ppt_master_package"] is None
    assert body["ppt_master_execution"] is None
    assert body["ppt_master_output"] is None
    assert body["ppt_master_runner"] is None


def test_latest_long_deck_job_can_be_queried(tmp_path: Path, monkeypatch) -> None:
    _install_fake_long_deck_backend(monkeypatch)
    client = _client(tmp_path)
    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]

    response = client.get("/api/jobs/latest?job_type=long_deck")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["job_type"] == "long_deck"


def test_latest_long_deck_job_excludes_short_deck_jobs(tmp_path: Path, monkeypatch) -> None:
    _install_fake_backend(monkeypatch)
    _install_fake_long_deck_backend(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/jobs", json=_job_payload())
    long_job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]

    response = client.get("/api/jobs/latest?job_type=long_deck")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == long_job_id
    assert body["job_type"] == "long_deck"


def test_old_long_deck_job_without_ppt_master_package_still_returns_status(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="succeeded", accepted=True, current_stage="completed")

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["ppt_master_package"]["generated"] is False
    assert "not been generated" in body["ppt_master_package"]["message"]
    assert body["ppt_master_execution"]["status"] == "not_prepared"
    assert "has not been prepared" in body["ppt_master_execution"]["message"]
    assert body["ppt_master_visual_project"]["status"] == "not_bootstrapped"
    assert "has not been bootstrapped" in body["ppt_master_visual_project"]["message"]
    assert body["ppt_master_output"]["detected"] is False
    assert body["ppt_master_output"]["message"] == "No PPT Master output has been registered for this job."
    assert body["ppt_master_runner"]["status"] == "not_run"
    assert "has not been run" in body["ppt_master_runner"]["message"]


def test_long_deck_job_status_reports_registered_ppt_master_output(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="succeeded", accepted=True, current_stage="completed")
    output_dir = tmp_path / "jobs" / job.job_id / "ppt_master_output"
    _build_mock_ppt_master_output(output_dir, slide_count=30)
    register_ppt_master_output_artifacts(store, job_id=job.job_id, output_dir=output_dir)

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["ppt_master_output"]["detected"] is True
    assert body["ppt_master_output"]["slide_count"] == 30
    assert body["ppt_master_output"]["generation_status"] == "succeeded"
    assert body["ppt_master_output"]["pptx_artifact_id"]
    assert body["ppt_master_output"]["notes_artifact_id"]
    assert body["ppt_master_output"]["manifest_artifact_id"]
    assert body["ppt_master_output"]["output_dir"] == str(output_dir.resolve())


def test_long_deck_job_status_auto_registers_existing_ppt_master_output(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="succeeded", accepted=True, current_stage="completed")
    output_dir = tmp_path / "jobs" / job.job_id / "ppt_master_output"
    _build_mock_ppt_master_output(output_dir, slide_count=30)

    response = client.get(f"/api/jobs/{job.job_id}")
    artifacts = client.get(f"/api/jobs/{job.job_id}/artifacts").json()["artifacts"]

    assert response.status_code == 200
    body = response.json()
    assert body["ppt_master_output"]["detected"] is True
    assert {artifact["name"] for artifact in artifacts} >= {
        PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
        PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
        PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    }


def test_prepare_ppt_master_execution_endpoint_returns_waiting_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _mock_expected_ppt_master_root(tmp_path)
    monkeypatch.setenv("PPT_MASTER_DIR", str(root))
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="failed_quality_gate", current_stage="failed_quality_gate")
    _build_mock_ppt_master_package(tmp_path / "jobs" / job.job_id)

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/prepare-ppt-master-execution")
    status_response = client.get(f"/api/jobs/{job.job_id}")
    artifacts = client.get(f"/api/jobs/{job.job_id}/artifacts").json()["artifacts"]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "waiting_for_external_ppt_master_run"
    assert body["plan_artifact_id"]
    assert body["expected_pptx_path"].endswith("generated_by_ppt_master.pptx")
    assert any("run_prompt.md" in step for step in body["suggested_steps"])
    assert any("register_ppt_master_output.py" in step for step in body["suggested_steps"])
    status_body = status_response.json()
    assert status_body["ppt_master_execution"]["status"] == "waiting_for_external_ppt_master_run"
    assert status_body["ppt_master_execution"]["plan_artifact_id"]
    assert PPT_MASTER_EXECUTION_PLAN_ARTIFACT in {artifact["name"] for artifact in artifacts}


def test_prepare_ppt_master_execution_endpoint_detects_output_and_registers_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _mock_expected_ppt_master_root(tmp_path)
    monkeypatch.setenv("PPT_MASTER_DIR", str(root))
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="failed_quality_gate", current_stage="failed_quality_gate")
    job_dir = tmp_path / "jobs" / job.job_id
    _build_mock_ppt_master_package(job_dir)
    _build_mock_ppt_master_output(job_dir / "ppt_master_output", slide_count=30)

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/prepare-ppt-master-execution")
    status_response = client.get(f"/api/jobs/{job.job_id}")
    artifacts = client.get(f"/api/jobs/{job.job_id}/artifacts").json()["artifacts"]

    assert response.status_code == 200
    assert response.json()["status"] == "output_detected"
    status_body = status_response.json()
    assert status_body["ppt_master_execution"]["status"] == "output_detected"
    assert status_body["ppt_master_output"]["detected"] is True
    assert status_body["ppt_master_output"]["slide_count"] == 30
    assert {artifact["name"] for artifact in artifacts} >= {
        PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
        PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
        PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
        PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    }


def test_prepare_ppt_master_execution_endpoint_reports_missing_package(tmp_path: Path) -> None:
    root = _mock_expected_ppt_master_root(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="failed_quality_gate", current_stage="failed_quality_gate")

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/prepare-ppt-master-execution")

    assert response.status_code == 200
    assert response.json()["status"] == "missing_package"
    assert (tmp_path / "jobs" / job.job_id / "ppt_master_execution_plan.json").exists()
    assert root.exists()


def test_prepare_ppt_master_execution_endpoint_reports_unavailable_ppt_master(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PPT_MASTER_DIR", str(tmp_path / "missing-ppt-master"))
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="failed_quality_gate", current_stage="failed_quality_gate")
    _build_mock_ppt_master_package(tmp_path / "jobs" / job.job_id)

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/prepare-ppt-master-execution")

    assert response.status_code == 200
    assert response.json()["status"] == "ppt_master_unavailable"


def test_prepare_ppt_master_execution_endpoint_rejects_short_deck_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="short_deck")

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/prepare-ppt-master-execution")

    assert response.status_code == 400
    assert "Only long deck jobs" in response.json()["detail"]


def test_bootstrap_ppt_master_project_endpoint_creates_scaffold_and_registers_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _mock_expected_ppt_master_root(tmp_path)
    monkeypatch.setenv("PPT_MASTER_DIR", str(root))
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="failed_quality_gate", current_stage="failed_quality_gate")
    job_dir = tmp_path / "jobs" / job.job_id
    _build_mock_ppt_master_package(job_dir)

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/bootstrap-ppt-master-project")
    status_response = client.get(f"/api/jobs/{job.job_id}")
    artifacts = client.get(f"/api/jobs/{job.job_id}/artifacts").json()["artifacts"]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["manifest_artifact_id"]
    assert body["instructions_artifact_id"]
    assert body["project_dir"].endswith("ppt_master_visual_project")
    assert body["project_source_path"].endswith("inputs/source.md")
    assert body["project_prompt_path"].endswith("inputs/run_prompt.md")
    assert body["expected_svg_output_dir"].endswith("svg_output")
    assert body["expected_svg_final_dir"].endswith("svg_final")
    assert body["expected_pptx_path"].endswith("generated_by_ppt_master.pptx")
    assert any("PROJECT_INSTRUCTIONS.md" in step for step in body["next_steps"])
    status_body = status_response.json()
    assert status_body["ppt_master_visual_project"]["status"] == "created"
    assert status_body["ppt_master_visual_project"]["instructions_artifact_id"]
    assert {artifact["name"] for artifact in artifacts} >= {
        PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT,
        PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT,
    }


def test_bootstrap_ppt_master_project_endpoint_rejects_short_deck_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="short_deck")

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/bootstrap-ppt-master-project")

    assert response.status_code == 400
    assert "Only long deck jobs" in response.json()["detail"]


def test_run_ppt_master_local_export_endpoint_reports_external_generation_needed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _mock_expected_ppt_master_root(tmp_path)
    monkeypatch.setenv("PPT_MASTER_DIR", str(root))
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="long_deck")
    store.update_job(job.job_id, status="failed_quality_gate", current_stage="failed_quality_gate")
    _build_mock_ppt_master_package(tmp_path / "jobs" / job.job_id)

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/run-ppt-master-local-export")
    status_response = client.get(f"/api/jobs/{job.job_id}")
    artifacts = client.get(f"/api/jobs/{job.job_id}/artifacts").json()["artifacts"]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "requires_external_ai_generation"
    assert body["requires_external_ai_generation"] is True
    assert body["result_artifact_id"]
    assert "visual project" in body["message"]
    status_body = status_response.json()
    assert status_body["ppt_master_runner"]["status"] == "requires_external_ai_generation"
    assert status_body["ppt_master_runner"]["result_artifact_id"]
    assert PPT_MASTER_RUNNER_RESULT_ARTIFACT in {artifact["name"] for artifact in artifacts}


def test_run_ppt_master_local_export_endpoint_rejects_short_deck_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = api.create_app(data_dir=tmp_path, store=store)
    client = TestClient(app)
    job = store.create_job(job_type="short_deck")

    response = client.post(f"/api/long-deck-jobs/{job.job_id}/run-ppt-master-local-export")

    assert response.status_code == 400
    assert "Only long deck jobs" in response.json()["detail"]


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


def test_long_deck_job_timeout_seconds_can_be_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LONG_DECK_JOB_TIMEOUT_SECONDS", "7200")
    _install_fake_long_deck_timeout_before_merge(monkeypatch)
    client = _client(tmp_path)

    job_id = client.post("/api/long-deck-jobs", json=_long_deck_payload()).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()

    assert api._long_deck_job_timeout_seconds() == 7200
    assert "timed out after 7200 seconds" in body["error_message"]
    assert body["ppt_master_package"]["reason"] == "job_timeout_before_merge"


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
