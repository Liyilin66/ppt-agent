"""Private beta FastAPI backend for job-based deck builds."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from ppt_agent.generation import DeckGenerationRequest
from ppt_agent.job_store import ArtifactRecord, JobRecord, JobStore
from ppt_agent.models import StrictModel
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline


DEFAULT_DATA_DIR = Path("data")
DEFAULT_MODEL = "gpt-5.5"


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
