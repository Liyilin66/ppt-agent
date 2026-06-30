"""Private beta FastAPI backend for job-based deck builds."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field

from ppt_agent.generation import DeckGenerationRequest
from ppt_agent.job_store import ArtifactRecord, JobRecord, JobStore
from ppt_agent.long_deck_orchestrator import LongDeckRunReport, LongDeckRunRequest, run_long_deck_batch_generation
from ppt_agent.long_deck_render import LongDeckRenderReport, render_long_deck_ir_to_pptx
from ppt_agent.models import StrictModel
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline
from ppt_agent.runtime import StageEvent, sanitize_error_message, observed_stage


DEFAULT_DATA_DIR = Path("data")
DEFAULT_MODEL = "gpt-5.5"
JOB_TIMEOUT_SECONDS = 600
LONG_DECK_JOB_TIMEOUT_SECONDS = 3600
LLM_TIMEOUT_SECONDS = 120
DEFAULT_THEME_PATH = Path("examples/theme.json")
DEFAULT_ASSETS_DIR = Path("examples")

logger = logging.getLogger(__name__)


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ppt-agent PPT 生成器</title>
    <style>
      :root {
        color-scheme: light;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #172033;
        background: #f5f7fb;
      }

      body {
        margin: 0;
      }

      main {
        max-width: 860px;
        margin: 0 auto;
        padding: 40px 24px;
      }

      h1 {
        margin: 0 0 8px;
        font-size: 32px;
      }

      p {
        margin: 0 0 24px;
        color: #506078;
      }

      form, section {
        background: #ffffff;
        border: 1px solid #d9e0ec;
        border-radius: 8px;
        padding: 20px;
        margin-top: 18px;
      }

      .embedded-form {
        background: transparent;
        border: 0;
        padding: 0;
        margin-top: 0;
      }

      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
      }

      label {
        display: grid;
        gap: 6px;
        font-weight: 600;
      }

      input, textarea {
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #b9c4d4;
        border-radius: 6px;
        padding: 10px 12px;
        font: inherit;
      }

      textarea {
        min-height: 104px;
        resize: vertical;
      }

      .full {
        margin-top: 16px;
      }

      button {
        margin-top: 18px;
        border: 0;
        border-radius: 6px;
        background: #2457c5;
        color: #ffffff;
        padding: 11px 16px;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
      }

      button:disabled {
        cursor: wait;
        opacity: 0.65;
      }

      #jobStatus {
        font-weight: 700;
      }

      #currentStage {
        font-weight: 700;
      }

      #longRunningNotice {
        color: #8a5a00;
        font-weight: 600;
      }

      #errorMessage {
        color: #9d2f2f;
        white-space: pre-wrap;
      }

      #artifacts {
        display: grid;
        gap: 10px;
        padding-left: 0;
        list-style: none;
      }

      #artifacts a {
        color: #2457c5;
        font-weight: 700;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>ppt-agent PPT 生成器</h1>
      <p>创建本地内测版 PPT 生成任务，并下载生成文件。</p>

      <form id="jobForm">
        <div class="grid">
          <label>
            演示主题
            <input id="topic" name="topic" required placeholder="AI 教育应用">
          </label>
          <label>
            目标观众
            <input id="audience" name="audience" required placeholder="大学生">
          </label>
          <label>
            页数
            <input id="slides" name="slides" type="number" min="1" max="10" value="8" required>
          </label>
          <label>
            最低 QA 分数
            <input id="min_qa_score" name="min_qa_score" type="number" min="0" max="100" value="80" required>
          </label>
          <label>
            最大尝试次数
            <input id="max_attempts" name="max_attempts" type="number" min="1" value="2" required>
          </label>
          <label>
            Patch 文件路径
            <input id="patch_path" name="patch_path" placeholder="可选：examples/sample_patch.json">
          </label>
        </div>
        <label class="full">
          详细要求
          <textarea id="user_requirements" name="user_requirements" placeholder="例如：我要做一份给大学课堂展示的中文 PPT，风格简洁现代，重点讲 AI 如何帮助学习，但要提醒学术诚信风险。"></textarea>
        </label>
        <button id="generateButton" type="submit">生成 PPT</button>
      </form>

      <section>
        <h2>长 PPT实验模式</h2>
        <p>当前支持 30 页，使用 mini-batch generation，默认 batch_size=2。会生成 editable PPTX，但耗时较长。这是 experimental，不影响普通 8/10 页生成器。</p>
        <form id="longDeckForm" class="embedded-form">
          <div class="grid">
            <label>
              主题
              <input id="long_topic" name="topic" required value="AI 产品经理如何设计 Agent 产品">
            </label>
            <label>
              目标观众
              <input id="long_audience" name="audience" required value="准备进入 AI 产品岗位的 IT 硕士学生">
            </label>
            <label>
              页数
              <input id="long_slide_count" name="slide_count" type="number" min="30" max="30" value="30" required>
            </label>
            <label>
              batch_size
              <input id="long_batch_size" name="batch_size" type="number" min="1" max="10" value="2" required>
            </label>
            <label>
              最大 batch 尝试次数
              <input id="long_max_batch_attempts" name="max_batch_attempts" type="number" min="1" max="3" value="1" required>
            </label>
          </div>
          <label class="full">
            长 PPT详细要求
            <textarea id="long_user_requirements" name="user_requirements" required>中文技术产品分享，面向准备进入 AI 产品岗位的 IT 硕士学生。重点讲 AI Agent 产品经理需要理解的技术边界、用户需求分析、工作流设计、评估指标和落地风险。风格像技术产品分享，不像营销材料。每页要有明确观点，避免空泛口号。长 PPT 要按章节推进，不要每 10 页重复开场。背景色极淡蓝绿色。PPT 最终需要可编辑。</textarea>
          </label>
          <button id="generateLongDeckButton" type="submit">生成 30 页长 PPT</button>
        </form>
      </section>

      <section>
        <h2>任务状态</h2>
        <p>任务 ID：<span id="jobId">暂无</span></p>
        <p>状态：<span id="jobStatus">未开始</span></p>
        <p>当前阶段：<span id="currentStage">暂无</span></p>
        <p>运行时间：<span id="elapsedSeconds">0</span> 秒</p>
        <p id="longRunningNotice"></p>
        <p id="errorMessage"></p>
      </section>

      <section>
        <h2>生成文件</h2>
        <ul id="artifacts"></ul>
      </section>
    </main>

    <script>
      const form = document.getElementById("jobForm");
      const longDeckForm = document.getElementById("longDeckForm");
      const button = document.getElementById("generateButton");
      const longDeckButton = document.getElementById("generateLongDeckButton");
      const jobId = document.getElementById("jobId");
      const jobStatus = document.getElementById("jobStatus");
      const currentStage = document.getElementById("currentStage");
      const elapsedSeconds = document.getElementById("elapsedSeconds");
      const longRunningNotice = document.getElementById("longRunningNotice");
      const errorMessage = document.getElementById("errorMessage");
      const artifacts = document.getElementById("artifacts");
      let pollTimer = null;

      function setBusy(isBusy) {
        button.disabled = isBusy;
        longDeckButton.disabled = isBusy;
      }

      const statusText = {
        idle: "未开始",
        submitting: "提交中",
        pending: "等待中",
        running: "生成中",
        succeeded: "已完成",
        failed: "失败"
      };

      const stageText = {
        create_job: "正在创建任务",
        running: "正在启动生成任务",
        build_brief: "正在解析需求",
        build_brief_fast_path: "正在快速解析需求",
        build_brief_fallback: "需求解析超时，使用快速模式继续",
        generate_deck_plan: "正在规划大纲",
        generate_deck_plan_fast_path: "正在快速规划大纲",
        generate_deck_plan_fallback: "大纲规划超时，使用快速模式继续",
        generate_deck: "正在生成 Deck",
        qa_attempt: "正在执行 QA 检查",
        render_pptx: "正在渲染 PPTX",
        apply_patch: "正在处理 Patch",
        save_artifacts: "正在保存文件",
        preparing_long_deck_plan: "正在准备长 PPT规划",
        merging_long_deck_ir: "正在合并长 PPT Deck IR",
        running_long_deck_qa: "正在执行长 PPT QA",
        rendering_long_deck_pptx: "正在渲染长 PPT PPTX",
        completed: "已完成",
        complete_job: "正在完成任务"
      };

      function setStatus(status, accepted, errorMessageText = "") {
        if (status === "succeeded" && errorMessageText.includes("Patch")) {
          if (accepted === false && errorMessageText.includes("QA score gate")) {
            jobStatus.textContent = "已生成，但 QA 和 Patch 仍需修正";
            return;
          }
          jobStatus.textContent = "已生成，但 Patch 需要修正";
          return;
        }
        if (status === "succeeded" && accepted === false) {
          jobStatus.textContent = "已生成，但未通过 QA";
          return;
        }
        jobStatus.textContent = statusText[status] || status;
      }

      function stageLabel(stage) {
        const chunkMatch = /^generate_deck_chunk_(\\d+)_of_(\\d+)$/.exec(stage || "");
        if (chunkMatch) {
          return `正在生成 Deck：第 ${chunkMatch[1]}/${chunkMatch[2]} 组`;
        }
        const longBatchMatch = /^generating_batch_(\\d+)_of_(\\d+)$/.exec(stage || "");
        if (longBatchMatch) {
          return `正在生成长 PPT：batch ${longBatchMatch[1]}/${longBatchMatch[2]}`;
        }
        return stageText[stage] || stage || "暂无";
      }

      function setProgress(job) {
        currentStage.textContent = stageLabel(job.current_stage);
        elapsedSeconds.textContent = String(job.elapsed_seconds || 0);
        const isTerminal = job.status === "succeeded" || job.status === "failed";
        if (!isTerminal && (job.elapsed_seconds || 0) >= 300) {
          longRunningNotice.textContent = "任务运行时间较长，请检查后端日志。";
        } else {
          longRunningNotice.textContent = "";
        }
      }

      function clearArtifacts() {
        artifacts.replaceChildren();
      }

      function buildPayload() {
        const patchPath = document.getElementById("patch_path").value.trim();
        const userRequirements = document.getElementById("user_requirements").value.trim();
        const payload = {
          topic: document.getElementById("topic").value.trim(),
          audience: document.getElementById("audience").value.trim(),
          slides: Number(document.getElementById("slides").value),
          min_qa_score: Number(document.getElementById("min_qa_score").value),
          max_attempts: Number(document.getElementById("max_attempts").value)
        };
        if (patchPath) {
          payload.patch_path = patchPath;
        }
        if (userRequirements) {
          payload.user_requirements = userRequirements;
        }
        return payload;
      }

      function buildLongDeckPayload() {
        return {
          topic: document.getElementById("long_topic").value.trim(),
          audience: document.getElementById("long_audience").value.trim(),
          slide_count: Number(document.getElementById("long_slide_count").value),
          language: "zh-CN",
          deck_type: "technical_product_share",
          user_requirements: document.getElementById("long_user_requirements").value.trim(),
          batch_size: Number(document.getElementById("long_batch_size").value),
          max_batch_attempts: Number(document.getElementById("long_max_batch_attempts").value)
        };
      }

      async function requestJson(url, options) {
        const response = await fetch(url, options);
        const body = await response.json();
        if (!response.ok) {
          throw new Error(body.detail || "请求失败");
        }
        return body;
      }

      async function loadArtifacts(id) {
        const body = await requestJson(`/api/jobs/${id}/artifacts`);
        clearArtifacts();
        for (const artifact of body.artifacts) {
          const item = document.createElement("li");
          const link = document.createElement("a");
          link.href = artifact.download_url;
          link.textContent = `下载 ${artifact.name}.${artifact.kind}`;
          item.appendChild(link);
          artifacts.appendChild(item);
        }
      }

      async function pollJob(id) {
        const job = await requestJson(`/api/jobs/${id}`);
        setStatus(job.status, job.accepted, job.error_message || "");
        setProgress(job);
        if (job.error_message) {
          errorMessage.textContent = job.error_message;
        }
        if (job.status === "succeeded" || job.status === "failed") {
          if (pollTimer) {
            clearInterval(pollTimer);
          }
          pollTimer = null;
          setBusy(false);
          await loadArtifacts(id);
          return true;
        }
        return false;
      }

      async function submitJob(url, payload) {
        if (pollTimer) {
          clearInterval(pollTimer);
        }
        setBusy(true);
        setStatus("submitting");
        currentStage.textContent = "正在提交任务";
        elapsedSeconds.textContent = "0";
        longRunningNotice.textContent = "";
        errorMessage.textContent = "";
        clearArtifacts();

        try {
          const job = await requestJson(url, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
          });
          jobId.textContent = job.job_id;
          setStatus(job.status, job.accepted, job.error_message || "");
          const finished = await pollJob(job.job_id);
          if (!finished) {
            pollTimer = setInterval(() => pollJob(job.job_id).catch((error) => {
              errorMessage.textContent = error.message;
              setBusy(false);
              clearInterval(pollTimer);
              pollTimer = null;
            }), 2000);
          }
        } catch (error) {
          errorMessage.textContent = error.message;
          setStatus("failed");
          setBusy(false);
        }
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await submitJob("/api/jobs", buildPayload());
      });

      longDeckForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await submitJob("/api/long-deck-jobs", buildLongDeckPayload());
      });
    </script>
  </body>
</html>
"""


class CreateJobRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slides: int = Field(..., ge=1, le=10)
    theme_path: str = Field(default="examples/theme.json", min_length=1)
    style: str | None = Field(default=None, min_length=1)
    language: str = Field(default="zh-CN", min_length=1)
    key_points: list[str] = Field(default_factory=list)
    user_requirements: str | None = Field(default=None, min_length=1)
    min_qa_score: int = Field(default=80, ge=0, le=100)
    max_attempts: int = Field(default=2, ge=1)
    patch_path: str | None = Field(default=None, min_length=1)


class CreateLongDeckJobRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: Literal[30] = 30
    language: str = Field(default="zh-CN", min_length=1)
    deck_type: str = Field(default="technical_product_share", min_length=1)
    user_requirements: str = Field(..., min_length=1)
    batch_size: int = Field(default=2, ge=1, le=10)
    max_batch_attempts: int = Field(default=1, ge=1, le=3)


class CreateJobResponse(StrictModel):
    job_id: str
    status: Literal["pending", "running", "succeeded", "failed"]


class JobResponse(JobRecord):
    pass


class ArtifactResponse(StrictModel):
    artifact_id: str
    name: str
    kind: Literal["json", "pptx"]
    download_url: str


class ArtifactListResponse(StrictModel):
    artifacts: list[ArtifactResponse]


