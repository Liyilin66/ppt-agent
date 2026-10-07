"""Shared v2 job execution and bookkeeping, independent of transport."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from ppt_agent.job_store import ArtifactKind, DeckRevisionRecord, JobRecord, JobStore
from ppt_agent.models import StrictModel
from ppt_agent.ppt_master_execution import (
    PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
    PPT_MASTER_EXECUTION_PLAN_FILENAME,
)
from ppt_agent.ppt_master_output import (
    PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    PPT_MASTER_OUTPUT_MANIFEST_FILENAME,
    PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
    PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
)
from ppt_agent.ppt_master_project import (
    PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT,
    PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT,
    PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME,
    PROJECT_INSTRUCTIONS_FILENAME,
)
from ppt_agent.ppt_master_runner import (
    PPT_MASTER_RUNNER_RESULT_ARTIFACT,
    PPT_MASTER_RUNNER_RESULT_FILENAME,
)
from ppt_agent.runtime import sanitize_error_message
from ppt_agent.v2.orchestrator import (
    BuildRequest as V2BuildRequest,
    BuildResult as V2BuildResult,
    build_deck as build_v2_deck,
)
from ppt_agent.v2.providers import (
    UsageMeter as V2UsageMeter,
    build_client as build_v2_client,
    ensure_pricing as ensure_v2_pricing,
    provider_config_from_env as v2_provider_config_from_env,
)
from ppt_agent.v2.revise import revise_deck as revise_v2_deck
from ppt_agent.v2.search import default_search_provider

# Retain the existing job log category for web consumers.
logger = logging.getLogger("ppt_agent.api")

PPT_MASTER_RUN_PROMPT_ARTIFACT = "ppt_master_run_prompt"
PPT_MASTER_MANIFEST_ARTIFACT = "ppt_master_package_manifest"
PPT_MASTER_README_ARTIFACT = "ppt_master_package_README"


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


DEFAULT_V2_CONCURRENCY = 8
DEFAULT_V2_BUDGET_USD = 15.0


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
    interview_id: str | None = Field(default=None, min_length=1, max_length=64)


class CreateLongDeckJobRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(default=30, ge=4, le=100)
    language: str = Field(default="zh-CN", min_length=1)
    deck_type: str = Field(default="technical_product_share", min_length=1)
    user_requirements: str = Field(..., min_length=1)
    batch_size: int = Field(default=2, ge=1, le=10)
    max_batch_attempts: int = Field(default=1, ge=1, le=3)
    interview_id: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    layout_engine: Literal["typeset", "free"] = "typeset"
    style_profile: Literal["consulting", "launch", "training", "corporate"] | None = None
    enable_search: bool = False


def create_v2_model_client():
    config = v2_provider_config_from_env()
    config.resolved_api_key()
    config, _ = ensure_v2_pricing(config)
    usage = V2UsageMeter(budget_usd=env_float("PPT_AGENT_V2_BUDGET_USD", DEFAULT_V2_BUDGET_USD))
    return build_v2_client(config, usage=usage)


def artifact_name_for_path(output_dir: Path, artifact_path: Path) -> str | None:
    try:
        relative_path = artifact_path.relative_to(output_dir)
    except ValueError:
        relative_path = artifact_path

    if relative_path.parts and relative_path.parts[0] == "checkpoints":
        return None
    if relative_path == Path("ppt_master_package/source.md"):
        return None
    if relative_path == Path(PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME):
        return PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT
    if artifact_path.name == PROJECT_INSTRUCTIONS_FILENAME:
        return PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT
    ppt_master_package_names = {
        Path("ppt_master_package/run_prompt.md"): PPT_MASTER_RUN_PROMPT_ARTIFACT,
        Path("ppt_master_package/README.md"): PPT_MASTER_README_ARTIFACT,
        Path("ppt_master_package/manifest.json"): PPT_MASTER_MANIFEST_ARTIFACT,
        Path(PPT_MASTER_EXECUTION_PLAN_FILENAME): PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
        Path(PPT_MASTER_RUNNER_RESULT_FILENAME): PPT_MASTER_RUNNER_RESULT_ARTIFACT,
        Path("ppt_master_output/generated_by_ppt_master.pptx"): PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
        Path("ppt_master_output/generation_notes.md"): PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
        Path(f"ppt_master_output/{PPT_MASTER_OUTPUT_MANIFEST_FILENAME}"): PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    }
    return ppt_master_package_names.get(relative_path, artifact_path.stem)


def register_job_artifacts(store: JobStore, job_id: str, output_dir: Path) -> None:
    for artifact_path in sorted(output_dir.rglob("*")):
        if not artifact_path.is_file() or artifact_path.suffix.lower() not in {".json", ".pptx", ".md"}:
            continue
        artifact_name = artifact_name_for_path(output_dir, artifact_path)
        if artifact_name is None:
            continue
        suffix = artifact_path.suffix.lower()
        kind: ArtifactKind = "pptx" if suffix == ".pptx" else "md" if suffix == ".md" else "json"
        store.add_artifact(job_id, name=artifact_name, kind=kind, path=artifact_path)


def long_deck_request_path(output_dir: Path) -> Path:
    return output_dir / "long_deck_request.json"


def write_long_deck_request_artifact(payload: CreateLongDeckJobRequest, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = long_deck_request_path(output_dir)
    path.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def save_presentation_request_snapshot(
    store: JobStore,
    job_id: str,
    payload: CreateJobRequest | CreateLongDeckJobRequest,
    *,
    resumed_from_job_id: str | None = None,
) -> None:
    slide_count = payload.slides if isinstance(payload, CreateJobRequest) else payload.slide_count
    store.save_presentation_request(
        job_id,
        topic=payload.topic,
        audience=payload.audience,
        user_requirements=payload.user_requirements or "",
        slide_count=slide_count,
        interview_id=payload.interview_id,
        resumed_from_job_id=resumed_from_job_id,
    )


class V2JobCancelled(RuntimeError):
    pass


def v2_long_deck_prompt(payload: CreateLongDeckJobRequest) -> str:
    return (
        f"主题：{payload.topic}\n"
        f"目标观众：{payload.audience}\n"
        f"演示要求：{payload.user_requirements}"
    )


def read_v2_qa_score(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total_pages = int(payload.get("total_pages") or 0)
        pages_with_errors = int(payload.get("pages_with_errors") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if total_pages <= 0:
        return None
    return max(0, min(100, round((total_pages - pages_with_errors) / total_pages * 100)))


def run_v2_long_deck_job(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    client,
    payload: CreateLongDeckJobRequest,
    *,
    output_dir_override: Path | None = None,
    resume: bool = False,
    document_paths: list[str] | None = None,
    image_paths: list[str] | None = None,
) -> None:
    output_dir = output_dir_override or jobs_root / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    store.update_job(job_id, status="running", current_stage="v2_intake")
    store.update_long_deck_progress(
        job_id,
        total_batches=payload.slide_count,
        completed_batches=0,
        failed_batches=0,
    )

    content_ready: set[int] = set()
    typeset_ready: set[int] = set()

    def progress_logger(message: str) -> None:
        if store.is_cancel_requested(job_id):
            raise V2JobCancelled("v2 long deck generation was cancelled.")

        current_stage: str | None = None
        completed_pages: int | None = None
        current_page: str | None = None
        design_match = re.match(r"^\[design\] (\d+)/(\d+) content pages done$", message)
        content_match = re.match(r"^\[content\] page (\d+) ready$", message)
        typeset_match = re.match(r"^\[typeset\] page (\d+) done$", message)
        stage_match = re.match(r"^\[stage\] ([a-z_]+) finished", message)
        if content_match or typeset_match:
            page_number = int((content_match or typeset_match).group(1))
            if content_match:
                content_ready.add(page_number)
                current_stage = "v2_content_generation"
            else:
                typeset_ready.add(page_number)
                current_stage = "v2_typesetting"
            completed_pages = min(max(len(content_ready), len(typeset_ready)), payload.slide_count)
            current_page = f"page_{page_number:03d}"
        elif design_match:
            completed_pages = min(int(design_match.group(1)), payload.slide_count)
            current_stage = f"generating_v2_page_{completed_pages}_of_{payload.slide_count}"
            current_page = f"page_{completed_pages:03d}"
        elif message.startswith("[brief]"):
            current_stage = "v2_brief"
        elif message.startswith("[theme]"):
            current_stage = "v2_theme"
        elif message.startswith("[outline]"):
            current_stage = "v2_outline"
        elif stage_match:
            stage = stage_match.group(1)
            current_stage = {
                "intake": "v2_brief",
                "brief": "v2_theme",
                "theme": "v2_outline",
                "outline": "v2_page_briefs",
                "page_briefs": ("v2_content_generation"
                                if payload.layout_engine == "typeset" else "v2_page_designs"),
                "typeset_pages": "v2_rendering_complete",
                "page_designs": "v2_quality_gate",
                "assemble_qa": "v2_quality_gate",
                "render": "v2_rendering_complete",
            }.get(stage, f"v2_{stage}")

        if current_stage is not None or completed_pages is not None:
            store.update_long_deck_progress(
                job_id,
                current_stage=current_stage,
                total_batches=payload.slide_count,
                completed_batches=completed_pages,
                current_batch=current_page,
            )
        logger.info(
            "v2_long_deck_job_stage %s",
            json.dumps(
                {
                    "job_id": job_id,
                    "message": message,
                    "current_stage": current_stage,
                    "slide_count": payload.slide_count,
                },
                ensure_ascii=False,
            ),
        )

    try:
        write_long_deck_request_artifact(payload, output_dir)
        result: V2BuildResult = build_v2_deck(
            V2BuildRequest(
                prompt=v2_long_deck_prompt(payload),
                page_count=payload.slide_count,
                language=payload.language,
                layout_engine=payload.layout_engine,
                style_profile=payload.style_profile,
                enable_search=payload.enable_search,
                source_paths=document_paths or [],
                image_paths=image_paths or [],
                output_dir=str(output_dir),
                deck_name="generated_long_deck_v2",
                resume=resume,
                concurrency=env_int("PPT_AGENT_V2_CONCURRENCY", DEFAULT_V2_CONCURRENCY),
                budget_usd=env_float("PPT_AGENT_V2_BUDGET_USD", DEFAULT_V2_BUDGET_USD),
                qa_gate="strict",
            ),
            client,
            search_provider=default_search_provider() if payload.enable_search else None,
            progress=progress_logger,
        )
        register_job_artifacts(store, job_id, output_dir)
        qa_score = read_v2_qa_score(Path(result.qa_report_path))
        store.update_long_deck_progress(
            job_id,
            total_batches=payload.slide_count,
            completed_batches=result.page_count,
            failed_batches=0,
            current_batch=f"page_{result.page_count:03d}",
        )
        if result.status in {"quality_gate_failed", "completed_with_qa_errors"}:
            store.update_job(
                job_id,
                status="failed_quality_gate",
                error_message="The full-deck quality check failed, so no PPTX was released.",
                accepted=False,
                qa_score=qa_score,
                current_stage="v2_quality_gate_failed",
            )
            return
        store.update_job(
            job_id,
            status="succeeded",
            error_message=None,
            accepted=True,
            qa_score=qa_score,
            current_stage="v2_completed",
        )
    except V2JobCancelled as exc:
        if output_dir.exists():
            register_job_artifacts(store, job_id, output_dir)
        store.update_job(
            job_id,
            status="cancelled",
            error_message=str(exc),
            accepted=False,
            current_stage="v2_cancelled",
        )
    except Exception as exc:
        error_message = sanitize_error_message(exc)
        logger.error("v2_long_deck_job_failed job_id=%s error=%s", job_id, error_message)
        if output_dir.exists():
            register_job_artifacts(store, job_id, output_dir)
        store.update_job(
            job_id,
            status="failed",
            error_message=error_message,
            accepted=False,
            current_stage="v2_failed",
        )


def register_missing_job_artifacts(store: JobStore, job_id: str, output_dir: Path) -> None:
    """Register artifacts that appeared after the original run (idempotent)."""

    existing = {artifact.name for artifact in store.list_artifacts(job_id)}
    for artifact_path in sorted(output_dir.rglob("*")):
        if not artifact_path.is_file() or artifact_path.suffix.lower() not in {".json", ".pptx", ".md"}:
            continue
        artifact_name = artifact_name_for_path(output_dir, artifact_path)
        if artifact_name is None or artifact_name in existing:
            continue
        suffix = artifact_path.suffix.lower()
        kind: ArtifactKind = "pptx" if suffix == ".pptx" else "md" if suffix == ".md" else "json"
        store.add_artifact(job_id, name=artifact_name, kind=kind, path=artifact_path)


def run_deck_revision(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    revision_id: str,
    client,
    message: str,
    page_numbers: list[int] | None,
    *,
    document_paths: list[str] | None = None,
    image_paths: list[str] | None = None,
) -> None:
    output_dir = jobs_root / job_id

    def progress_logger(text: str) -> None:
        logger.info(
            "deck_revision_stage %s",
            json.dumps(
                {"job_id": job_id, "revision_id": revision_id, "message": text},
                ensure_ascii=False,
            ),
        )

    try:
        result = revise_v2_deck(
            output_dir=output_dir,
            deck_name="generated_long_deck_v2",
            message=message,
            client=client,
            selected_pages=page_numbers,
            attachment_paths=[*(document_paths or []), *(image_paths or [])],
            concurrency=env_int("PPT_AGENT_V2_CONCURRENCY", DEFAULT_V2_CONCURRENCY),
            progress=progress_logger,
        )
        store.update_deck_revision(
            revision_id,
            status="succeeded",
            reply=result.reply,
            revised_pages_json=json.dumps(result.revised_pages),
        )
        register_missing_job_artifacts(store, job_id, output_dir)
        job = store.get_job(job_id)
        if (
            job is not None
            and job.status == "failed_quality_gate"
            and result.qa_error_pages == 0
            and result.pptx_path
        ):
            store.update_job(
                job_id,
                status="succeeded",
                error_message=None,
                accepted=True,
                current_stage="v2_completed",
            )
    except Exception as exc:
        error_message = sanitize_error_message(exc)
        logger.error(
            "deck_revision_failed job_id=%s revision_id=%s error=%s",
            job_id,
            revision_id,
            error_message,
        )
        store.update_deck_revision(revision_id, status="failed", error_message=error_message)


def start_v2_job(
    store: JobStore,
    jobs_root: Path,
    client,
    payload: CreateLongDeckJobRequest,
    *,
    submit: Callable[..., None],
    document_paths: list[str] | None = None,
    image_paths: list[str] | None = None,
) -> JobRecord:
    """Create a v2 job and submit its existing executor to the caller's scheduler."""
    job = store.create_job(job_type="long_deck_v2")
    save_presentation_request_snapshot(store, job.job_id, payload)
    store.update_long_deck_progress(
        job.job_id, current_stage="v2_intake", total_batches=payload.slide_count,
        completed_batches=0, failed_batches=0,
    )
    submit(run_v2_long_deck_job, store, jobs_root, job.job_id, client, payload,
           document_paths=document_paths, image_paths=image_paths)
    return job


def start_deck_revision(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    client,
    message: str,
    page_numbers: list[int] | None,
    *,
    submit: Callable[..., None],
    document_paths: list[str] | None = None,
    image_paths: list[str] | None = None,
) -> DeckRevisionRecord:
    """Create a revision and submit it after the caller has validated the request."""
    record = store.create_deck_revision(job_id=job_id, message=message)
    submit(run_deck_revision, store, jobs_root, job_id, record.revision_id,
           client, message, page_numbers,
           document_paths=document_paths, image_paths=image_paths)
    return record
