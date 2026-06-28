"""Private beta FastAPI backend for job-based deck builds."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field

from ppt_agent.generation import DeckGenerationRequest
from ppt_agent.job_store import ArtifactRecord, JobRecord, JobStore
from ppt_agent.models import StrictModel
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline


DEFAULT_DATA_DIR = Path("data")
DEFAULT_MODEL = "gpt-5.5"


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

      input {
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #b9c4d4;
        border-radius: 6px;
        padding: 10px 12px;
        font: inherit;
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
            <input id="patch_path" name="patch_path" placeholder="examples/sample_patch.json">
          </label>
        </div>
        <button id="generateButton" type="submit">生成 PPT</button>
      </form>

      <section>
        <h2>任务状态</h2>
        <p>任务 ID：<span id="jobId">暂无</span></p>
        <p>状态：<span id="jobStatus">未开始</span></p>
        <p id="errorMessage"></p>
      </section>

      <section>
        <h2>生成文件</h2>
        <ul id="artifacts"></ul>
      </section>
    </main>

    <script>
      const form = document.getElementById("jobForm");
      const button = document.getElementById("generateButton");
      const jobId = document.getElementById("jobId");
      const jobStatus = document.getElementById("jobStatus");
      const errorMessage = document.getElementById("errorMessage");
      const artifacts = document.getElementById("artifacts");
      let pollTimer = null;

      function setBusy(isBusy) {
        button.disabled = isBusy;
      }

      const statusText = {
        idle: "未开始",
        submitting: "提交中",
        pending: "等待中",
        running: "生成中",
        succeeded: "已完成",
        failed: "失败"
      };

      function setStatus(status) {
        jobStatus.textContent = statusText[status] || status;
      }

      function clearArtifacts() {
        artifacts.replaceChildren();
      }

      function buildPayload() {
        const patchPath = document.getElementById("patch_path").value.trim();
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
        return payload;
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
        setStatus(job.status);
        if (job.error_message) {
          errorMessage.textContent = job.error_message;
        }
        if (job.status === "succeeded" || job.status === "failed") {
          clearInterval(pollTimer);
          pollTimer = null;
          setBusy(false);
          await loadArtifacts(id);
          return true;
        }
        return false;
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (pollTimer) {
          clearInterval(pollTimer);
        }
        setBusy(true);
        setStatus("submitting");
        errorMessage.textContent = "";
        clearArtifacts();

        try {
          const job = await requestJson("/api/jobs", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(buildPayload())
          });
          jobId.textContent = job.job_id;
          setStatus(job.status);
          const finished = await pollJob(job.job_id);
          if (!finished) {
            pollTimer = setInterval(() => pollJob(job.job_id).catch((error) => {
              errorMessage.textContent = error.message;
              setBusy(false);
              clearInterval(pollTimer);
            }), 2000);
          }
        } catch (error) {
          errorMessage.textContent = error.message;
          setStatus("failed");
          setBusy(false);
        }
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
    language: str = Field(default="en", min_length=1)
    key_points: list[str] = Field(default_factory=list)
    min_qa_score: int = Field(default=80, ge=0, le=100)
    max_attempts: int = Field(default=2, ge=1)
    patch_path: str | None = Field(default=None, min_length=1)


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

    return ChatOpenAI(model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))


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


def _run_job(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    model,
    payload: CreateJobRequest,
) -> None:
    store.update_job(job_id, status="running")

    try:
        output_dir = jobs_root / job_id
        request = BuildPipelineRequest(
            generation_request=DeckGenerationRequest(
                topic=payload.topic,
                audience=payload.audience,
                slide_count=payload.slides,
                style=payload.style,
                language=payload.language,
                key_points=payload.key_points,
            ),
            theme_path=Path(payload.theme_path),
            output_dir=output_dir,
            min_qa_score=payload.min_qa_score,
            max_attempts=payload.max_attempts,
            patch_path=Path(payload.patch_path) if payload.patch_path else None,
        )
        result = run_build_pipeline(model, request)

        for artifact in result.artifacts:
            store.add_artifact(job_id, name=artifact.name, kind=artifact.kind, path=artifact.path)

        error_message = "\n".join(result.messages) if result.messages else None
        store.update_job(
            job_id,
            status="succeeded" if result.status_code == 0 else "failed",
            error_message=error_message,
            accepted=result.accepted,
            qa_score=result.generation_result.qa_report.score,
        )
    except Exception as exc:  # Keep failed jobs inspectable instead of surfacing background tracebacks.
        store.update_job(job_id, status="failed", error_message=str(exc), accepted=False)


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
            raise HTTPException(status_code=503, detail=f"Could not initialize OpenAI chat model: {exc}") from exc

        job = app.state.job_store.create_job()
        background_tasks.add_task(_run_job, app.state.job_store, app.state.jobs_root, job.job_id, model, payload)
        return CreateJobResponse(job_id=job.job_id, status=job.status)

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> JobResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
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