def _create_chat_model():
    from langchain_openai import ChatOpenAI

    kwargs = {"model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL)}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return ChatOpenAI(**kwargs)


def _artifact_response(artifact: ArtifactRecord) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=artifact.artifact_id,
        name=artifact.name,
        kind=artifact.kind,
        download_url=f"/api/artifacts/{artifact.artifact_id}",
    )


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _model_name(model) -> str:
    return str(getattr(model, "model_name", None) or getattr(model, "model", None) or model.__class__.__name__)


def _validate_patch_path(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError(f"Patch file must be a .json file: {path}")
    if not path.is_file():
        raise ValueError(f"Patch file not found: {path}")


def _log_job_stage(
    store: JobStore,
    job_id: str,
    *,
    stage_name: str,
    event: StageEvent,
    model_name: str,
    slide_count: int,
    use_deck_plan: bool,
    metadata: dict,
) -> None:
    if event == "start":
        chunk_index = metadata.get("chunk_index")
        total_chunks = metadata.get("total_chunks")
        current_stage = (
            f"generate_deck_chunk_{chunk_index}_of_{total_chunks}"
            if stage_name == "generate_deck" and chunk_index and total_chunks
            else stage_name
        )
        store.update_progress(job_id, current_stage=current_stage)

    error_message = metadata.get("error_message")
    record = {
        "job_id": job_id,
        "stage": stage_name,
        "event": event,
        "started_at": metadata.get("started_at"),
        "finished_at": metadata.get("finished_at"),
        "duration_ms": metadata.get("duration_ms"),
        "model_name": model_name,
        "slide_count": metadata.get("slide_count", slide_count),
        "use_deck_plan": metadata.get("use_deck_plan", use_deck_plan),
        "attempt_index": metadata.get("attempt_index"),
        "chunk_index": metadata.get("chunk_index"),
        "total_chunks": metadata.get("total_chunks"),
        "error_message": sanitize_error_message(error_message) if error_message else None,
    }
    logger.info("job_stage %s", json.dumps(record, ensure_ascii=False))


def _expire_stale_job(store: JobStore, job: JobRecord) -> JobRecord:
    timeout_seconds = _timeout_seconds_for_stage(job.current_stage)
    if job.status not in {"pending", "running"} or job.elapsed_seconds <= timeout_seconds:
        return job

    stage = job.current_stage or "unknown"
    error_message = f"Job timed out after {timeout_seconds:g} seconds while running stage '{stage}'."
    store.update_job(job.job_id, status="failed", error_message=error_message, accepted=False)
    return store.get_job(job.job_id) or job


def _timeout_seconds_for_stage(stage: str | None) -> int:
    if stage and (
        stage.startswith("generating_batch_")
        or stage
        in {
            "preparing_long_deck_plan",
            "merging_long_deck_ir",
            "running_long_deck_qa",
            "rendering_long_deck_pptx",
        }
    ):
        return LONG_DECK_JOB_TIMEOUT_SECONDS
    return JOB_TIMEOUT_SECONDS


def _expected_long_deck_batches(payload: CreateLongDeckJobRequest) -> int:
    return math.ceil(payload.slide_count / payload.batch_size)


def _long_deck_stage_from_progress(message: str, total_batches: int) -> str | None:
    if message.startswith("Starting long deck run:"):
        return "preparing_long_deck_plan"
    batch_match = re.match(r"^Starting batch_(\d+) slides ", message)
    if batch_match:
        return f"generating_batch_{batch_match.group(1)}_of_{total_batches}"
    if message.startswith("Merging "):
        return "merging_long_deck_ir"
    if message == "Running long deck QA":
        return "running_long_deck_qa"
    if message == "Long deck run succeeded":
        return "completed"
    return None


def _register_job_artifacts(store: JobStore, job_id: str, output_dir: Path) -> None:
    for artifact_path in sorted(output_dir.rglob("*")):
        if not artifact_path.is_file() or artifact_path.suffix.lower() not in {".json", ".pptx"}:
            continue
        kind: Literal["json", "pptx"] = "pptx" if artifact_path.suffix.lower() == ".pptx" else "json"
        store.add_artifact(job_id, name=artifact_path.stem, kind=kind, path=artifact_path)


def _read_long_deck_qa_score(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    try:
        score = json.loads(path.read_text(encoding="utf-8")).get("score")
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(score, int | float):
        return None
    if 0 <= score <= 1:
        return int(round(score * 100))
    if 0 <= score <= 100:
        return int(round(score))
    return None


def _run_job(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    model,
    payload: CreateJobRequest,
) -> None:
    started_at = time.monotonic()
    model_name = _model_name(model)

    def stage_observer(stage_name: str, event: StageEvent, metadata: dict) -> None:
        _log_job_stage(
            store,
            job_id,
            stage_name=stage_name,
            event=event,
            model_name=model_name,
            slide_count=payload.slides,
            use_deck_plan=True,
            metadata=metadata,
        )

    store.update_job(job_id, status="running", current_stage="running")

    try:
        output_dir = jobs_root / job_id
        patch_path = Path(payload.patch_path) if payload.patch_path else None
        if patch_path is not None:
            with observed_stage(stage_observer, "apply_patch", patch_path=str(patch_path)):
                _validate_patch_path(patch_path)

        request = BuildPipelineRequest(
            generation_request=DeckGenerationRequest(
                topic=payload.topic,
                audience=payload.audience,
                slide_count=payload.slides,
                style=payload.style,
                language=payload.language,
                key_points=payload.key_points,
                user_requirements=payload.user_requirements,
            ),
            theme_path=Path(payload.theme_path),
            output_dir=output_dir,
            min_qa_score=payload.min_qa_score,
            max_attempts=payload.max_attempts,
            patch_path=patch_path,
        )
        result = run_build_pipeline(
            model,
            request,
            stage_observer=stage_observer,
            llm_timeout_seconds=LLM_TIMEOUT_SECONDS,
            job_timeout_seconds=JOB_TIMEOUT_SECONDS,
            started_at_monotonic=started_at,
        )

        with observed_stage(stage_observer, "save_artifacts"):
            for artifact in result.artifacts:
                store.add_artifact(job_id, name=artifact.name, kind=artifact.kind, path=artifact.path)

        error_message = "\n".join(result.messages) if result.messages else None
        completed_with_artifacts = any(artifact.name == "generated_deck" for artifact in result.artifacts)
        with observed_stage(stage_observer, "complete_job"):
            store.update_job(
                job_id,
                status="succeeded" if completed_with_artifacts else "failed",
                error_message=error_message,
                accepted=result.accepted,
                qa_score=result.generation_result.qa_report.score,
                current_stage="complete_job",
            )
    except Exception as exc:  # Keep failed jobs inspectable instead of surfacing background tracebacks.
        error_message = sanitize_error_message(exc)
        logger.error("job_failed job_id=%s error=%s", job_id, error_message)
        store.update_job(job_id, status="failed", error_message=error_message, accepted=False)


def _run_long_deck_job(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    model,
    payload: CreateLongDeckJobRequest,
) -> None:
    model_name = _model_name(model)
    total_batches = _expected_long_deck_batches(payload)
    output_dir = jobs_root / job_id
    store.update_job(job_id, status="running", current_stage="preparing_long_deck_plan")

    def progress_logger(message: str) -> None:
        current_stage = _long_deck_stage_from_progress(message, total_batches)
        if current_stage is not None:
            store.update_progress(job_id, current_stage=current_stage)
        logger.info(
            "long_deck_job_stage %s",
            json.dumps(
                {
                    "job_id": job_id,
                    "message": message,
                    "current_stage": current_stage,
                    "model_name": model_name,
                    "slide_count": payload.slide_count,
                    "batch_size": payload.batch_size,
                    "total_batches": total_batches,
                },
                ensure_ascii=False,
            ),
        )

    try:
        run_report: LongDeckRunReport = run_long_deck_batch_generation(
            LongDeckRunRequest(
                topic=payload.topic,
                audience=payload.audience,
                slide_count=payload.slide_count,
                language=payload.language,
                deck_type=payload.deck_type,
                user_requirements=payload.user_requirements,
                batch_size=payload.batch_size,
                max_batch_attempts=payload.max_batch_attempts,
                output_dir=output_dir,
            ),
            model,
            progress_logger=progress_logger,
        )

        render_report: LongDeckRenderReport | None = None
        if run_report.merged_deck_ir_path is not None and run_report.status == "succeeded":
            store.update_progress(job_id, current_stage="rendering_long_deck_pptx")
            render_report = render_long_deck_ir_to_pptx(
                run_report.merged_deck_ir_path,
                output_dir / "generated_long_deck.pptx",
                output_dir / "long_deck_render_report.json",
                theme_path=DEFAULT_THEME_PATH,
                assets_dir=DEFAULT_ASSETS_DIR,
            )

        _register_job_artifacts(store, job_id, output_dir)

        qa_score = _read_long_deck_qa_score(run_report.long_deck_qa_path)
        if run_report.status != "succeeded":
            error_message = run_report.error_message or "Long deck generation did not finish successfully."
            store.update_job(
                job_id,
                status="failed",
                error_message=error_message,
                accepted=False,
                qa_score=qa_score,
            )
            return

        if render_report is None or render_report.status != "succeeded":
            error_message = (
                render_report.error_message
                if render_report is not None
                else "Long deck render report was not produced."
            )
            store.update_job(
                job_id,
                status="failed",
                error_message=error_message,
                accepted=False,
                qa_score=qa_score,
            )
            return

        store.update_job(
            job_id,
            status="succeeded",
            error_message=None,
            accepted=True,
            qa_score=qa_score,
            current_stage="completed",
        )
    except Exception as exc:  # Keep partial long-deck artifacts inspectable.
        error_message = sanitize_error_message(exc)
        logger.error("long_deck_job_failed job_id=%s error=%s", job_id, error_message)
        if output_dir.exists():
            _register_job_artifacts(store, job_id, output_dir)
        store.update_job(job_id, status="failed", error_message=error_message, accepted=False)


def create_app(data_dir: str | Path | None = None, store: JobStore | None = None) -> FastAPI:
    app = FastAPI(title="ppt-agent API")
    root = Path(data_dir) if data_dir is not None else Path(os.getenv("PPT_AGENT_DATA_DIR", DEFAULT_DATA_DIR))
    jobs_root = root / "jobs"

    app.state.job_store = store or JobStore(root / "jobs.sqlite3")
    app.state.jobs_root = jobs_root

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/jobs", response_model=CreateJobResponse, status_code=202)
    def create_job(payload: CreateJobRequest, background_tasks: BackgroundTasks) -> CreateJobResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")

        try:
            model = _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not initialize OpenAI chat model: {detail}") from exc

        job = app.state.job_store.create_job()
        model_name = _model_name(model)

        def create_stage_observer(stage_name: str, event: StageEvent, metadata: dict) -> None:
            _log_job_stage(
                app.state.job_store,
                job.job_id,
                stage_name=stage_name,
                event=event,
                model_name=model_name,
                slide_count=payload.slides,
                use_deck_plan=True,
                metadata=metadata,
            )

        with observed_stage(create_stage_observer, "create_job"):
            pass
        background_tasks.add_task(_run_job, app.state.job_store, app.state.jobs_root, job.job_id, model, payload)
        return CreateJobResponse(job_id=job.job_id, status=job.status)

    @app.post("/api/long-deck-jobs", response_model=CreateJobResponse, status_code=202)
    def create_long_deck_job(
        payload: CreateLongDeckJobRequest,
        background_tasks: BackgroundTasks,
    ) -> CreateJobResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")

        try:
            model = _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not initialize OpenAI chat model: {detail}") from exc

        job = app.state.job_store.create_job()
        app.state.job_store.update_progress(job.job_id, current_stage="preparing_long_deck_plan")
        background_tasks.add_task(
            _run_long_deck_job,
            app.state.job_store,
            app.state.jobs_root,
            job.job_id,
            model,
            payload,
        )
        return CreateJobResponse(job_id=job.job_id, status=job.status)

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> JobResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        job = _expire_stale_job(app.state.job_store, job)
        return JobResponse.model_validate(job.model_dump())

    @app.get("/api/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
    def list_artifacts(job_id: str) -> ArtifactListResponse:
        if app.state.job_store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        artifacts = app.state.job_store.list_artifacts(job_id)
        return ArtifactListResponse(artifacts=[_artifact_response(artifact) for artifact in artifacts])

    @app.get("/api/artifacts/{artifact_id}")
    def download_artifact(artifact_id: str) -> FileResponse:
        artifact = app.state.job_store.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")

        artifact_path = artifact.path.resolve()
        if not _path_within(artifact_path, app.state.jobs_root) or not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found.")

        return FileResponse(artifact_path, filename=artifact_path.name)

    return app


app = create_app()
