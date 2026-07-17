"""Private beta FastAPI backend for job-based deck builds."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from ppt_agent.generation import DeckGenerationRequest
from ppt_agent.job_store import (
    ArtifactKind,
    ArtifactRecord,
    JobRecord,
    JobStatus,
    JobStore,
    PresentationHistoryRecord,
)
from ppt_agent.long_deck_orchestrator import LongDeckRunReport, LongDeckRunRequest, run_long_deck_batch_generation
from ppt_agent.long_deck_render import LongDeckRenderReport, render_long_deck_ir_to_pptx
from ppt_agent.models import StrictModel
from ppt_agent.pipeline import BuildPipelineRequest, run_build_pipeline
from ppt_agent.ppt_master_execution import (
    PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
    PPT_MASTER_EXECUTION_PLAN_FILENAME,
    PptMasterExecutionPlan,
    prepare_ppt_master_execution,
)
from ppt_agent.ppt_master_integration import PPT_MASTER_RECOVERY_WARNING, create_ppt_master_job_package
from ppt_agent.ppt_master_output import (
    PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
    PPT_MASTER_OUTPUT_MANIFEST_FILENAME,
    PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
    PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
    PptMasterOutputManifest,
    detect_ppt_master_output,
    register_ppt_master_output_artifacts,
)
from ppt_agent.ppt_master_project import (
    PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT,
    PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT,
    PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME,
    PROJECT_INSTRUCTIONS_FILENAME,
    PptMasterVisualProject,
    bootstrap_ppt_master_visual_project,
    read_ppt_master_visual_project_manifest,
    register_ppt_master_visual_project_artifacts,
)
from ppt_agent.ppt_master_runner import (
    PPT_MASTER_RUNNER_RESULT_ARTIFACT,
    PPT_MASTER_RUNNER_RESULT_FILENAME,
    PptMasterRunnerResult,
    read_ppt_master_runner_result,
    register_ppt_master_runner_result_artifact,
    run_ppt_master_local_export,
)
from ppt_agent.runtime import StageEvent, sanitize_error_message, observed_stage
from ppt_agent.requirements_interview import (
    InterviewMessage,
    PresentationInterviewDecision,
    PresentationInterviewState,
    run_requirements_interview_turn,
)
from ppt_agent.v2.orchestrator import (
    BuildRequest as V2BuildRequest,
    BuildResult as V2BuildResult,
    build_deck as build_v2_deck,
    plan_deck as plan_v2_deck,
)
from ppt_agent.v2.planning import (
    ContentBrief as V2ContentBrief,
    EditableDeckPlan,
    editable_plan_from_skeleton,
    skeleton_from_editable_plan,
)
from ppt_agent.v2.revise import RevisionError, revise_deck as revise_v2_deck
from ppt_agent.v2.design import ThemeSpec as V2ThemeSpec
from ppt_agent.v2.ir import DeckDesign as V2DeckDesign
from ppt_agent.v2.ir import PageDesign as V2PageDesign
from ppt_agent.v2.preview import page_to_embedded_html as v2_page_to_embedded_html
from ppt_agent.v2.providers import (
    ProviderError as V2ProviderError,
    UsageMeter as V2UsageMeter,
    build_client as build_v2_client,
    ensure_pricing as ensure_v2_pricing,
    provider_config_from_env as v2_provider_config_from_env,
)


DEFAULT_DATA_DIR = Path("data")
DEFAULT_MODEL = "gpt-5.5"
JOB_TIMEOUT_SECONDS = 600
LLM_TIMEOUT_SECONDS = 120
DEFAULT_THEME_PATH = Path("examples/theme.json")
DEFAULT_ASSETS_DIR = Path("examples")

logger = logging.getLogger(__name__)

PPT_MASTER_SOURCE_ARTIFACT = "ppt_master_source"
PPT_MASTER_RUN_PROMPT_ARTIFACT = "ppt_master_run_prompt"
PPT_MASTER_MANIFEST_ARTIFACT = "ppt_master_package_manifest"
PPT_MASTER_README_ARTIFACT = "ppt_master_package_README"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


DEFAULT_LONG_DECK_JOB_TIMEOUT_SECONDS = 3600
DEFAULT_V2_CONCURRENCY = 8
DEFAULT_V2_BUDGET_USD = 15.0


def _long_deck_job_timeout_seconds() -> int:
    return _env_int("LONG_DECK_JOB_TIMEOUT_SECONDS", DEFAULT_LONG_DECK_JOB_TIMEOUT_SECONDS)


# Keep browser assets outside this API module so UI changes remain reviewable and packageable.
WEBUI_DIR = Path(__file__).resolve().parent / "webui"


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


def _uses_v2_generation(payload: CreateLongDeckJobRequest) -> bool:
    return payload.deck_type == "visual_design_v2" or payload.slide_count != 30


class DeckPlanResponse(StrictModel):
    plan_id: str
    status: Literal["planning", "ready", "failed", "confirmed"]
    request: CreateLongDeckJobRequest
    plan: EditableDeckPlan | None = None
    total_pages: int | None = None
    error_message: str | None = None
    job_id: str | None = None
    created_at: str
    updated_at: str


class UpdateDeckPlanRequest(StrictModel):
    plan: EditableDeckPlan


class ConfirmDeckPlanRequest(StrictModel):
    plan: EditableDeckPlan | None = None


class CreateDeckRevisionRequest(StrictModel):
    message: str = Field(..., min_length=1, max_length=4000)
    page_numbers: list[int] | None = None


class DeckRevisionResponse(StrictModel):
    revision_id: str
    job_id: str
    status: Literal["running", "succeeded", "failed"]
    message: str
    reply: str = ""
    revised_pages: list[int] = Field(default_factory=list)
    error_message: str | None = None
    created_at: str
    updated_at: str


class DeckRevisionListResponse(StrictModel):
    items: list[DeckRevisionResponse]


class CreateJobResponse(StrictModel):
    job_id: str
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "failed_quality_gate",
        "partial_failed_quality_gate",
        "cancelled",
        "partial_cancelled",
    ]


class PptMasterPackageResponse(StrictModel):
    generated: bool
    package_mode: Literal["normal", "recovery"] | None = None
    reason: Literal[
        "job_timeout_before_merge",
        "batch_generation_failed_before_merge",
        "quality_gate_failed_recovery_generated",
        "normal_generated",
        "not_applicable",
    ] = "not_applicable"
    available: bool | None = None
    is_expected_repo: bool | None = None
    source_quality_gate_status: str | None = None
    warning: str | None = None
    ppt_master_root: str | None = None
    missing_paths: list[str] = Field(default_factory=list)
    source_artifact_id: str | None = None
    run_prompt_artifact_id: str | None = None
    manifest_artifact_id: str | None = None
    readme_artifact_id: str | None = None
    message: str


class PptMasterOutputResponse(StrictModel):
    detected: bool
    pptx_artifact_id: str | None = None
    notes_artifact_id: str | None = None
    manifest_artifact_id: str | None = None
    output_dir: str | None = None
    slide_count: int | None = Field(default=None, ge=0)
    generation_status: str | None = None
    message: str


class PptMasterExecutionResponse(StrictModel):
    status: str
    plan_artifact_id: str | None = None
    project_dir: str | None = None
    output_dir: str | None = None
    expected_pptx_path: str | None = None
    suggested_steps: list[str] = Field(default_factory=list)
    message: str


class PptMasterVisualProjectResponse(StrictModel):
    status: str
    manifest_artifact_id: str | None = None
    instructions_artifact_id: str | None = None
    project_dir: str | None = None
    project_source_path: str | None = None
    project_prompt_path: str | None = None
    expected_svg_output_dir: str | None = None
    expected_svg_final_dir: str | None = None
    expected_pptx_path: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    message: str
    warnings: list[str] = Field(default_factory=list)


class PptMasterRunnerResponse(StrictModel):
    status: str
    result_artifact_id: str | None = None
    project_dir: str | None = None
    output_dir: str | None = None
    pptx_path: str | None = None
    slide_count: int | None = Field(default=None, ge=0)
    registered: bool = False
    requires_external_ai_generation: bool = False
    message: str
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class JobResponse(JobRecord):
    ppt_master_package: PptMasterPackageResponse | None = None
    ppt_master_execution: PptMasterExecutionResponse | None = None
    ppt_master_visual_project: PptMasterVisualProjectResponse | None = None
    ppt_master_output: PptMasterOutputResponse | None = None
    ppt_master_runner: PptMasterRunnerResponse | None = None


class ArtifactResponse(StrictModel):
    artifact_id: str
    name: str
    kind: ArtifactKind
    download_url: str


class ArtifactListResponse(StrictModel):
    artifacts: list[ArtifactResponse]


class PresentationHistoryItem(StrictModel):
    job_id: str
    status: JobStatus
    job_type: str | None = None
    topic: str
    audience: str | None = None
    user_requirements: str | None = None
    slide_count: int | None = Field(default=None, ge=1, le=100)
    created_at: str
    updated_at: str
    accepted: bool | None = None
    qa_score: int | None = Field(default=None, ge=0, le=100)
    pptx_artifact_id: str | None = None
    pptx_artifact_name: str | None = None
    pptx_download_url: str | None = None


class PresentationHistoryResponse(StrictModel):
    items: list[PresentationHistoryItem]
    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=100)
    offset: int = Field(..., ge=0)


class StartPresentationInterviewRequest(StrictModel):
    message: str = Field(..., min_length=1, max_length=6000)


class ContinuePresentationInterviewRequest(StrictModel):
    message: str = Field(..., min_length=1, max_length=6000)
    selected_option_id: str | None = Field(default=None, min_length=1, max_length=40)


def _create_chat_model():
    from langchain_openai import ChatOpenAI

    kwargs = {"model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL)}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return ChatOpenAI(**kwargs)


def _create_interview_chat_model():
    from langchain_openai import ChatOpenAI

    kwargs = {
        "model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        "reasoning_effort": os.getenv("PPT_AGENT_INTERVIEW_REASONING_EFFORT", "low"),
        "max_completion_tokens": _env_int("PPT_AGENT_INTERVIEW_MAX_TOKENS", 1800),
        "max_retries": 1,
    }
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return ChatOpenAI(**kwargs)


def _create_v2_model_client():
    config = v2_provider_config_from_env()
    config.resolved_api_key()
    config, _ = ensure_v2_pricing(config)
    usage = V2UsageMeter(budget_usd=_env_float("PPT_AGENT_V2_BUDGET_USD", DEFAULT_V2_BUDGET_USD))
    return build_v2_client(config, usage=usage)


def _artifact_response(artifact: ArtifactRecord) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=artifact.artifact_id,
        name=artifact.name,
        kind=artifact.kind,
        download_url=f"/api/artifacts/{artifact.artifact_id}",
    )


def _presentation_history_item(record: PresentationHistoryRecord) -> PresentationHistoryItem:
    pptx_available = record.pptx_path is not None and record.pptx_path.is_file()
    return PresentationHistoryItem(
        job_id=record.job_id,
        status=record.status,
        job_type=record.job_type,
        topic=record.topic or f"历史演示 {record.job_id[:8]}",
        audience=record.audience or None,
        user_requirements=record.user_requirements or None,
        slide_count=record.slide_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
        accepted=record.accepted,
        qa_score=record.qa_score,
        pptx_artifact_id=record.pptx_artifact_id if pptx_available else None,
        pptx_artifact_name=record.pptx_artifact_name if pptx_available else None,
        pptx_download_url=(
            f"/api/artifacts/{record.pptx_artifact_id}"
            if pptx_available and record.pptx_artifact_id is not None
            else None
        ),
    )


def _presentation_interview_state_from_record(record) -> PresentationInterviewState:
    return PresentationInterviewState(
        interview_id=record.interview_id,
        status=record.status,
        messages=[InterviewMessage.model_validate(item) for item in json.loads(record.messages_json)],
        decision=PresentationInterviewDecision.model_validate_json(record.decision_json),
        turn_count=record.turn_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _save_presentation_interview_state(
    store: JobStore,
    *,
    interview_id: str,
    messages: list[InterviewMessage],
    decision: PresentationInterviewDecision,
    turn_count: int,
) -> PresentationInterviewState:
    record = store.save_presentation_interview(
        interview_id=interview_id,
        status=decision.status,
        messages_json=json.dumps([message.model_dump() for message in messages], ensure_ascii=False),
        decision_json=decision.model_dump_json(),
        turn_count=turn_count,
    )
    return _presentation_interview_state_from_record(record)


def _read_ppt_master_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_ppt_master_output_manifest(path: Path) -> PptMasterOutputManifest | None:
    try:
        return PptMasterOutputManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_ppt_master_execution_plan(path: Path) -> PptMasterExecutionPlan | None:
    try:
        return PptMasterExecutionPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_ppt_master_runner_result(path: Path) -> PptMasterRunnerResult | None:
    return read_ppt_master_runner_result(path)


def _read_ppt_master_visual_project(path: Path) -> PptMasterVisualProject | None:
    return read_ppt_master_visual_project_manifest(path)


def _ensure_artifact(
    store: JobStore,
    *,
    job_id: str,
    name: str,
    kind: ArtifactKind,
    path: Path,
) -> ArtifactRecord:
    resolved_path = path.expanduser().resolve(strict=False)
    for artifact in reversed(store.list_artifacts(job_id)):
        if artifact.name != name:
            continue
        if artifact.kind == kind and artifact.path.expanduser().resolve(strict=False) == resolved_path:
            return artifact
    return store.add_artifact(job_id, name=name, kind=kind, path=resolved_path)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _timeout_seconds_for_stage(stage: str | None) -> int:
    if stage and (
        stage.startswith(("generating_batch_", "generating_v2_page_", "v2_"))
        or stage
        in {
            "preparing_long_deck_plan",
            "merging_long_deck_ir",
            "running_long_deck_qa",
            "running_long_deck_quality_gate",
            "rendering_long_deck_pptx",
            "cancel_requested",
        }
    ):
        return _long_deck_job_timeout_seconds()
    return JOB_TIMEOUT_SECONDS


def _ppt_master_package_response(store: JobStore, job: JobRecord) -> PptMasterPackageResponse | None:
    if job.job_type != "long_deck":
        return None

    artifacts_by_name = {artifact.name: artifact for artifact in store.list_artifacts(job.job_id)}
    source_artifact = artifacts_by_name.get(PPT_MASTER_SOURCE_ARTIFACT)
    run_prompt_artifact = artifacts_by_name.get(PPT_MASTER_RUN_PROMPT_ARTIFACT)
    manifest_artifact = artifacts_by_name.get(PPT_MASTER_MANIFEST_ARTIFACT)
    readme_artifact = artifacts_by_name.get(PPT_MASTER_README_ARTIFACT)

    if source_artifact and run_prompt_artifact and manifest_artifact and readme_artifact:
        manifest = _read_ppt_master_manifest(manifest_artifact.path)
        is_expected_repo = manifest.get("is_expected_repo")
        if not isinstance(is_expected_repo, bool):
            is_expected_repo = None
        ppt_master_root = manifest.get("ppt_master_root")
        if not isinstance(ppt_master_root, str):
            ppt_master_root = None
        available = manifest.get("is_available") is True
        package_mode = manifest.get("package_mode")
        if package_mode not in {"normal", "recovery"}:
            package_mode = "normal"
        source_quality_gate_status = _optional_str(manifest.get("source_quality_gate_status"))
        warning = _optional_str(manifest.get("warning"))
        if package_mode == "recovery":
            message = (
                "质量门禁未通过，因此不会生成旧 renderer PPTX。"
                "但已生成 PPT Master recovery package，可用于交给本地 ppt-master 重新生成。"
            )
            reason = "quality_gate_failed_recovery_generated"
        else:
            message = (
                "PPT Master handoff package is ready. ppt-agent does not run ppt-master automatically."
                if available
                else "PPT Master handoff package is ready, but local ppt-master was not detected."
            )
            reason = "normal_generated"
        return PptMasterPackageResponse(
            generated=True,
            reason=reason,
            available=available,
            is_expected_repo=is_expected_repo,
            package_mode=package_mode,
            source_quality_gate_status=source_quality_gate_status,
            warning=warning,
            ppt_master_root=ppt_master_root,
            missing_paths=_string_list(manifest.get("missing_paths")),
            source_artifact_id=source_artifact.artifact_id,
            run_prompt_artifact_id=run_prompt_artifact.artifact_id,
            manifest_artifact_id=manifest_artifact.artifact_id,
            readme_artifact_id=readme_artifact.artifact_id,
            message=message,
        )

    if job.status in {"failed_quality_gate", "partial_failed_quality_gate"}:
        return PptMasterPackageResponse(
            generated=False,
            reason="not_applicable",
            available=None,
            package_mode=None,
            source_quality_gate_status=job.status,
            missing_paths=[],
            message="质量门禁失败，但当前没有可用的 PPT Master package 状态记录。",
        )

    if job.status == "failed" and job.current_stage and job.current_stage.startswith("generating_batch_"):
        timed_out = False
        if job.error_message:
            timeout_seconds = _timeout_seconds_for_stage(job.current_stage)
            timed_out = f"timed out after {timeout_seconds:g} seconds" in job.error_message.lower()
        reason = "job_timeout_before_merge" if timed_out else "batch_generation_failed_before_merge"
        return PptMasterPackageResponse(
            generated=False,
            reason=reason,
            available=None,
            is_expected_repo=None,
            package_mode=None,
            ppt_master_root=None,
            missing_paths=[],
            message=(
                "Long deck generation timed out before a complete merged Deck IR was available. "
                "Resume the job to continue from the last completed batch. PPT Master package will be generated "
                "after merge or quality gate evaluation."
                if timed_out
                else "Long deck batch generation stopped before a complete merged Deck IR was available. "
                "Resume the job to continue from the last completed batch. PPT Master package will be generated "
                "after merge or quality gate evaluation."
            ),
        )

    if job.status in {"pending", "running"}:
        message = "PPT Master package will be generated after the long deck passes the quality gate."
    else:
        message = "PPT Master package has not been generated for this job."
    return PptMasterPackageResponse(
        generated=False,
        reason="not_applicable",
        available=None,
        is_expected_repo=None,
        package_mode=None,
        ppt_master_root=None,
        missing_paths=[],
        message=message,
    )


def _ppt_master_output_dir_for_job(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / job_id / "ppt_master_output"


def _ppt_master_execution_plan_path_for_job(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / job_id / PPT_MASTER_EXECUTION_PLAN_FILENAME


def _ppt_master_runner_result_path_for_job(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / job_id / PPT_MASTER_RUNNER_RESULT_FILENAME


def _ppt_master_visual_project_manifest_path_for_job(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / job_id / PPT_MASTER_VISUAL_PROJECT_MANIFEST_FILENAME


def _ppt_master_execution_response(
    store: JobStore,
    jobs_root: Path,
    job: JobRecord,
) -> PptMasterExecutionResponse | None:
    if job.job_type != "long_deck":
        return None

    artifacts_by_name = {artifact.name: artifact for artifact in store.list_artifacts(job.job_id)}
    plan_artifact = artifacts_by_name.get(PPT_MASTER_EXECUTION_PLAN_ARTIFACT)
    plan_path = _ppt_master_execution_plan_path_for_job(jobs_root, job.job_id)
    plan = None
    if plan_artifact is not None:
        plan = _read_ppt_master_execution_plan(plan_artifact.path)
    elif plan_path.is_file():
        plan = _read_ppt_master_execution_plan(plan_path)
        if plan is not None:
            plan_artifact = _ensure_artifact(
                store,
                job_id=job.job_id,
                name=PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
                kind="json",
                path=plan_path,
            )

    if plan is not None:
        return PptMasterExecutionResponse(
            status=plan.status,
            plan_artifact_id=plan_artifact.artifact_id if plan_artifact is not None else None,
            project_dir=str(plan.project_dir) if plan.project_dir is not None else None,
            output_dir=str(plan.output_dir),
            expected_pptx_path=str(plan.expected_pptx_path),
            suggested_steps=plan.suggested_steps,
            message=_ppt_master_execution_message(plan.status),
        )

    output_dir = _ppt_master_output_dir_for_job(jobs_root, job.job_id)
    return PptMasterExecutionResponse(
        status="not_prepared",
        plan_artifact_id=None,
        project_dir=None,
        output_dir=str(output_dir),
        expected_pptx_path=str(output_dir / "generated_by_ppt_master.pptx"),
        suggested_steps=[
            f"Call POST /api/long-deck-jobs/{job.job_id}/prepare-ppt-master-execution to create an execution plan."
        ],
        message="PPT Master execution plan has not been prepared for this job.",
    )


def _ppt_master_execution_message(status: str) -> str:
    if status == "waiting_for_external_ppt_master_run":
        return (
            "PPT Master package 已准备好，但当前阶段不会自动运行 ppt-master。"
            "请让本地 AI IDE 使用 run_prompt.md 执行，完成后系统可注册输出。"
        )
    if status == "output_detected":
        return "检测到 PPT Master 输出，可注册或已注册。"
    if status == "ppt_master_unavailable":
        return "PPT Master package 已准备好，但本地 ppt-master 不可用。请检查 PPT_MASTER_DIR。"
    if status == "missing_package":
        return "当前 job 缺少完整 ppt_master_package，因此不能准备执行桥。"
    return "PPT Master execution plan has not been prepared for this job."


def _ppt_master_visual_project_message(status: str) -> str:
    if status == "created":
        return "PPT Master visual project scaffold 已创建。下一步是在本地 AI IDE / ppt-master skill 中生成 SVG。"
    if status == "already_exists":
        return "PPT Master visual project scaffold 已存在。可以继续在该目录补全 SVG。"
    if status == "missing_package":
        return "当前 job 缺少 ppt_master_package/source.md 或 run_prompt.md，不能创建 visual project。"
    if status == "ppt_master_unavailable":
        return "本地 ppt-master 不可用。请检查 PPT_MASTER_DIR 或 --ppt-master-dir。"
    if status == "failed":
        return "PPT Master visual project bootstrap 失败，请查看 warnings。"
    return "PPT Master visual project scaffold has not been bootstrapped for this job."


def _visual_project_response_from_manifest(
    project: PptMasterVisualProject,
    *,
    manifest_artifact_id: str | None = None,
    instructions_artifact_id: str | None = None,
) -> PptMasterVisualProjectResponse:
    return PptMasterVisualProjectResponse(
        status=project.status,
        manifest_artifact_id=manifest_artifact_id,
        instructions_artifact_id=instructions_artifact_id,
        project_dir=str(project.project_dir),
        project_source_path=str(project.project_source_path),
        project_prompt_path=str(project.project_prompt_path),
        expected_svg_output_dir=str(project.expected_svg_output_dir),
        expected_svg_final_dir=str(project.expected_svg_final_dir),
        expected_pptx_path=str(project.expected_pptx_path),
        next_steps=project.next_steps,
        message=_ppt_master_visual_project_message(project.status),
        warnings=project.warnings,
    )


def _ppt_master_visual_project_response(
    store: JobStore,
    jobs_root: Path,
    job: JobRecord,
) -> PptMasterVisualProjectResponse | None:
    if job.job_type != "long_deck":
        return None

    artifacts_by_name = {artifact.name: artifact for artifact in store.list_artifacts(job.job_id)}
    manifest_artifact = artifacts_by_name.get(PPT_MASTER_VISUAL_PROJECT_MANIFEST_ARTIFACT)
    instructions_artifact = artifacts_by_name.get(PPT_MASTER_PROJECT_INSTRUCTIONS_ARTIFACT)
    manifest_path = _ppt_master_visual_project_manifest_path_for_job(jobs_root, job.job_id)
    project = None
    if manifest_artifact is not None:
        project = _read_ppt_master_visual_project(manifest_artifact.path)
    elif manifest_path.is_file():
        project = _read_ppt_master_visual_project(manifest_path)
        if project is not None:
            registration = register_ppt_master_visual_project_artifacts(
                store,
                job_id=job.job_id,
                job_dir=jobs_root / job.job_id,
            )
            manifest_artifact = registration.manifest_artifact
            instructions_artifact = registration.instructions_artifact

    if project is not None:
        return _visual_project_response_from_manifest(
            project,
            manifest_artifact_id=manifest_artifact.artifact_id if manifest_artifact is not None else None,
            instructions_artifact_id=instructions_artifact.artifact_id if instructions_artifact is not None else None,
        )

    output_dir = _ppt_master_output_dir_for_job(jobs_root, job.job_id)
    return PptMasterVisualProjectResponse(
        status="not_bootstrapped",
        manifest_artifact_id=None,
        instructions_artifact_id=None,
        project_dir=str(output_dir / "ppt_master_visual_project"),
        project_source_path=None,
        project_prompt_path=None,
        expected_svg_output_dir=str(output_dir / "ppt_master_visual_project" / "svg_output"),
        expected_svg_final_dir=str(output_dir / "ppt_master_visual_project" / "svg_final"),
        expected_pptx_path=str(output_dir / "generated_by_ppt_master.pptx"),
        next_steps=[
            f"Call POST /api/long-deck-jobs/{job.job_id}/bootstrap-ppt-master-project to create the scaffold."
        ],
        message=_ppt_master_visual_project_message("not_bootstrapped"),
    )


def _runner_response_from_result(
    result: PptMasterRunnerResult,
    *,
    result_artifact_id: str | None = None,
) -> PptMasterRunnerResponse:
    return PptMasterRunnerResponse(
        status=result.status,
        result_artifact_id=result_artifact_id,
        project_dir=str(result.project_dir) if result.project_dir is not None else None,
        output_dir=str(result.output_dir),
        pptx_path=str(result.pptx_path) if result.pptx_path is not None else None,
        slide_count=result.slide_count,
        registered=result.registered,
        requires_external_ai_generation=result.status == "requires_external_ai_generation",
        message=result.message,
        warnings=result.warnings,
        errors=result.errors,
    )


def _ppt_master_runner_response(
    store: JobStore,
    jobs_root: Path,
    job: JobRecord,
) -> PptMasterRunnerResponse | None:
    if job.job_type != "long_deck":
        return None

    artifacts_by_name = {artifact.name: artifact for artifact in store.list_artifacts(job.job_id)}
    result_artifact = artifacts_by_name.get(PPT_MASTER_RUNNER_RESULT_ARTIFACT)
    result_path = _ppt_master_runner_result_path_for_job(jobs_root, job.job_id)
    result = None
    if result_artifact is not None:
        result = _read_ppt_master_runner_result(result_artifact.path)
    elif result_path.is_file():
        result = _read_ppt_master_runner_result(result_path)
        if result is not None:
            result_artifact = register_ppt_master_runner_result_artifact(
                store,
                job_id=job.job_id,
                job_dir=jobs_root / job.job_id,
            )

    if result is not None:
        return _runner_response_from_result(
            result,
            result_artifact_id=result_artifact.artifact_id if result_artifact is not None else None,
        )

    return PptMasterRunnerResponse(
        status="not_run",
        result_artifact_id=None,
        project_dir=None,
        output_dir=str(_ppt_master_output_dir_for_job(jobs_root, job.job_id)),
        pptx_path=None,
        slide_count=None,
        registered=False,
        requires_external_ai_generation=False,
        message="PPT Master local export has not been run for this job.",
    )


def _ensure_registered_ppt_master_output(
    store: JobStore,
    jobs_root: Path,
    job: JobRecord,
) -> None:
    if job.job_type != "long_deck":
        return
    artifacts_by_name = {artifact.name: artifact for artifact in store.list_artifacts(job.job_id)}
    if (
        PPT_MASTER_OUTPUT_PPTX_ARTIFACT in artifacts_by_name
        and PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT in artifacts_by_name
    ):
        return
    output_dir = _ppt_master_output_dir_for_job(jobs_root, job.job_id)
    manifest = detect_ppt_master_output(output_dir)
    if not manifest.detected or manifest.pptx_path is None:
        return
    register_ppt_master_output_artifacts(store, job_id=job.job_id, output_dir=output_dir)


def _ppt_master_output_response(
    store: JobStore,
    jobs_root: Path,
    job: JobRecord,
) -> PptMasterOutputResponse | None:
    if job.job_type != "long_deck":
        return None

    _ensure_registered_ppt_master_output(store, jobs_root, job)
    artifacts_by_name = {artifact.name: artifact for artifact in store.list_artifacts(job.job_id)}
    pptx_artifact = artifacts_by_name.get(PPT_MASTER_OUTPUT_PPTX_ARTIFACT)
    notes_artifact = artifacts_by_name.get(PPT_MASTER_OUTPUT_NOTES_ARTIFACT)
    manifest_artifact = artifacts_by_name.get(PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT)

    if pptx_artifact and manifest_artifact:
        manifest = _read_ppt_master_output_manifest(manifest_artifact.path)
        output_dir = None
        slide_count = None
        generation_status = None
        if manifest is not None:
            output_dir = str(manifest.output_dir)
            slide_count = manifest.slide_count
            generation_status = manifest.generation_status
        else:
            output_dir = str(manifest_artifact.path.parent)
        return PptMasterOutputResponse(
            detected=True,
            pptx_artifact_id=pptx_artifact.artifact_id,
            notes_artifact_id=notes_artifact.artifact_id if notes_artifact is not None else None,
            manifest_artifact_id=manifest_artifact.artifact_id,
            output_dir=output_dir,
            slide_count=slide_count,
            generation_status=generation_status or "succeeded",
            message="PPT Master output has been registered for this job.",
        )

    return PptMasterOutputResponse(
        detected=False,
        pptx_artifact_id=None,
        notes_artifact_id=None,
        manifest_artifact_id=None,
        output_dir=None,
        slide_count=None,
        generation_status=None,
        message="No PPT Master output has been registered for this job.",
    )


def _job_response(store: JobStore, jobs_root: Path, job: JobRecord) -> JobResponse:
    data = job.model_dump(mode="python")
    package = _ppt_master_package_response(store, job)
    execution = _ppt_master_execution_response(store, jobs_root, job)
    visual_project = _ppt_master_visual_project_response(store, jobs_root, job)
    output = _ppt_master_output_response(store, jobs_root, job)
    runner = _ppt_master_runner_response(store, jobs_root, job)
    data["ppt_master_package"] = package.model_dump(mode="python") if package is not None else None
    data["ppt_master_execution"] = execution.model_dump(mode="python") if execution is not None else None
    data["ppt_master_visual_project"] = (
        visual_project.model_dump(mode="python") if visual_project is not None else None
    )
    data["ppt_master_output"] = output.model_dump(mode="python") if output is not None else None
    data["ppt_master_runner"] = runner.model_dump(mode="python") if runner is not None else None
    return JobResponse.model_validate(data)


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _natural_slide_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)(?!.*\d)", path.stem)
    return (int(match.group(1)) if match else 0, path.name)


def _ppt_master_preview_slides(job_dir: Path) -> list[Path]:
    output_root = job_dir / "ppt_master_output"
    if not output_root.is_dir():
        return []

    candidates: list[tuple[float, list[Path]]] = []
    for svg_dir in output_root.rglob("svg_final"):
        resolved_dir = svg_dir.resolve(strict=False)
        if not svg_dir.is_dir() or not _path_within(resolved_dir, job_dir):
            continue
        slides = sorted(
            (
                path.resolve()
                for path in svg_dir.glob("*.svg")
                if path.is_file() and _path_within(path.resolve(), job_dir)
            ),
            key=_natural_slide_key,
        )
        if slides:
            candidates.append((svg_dir.stat().st_mtime, slides))

    if not candidates:
        return []
    candidates.sort(key=lambda item: (len(item[1]), item[0]), reverse=True)
    return candidates[0][1]


def _select_visual_highlights(
    scored_pages: list[tuple[int, float, str]],
    *,
    count: int = 3,
) -> list[int]:
    if len(scored_pages) <= count:
        return sorted(page_number for page_number, _, _ in scored_pages)

    ranked = sorted(scored_pages, key=lambda item: (-item[1], item[0]))
    page_span = max(page_number for page_number, _, _ in ranked) - min(
        page_number for page_number, _, _ in ranked
    )
    minimum_gap = max(2, page_span // 12)
    selected = [ranked[0]]
    remaining = ranked[1:]
    while remaining and len(selected) < count:
        used_signatures = {item[2] for item in selected}

        def adjusted_score(candidate: tuple[int, float, str]) -> tuple[float, float, int]:
            page_number, visual_score, signature = candidate
            nearest_distance = min(abs(page_number - chosen[0]) for chosen in selected)
            repeat_penalty = 8.0 if signature in used_signatures else 0.0
            proximity_penalty = max(0, minimum_gap - nearest_distance) * 2.0
            return visual_score - repeat_penalty - proximity_penalty, visual_score, -page_number

        chosen = max(remaining, key=adjusted_score)
        selected.append(chosen)
        remaining.remove(chosen)

    return sorted(page_number for page_number, _, _ in selected)


def _v2_page_visual_score(page: V2PageDesign) -> tuple[float, str]:
    role_score = {
        "cover": -8.0,
        "toc": -3.0,
        "section_divider": 1.0,
        "content": 5.0,
        "quote": 5.0,
        "stats": 12.0,
        "comparison": 11.0,
        "timeline": 11.0,
        "closing": -8.0,
    }.get(page.role, 0.0)
    type_weights = {
        "chart": 12.0,
        "table": 9.0,
        "image": 8.0,
        "icon": 2.5,
        "shape": 1.0,
        "line": 0.5,
        "text": 0.0,
    }
    element_types = [getattr(element, "type", "unknown") for element in page.elements]
    score = role_score + sum(type_weights.get(element_type, 0.0) for element_type in element_types)
    score += len(set(element_types)) * 1.5
    if page.background_gradient is not None:
        score += 2.0
    if 4 <= len(page.elements) <= 18:
        score += 2.0
    elif len(page.elements) > 24:
        score -= (len(page.elements) - 24) * 0.4

    text_length = sum(
        len(getattr(element, "text", ""))
        for element in page.elements
        if getattr(element, "type", "") == "text"
    )
    if text_length > 650:
        score -= min(10.0, (text_length - 650) / 80)
    signature = page.role if page.role != "content" else "+".join(sorted(set(element_types)))
    return score, signature


@lru_cache(maxsize=128)
def _cached_v2_visual_highlights(job_dir_text: str, update_token: int) -> tuple[int, ...]:
    del update_token
    job_dir = Path(job_dir_text)
    design_path = job_dir / "generated_long_deck_v2_design.json"
    pages: list[V2PageDesign] = []
    if design_path.is_file():
        try:
            pages = V2DeckDesign.model_validate_json(design_path.read_text(encoding="utf-8")).pages
        except (OSError, ValueError):
            return ()
    else:
        pages_dir = job_dir / "checkpoints" / "pages"
        for path in sorted(pages_dir.glob("page_*.json"), key=_natural_slide_key):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                pages.append(V2PageDesign.model_validate(payload.get("page", payload)))
            except (OSError, ValueError, TypeError):
                continue

    scored = []
    for page in pages:
        score, signature = _v2_page_visual_score(page)
        scored.append((page.page_number, score, signature))
    return tuple(_select_visual_highlights(scored))


@lru_cache(maxsize=128)
def _cached_svg_visual_highlights(job_dir_text: str, update_token: int) -> tuple[int, ...]:
    del update_token
    slides = _ppt_master_preview_slides(Path(job_dir_text))
    scored: list[tuple[int, float, str]] = []
    for index, path in enumerate(slides, start=1):
        try:
            svg = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        graphic_count = sum(svg.count(f"<{tag}") for tag in ("path", "rect", "circle", "ellipse", "polygon", "line"))
        text_count = svg.count("<text")
        image_count = svg.count("<image")
        gradient_count = svg.count("gradient")
        color_count = len(set(re.findall(r'(?:fill|stroke)=["\'](#[0-9a-f]{3,8}|rgb\([^)]*\))', svg)))
        score = min(graphic_count, 40) * 0.35 + min(text_count, 14) * 0.25
        score += image_count * 4.0 + min(gradient_count, 4) * 1.5 + min(color_count, 10) * 0.6
        if index in {1, len(slides)}:
            score -= 6.0
        density = "image" if image_count else ("diagram" if graphic_count >= 10 else "editorial")
        scored.append((index, score, density))
    return tuple(_select_visual_highlights(scored))


def _v2_preview_available_slide_numbers(job_dir: Path) -> tuple[list[int], int]:
    design_path = job_dir / "generated_long_deck_v2_design.json"
    if design_path.is_file():
        try:
            deck = V2DeckDesign.model_validate_json(design_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], 0
        return [page.page_number for page in deck.pages], design_path.stat().st_mtime_ns

    pages_dir = job_dir / "checkpoints" / "pages"
    if not pages_dir.is_dir():
        return [], 0
    page_files = sorted(pages_dir.glob("page_*.json"), key=_natural_slide_key)
    numbers: list[int] = []
    update_token = 0
    for path in page_files:
        match = re.search(r"(\d+)(?!.*\d)", path.stem)
        if match and path.is_file():
            numbers.append(int(match.group(1)))
            update_token = max(update_token, path.stat().st_mtime_ns)
    # Theme-only revisions repaint every page without touching page checkpoints.
    theme_path = job_dir / "checkpoints" / "theme.json"
    if theme_path.is_file():
        update_token = max(update_token, theme_path.stat().st_mtime_ns)
    return numbers, update_token


def _v2_preview_page(job_dir: Path, slide_number: int) -> tuple[V2PageDesign, V2DeckDesign] | None:
    design_path = job_dir / "generated_long_deck_v2_design.json"
    if design_path.is_file():
        try:
            deck = V2DeckDesign.model_validate_json(design_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        page = next((item for item in deck.pages if item.page_number == slide_number), None)
        return (page, deck) if page is not None else None

    checkpoint_root = job_dir / "checkpoints"
    page_path = checkpoint_root / "pages" / f"page_{slide_number:03d}.json"
    theme_path = checkpoint_root / "theme.json"
    skeleton_path = checkpoint_root / "skeleton.json"
    if not page_path.is_file() or not theme_path.is_file() or not skeleton_path.is_file():
        return None
    try:
        page_payload = json.loads(page_path.read_text(encoding="utf-8"))
        skeleton_payload = json.loads(skeleton_path.read_text(encoding="utf-8"))
        page = V2PageDesign.model_validate(page_payload.get("page", page_payload))
        theme = V2ThemeSpec.model_validate_json(theme_path.read_text(encoding="utf-8"))
        deck = V2DeckDesign.model_construct(
            deck_title=skeleton_payload.get("deck_title") or "PPT 项目工作台",
            subtitle=skeleton_payload.get("subtitle"),
            language=skeleton_payload.get("language") or "zh-CN",
            theme=theme,
            pages=[page],
        )
    except (OSError, TypeError, ValueError):
        return None
    return page, deck


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
    if message == "Running long deck hard quality gate":
        return "running_long_deck_quality_gate"
    if message == "Long deck run succeeded":
        return "completed"
    return None


def _artifact_name_for_path(output_dir: Path, artifact_path: Path) -> str | None:
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


def _register_job_artifacts(store: JobStore, job_id: str, output_dir: Path) -> None:
    for artifact_path in sorted(output_dir.rglob("*")):
        if not artifact_path.is_file() or artifact_path.suffix.lower() not in {".json", ".pptx", ".md"}:
            continue
        artifact_name = _artifact_name_for_path(output_dir, artifact_path)
        if artifact_name is None:
            continue
        suffix = artifact_path.suffix.lower()
        kind: ArtifactKind = "pptx" if suffix == ".pptx" else "md" if suffix == ".md" else "json"
        store.add_artifact(job_id, name=artifact_name, kind=kind, path=artifact_path)


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


def _long_deck_request_path(output_dir: Path) -> Path:
    return output_dir / "long_deck_request.json"


def _write_long_deck_request_artifact(payload: CreateLongDeckJobRequest, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _long_deck_request_path(output_dir)
    path.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _load_long_deck_request_artifact(output_dir: Path) -> CreateLongDeckJobRequest:
    path = _long_deck_request_path(output_dir)
    if not path.is_file():
        raise ValueError(
            "Long deck request metadata is missing; this job cannot be resumed from the Web UI."
        )
    return CreateLongDeckJobRequest.model_validate_json(path.read_text(encoding="utf-8"))


def _save_presentation_request_snapshot(
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


def _backfill_presentation_request_history(store: JobStore, jobs_root: Path) -> None:
    """Import metadata from existing job artifacts without changing those artifacts."""
    for job_id in store.job_ids_missing_presentation_request():
        job_dir = jobs_root / job_id
        try:
            long_request_path = _long_deck_request_path(job_dir)
            if long_request_path.is_file():
                payload = CreateLongDeckJobRequest.model_validate_json(
                    long_request_path.read_text(encoding="utf-8")
                )
                _save_presentation_request_snapshot(store, job_id, payload)
                continue

            brief_path = job_dir / "generated_deck_brief.json"
            if brief_path.is_file():
                brief_document = json.loads(brief_path.read_text(encoding="utf-8"))
                brief = brief_document.get("brief", brief_document)
                if isinstance(brief, dict):
                    topic = brief.get("topic")
                    slide_count = brief.get("slide_count")
                    if isinstance(topic, str) and topic.strip() and isinstance(slide_count, int):
                        store.save_presentation_request(
                            job_id,
                            topic=topic.strip(),
                            audience=str(brief.get("audience") or ""),
                            user_requirements=str(
                                brief.get("user_requirements_raw") or brief.get("content_focus") or ""
                            ),
                            slide_count=slide_count,
                        )
                        continue

            deck_ir_path = job_dir / "generated_deck_ir.json"
            if deck_ir_path.is_file():
                deck_ir = json.loads(deck_ir_path.read_text(encoding="utf-8"))
                title = deck_ir.get("title")
                slides = deck_ir.get("slides")
                if isinstance(title, str) and title.strip() and isinstance(slides, list) and slides:
                    store.save_presentation_request(
                        job_id,
                        topic=title.strip(),
                        audience="",
                        user_requirements="",
                        slide_count=min(len(slides), 100),
                    )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("presentation_history_backfill_skipped job_id=%s", job_id)


def _read_quality_gate_status(path: Path | None, *, fallback: str | None = None) -> str | None:
    if path is None or not path.is_file():
        return fallback
    try:
        status = json.loads(path.read_text(encoding="utf-8")).get("status")
    except (OSError, json.JSONDecodeError):
        return fallback
    return status if isinstance(status, str) else fallback


def _create_long_deck_ppt_master_package(
    *,
    deck_ir_path: Path,
    output_dir: Path,
    payload: CreateLongDeckJobRequest,
    package_mode: Literal["normal", "recovery"],
    source_quality_gate_status: str | None,
    source_quality_gate_report_path: Path | None,
) -> None:
    warning = PPT_MASTER_RECOVERY_WARNING if package_mode == "recovery" else None
    ppt_master_package = create_ppt_master_job_package(
        json.loads(deck_ir_path.read_text(encoding="utf-8")),
        output_dir / "ppt_master_package",
        topic=payload.topic,
        audience=payload.audience,
        package_mode=package_mode,
        source_quality_gate_status=source_quality_gate_status,
        source_quality_gate_report_path=source_quality_gate_report_path,
        warning=warning,
    )
    (output_dir / "ppt_master_source.md").write_text(
        ppt_master_package.source_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


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


class _V2JobCancelled(RuntimeError):
    pass


def _v2_long_deck_prompt(payload: CreateLongDeckJobRequest) -> str:
    return (
        f"主题：{payload.topic}\n"
        f"目标观众：{payload.audience}\n"
        f"演示要求：{payload.user_requirements}"
    )


def _read_v2_qa_score(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total_pages = int(payload.get("total_pages") or 0)
        pages_with_errors = int(payload.get("pages_with_errors") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if total_pages <= 0:
        return None
    return max(0, min(100, round((total_pages - pages_with_errors) / total_pages * 100)))


def _run_v2_long_deck_job(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    client,
    payload: CreateLongDeckJobRequest,
    *,
    output_dir_override: Path | None = None,
    resume: bool = False,
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

    def progress_logger(message: str) -> None:
        if store.is_cancel_requested(job_id):
            raise _V2JobCancelled("v2 long deck generation was cancelled.")

        current_stage: str | None = None
        completed_pages: int | None = None
        current_page: str | None = None
        design_match = re.match(r"^\[design\] (\d+)/(\d+) content pages done$", message)
        stage_match = re.match(r"^\[stage\] ([a-z_]+) finished", message)
        if design_match:
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
                "page_briefs": "v2_page_designs",
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
        _write_long_deck_request_artifact(payload, output_dir)
        result: V2BuildResult = build_v2_deck(
            V2BuildRequest(
                prompt=_v2_long_deck_prompt(payload),
                page_count=payload.slide_count,
                language=payload.language,
                output_dir=str(output_dir),
                deck_name="generated_long_deck_v2",
                resume=resume,
                concurrency=_env_int("PPT_AGENT_V2_CONCURRENCY", DEFAULT_V2_CONCURRENCY),
                budget_usd=_env_float("PPT_AGENT_V2_BUDGET_USD", DEFAULT_V2_BUDGET_USD),
                qa_gate="strict",
            ),
            client,
            progress=progress_logger,
        )
        _register_job_artifacts(store, job_id, output_dir)
        qa_score = _read_v2_qa_score(Path(result.qa_report_path))
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
    except _V2JobCancelled as exc:
        if output_dir.exists():
            _register_job_artifacts(store, job_id, output_dir)
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
            _register_job_artifacts(store, job_id, output_dir)
        store.update_job(
            job_id,
            status="failed",
            error_message=error_message,
            accepted=False,
            current_stage="v2_failed",
        )


def _deck_plan_response(record) -> DeckPlanResponse:
    request = CreateLongDeckJobRequest.model_validate_json(record.request_json)
    plan: EditableDeckPlan | None = None
    total_pages: int | None = None
    if record.plan_json:
        payload = json.loads(record.plan_json)
        plan = EditableDeckPlan.model_validate(payload["plan"])
        total_pages = plan.total_pages()
    return DeckPlanResponse(
        plan_id=record.plan_id,
        status=record.status,
        request=request,
        plan=plan,
        total_pages=total_pages,
        error_message=record.error_message,
        job_id=record.job_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_deck_plan(
    store: JobStore,
    plans_root: Path,
    plan_id: str,
    client,
    payload: CreateLongDeckJobRequest,
) -> None:
    output_dir = plans_root / plan_id
    output_dir.mkdir(parents=True, exist_ok=True)

    def progress_logger(message: str) -> None:
        logger.info(
            "deck_plan_stage %s",
            json.dumps({"plan_id": plan_id, "message": message}, ensure_ascii=False),
        )

    try:
        result = plan_v2_deck(
            V2BuildRequest(
                prompt=_v2_long_deck_prompt(payload),
                page_count=payload.slide_count,
                language=payload.language,
                output_dir=str(output_dir),
                deck_name="deck_plan",
                concurrency=_env_int("PPT_AGENT_V2_CONCURRENCY", DEFAULT_V2_CONCURRENCY),
                budget_usd=_env_float("PPT_AGENT_V2_BUDGET_USD", DEFAULT_V2_BUDGET_USD),
            ),
            client,
            progress=progress_logger,
        )
        editable = editable_plan_from_skeleton(result.skeleton)
        store.update_deck_plan(
            plan_id,
            status="ready",
            plan_json=json.dumps(
                {
                    "brief": result.brief.model_dump(mode="json"),
                    "plan": editable.model_dump(mode="json"),
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:
        error_message = sanitize_error_message(exc)
        logger.error("deck_plan_failed plan_id=%s error=%s", plan_id, error_message)
        store.update_deck_plan(plan_id, status="failed", error_message=error_message)


def _seed_plan_checkpoints(job_dir: Path, brief: V2ContentBrief, skeleton) -> None:
    """Write an approved plan as generation checkpoints so resume skips planning."""

    checkpoints_dir = job_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir.joinpath("brief.json").write_text(
        json.dumps(brief.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    skeleton_payload = json.dumps(
        skeleton.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    checkpoints_dir.joinpath("skeleton.json").write_text(skeleton_payload, encoding="utf-8")
    checkpoints_dir.joinpath("skeleton_with_briefs.json").write_text(
        skeleton_payload, encoding="utf-8"
    )


def _deck_revision_response(record) -> DeckRevisionResponse:
    try:
        revised_pages = [int(number) for number in json.loads(record.revised_pages_json)]
    except (ValueError, TypeError):
        revised_pages = []
    return DeckRevisionResponse(
        revision_id=record.revision_id,
        job_id=record.job_id,
        status=record.status,
        message=record.message,
        reply=record.reply,
        revised_pages=revised_pages,
        error_message=record.error_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _register_missing_job_artifacts(store: JobStore, job_id: str, output_dir: Path) -> None:
    """Register artifacts that appeared after the original run (idempotent)."""

    existing = {artifact.name for artifact in store.list_artifacts(job_id)}
    for artifact_path in sorted(output_dir.rglob("*")):
        if not artifact_path.is_file() or artifact_path.suffix.lower() not in {".json", ".pptx", ".md"}:
            continue
        artifact_name = _artifact_name_for_path(output_dir, artifact_path)
        if artifact_name is None or artifact_name in existing:
            continue
        suffix = artifact_path.suffix.lower()
        kind: ArtifactKind = "pptx" if suffix == ".pptx" else "md" if suffix == ".md" else "json"
        store.add_artifact(job_id, name=artifact_name, kind=kind, path=artifact_path)


def _run_deck_revision(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    revision_id: str,
    client,
    message: str,
    page_numbers: list[int] | None,
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
            concurrency=_env_int("PPT_AGENT_V2_CONCURRENCY", DEFAULT_V2_CONCURRENCY),
            progress=progress_logger,
        )
        store.update_deck_revision(
            revision_id,
            status="succeeded",
            reply=result.reply,
            revised_pages_json=json.dumps(result.revised_pages),
        )
        _register_missing_job_artifacts(store, job_id, output_dir)
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


def _run_long_deck_job(
    store: JobStore,
    jobs_root: Path,
    job_id: str,
    model,
    payload: CreateLongDeckJobRequest,
    *,
    output_dir_override: Path | None = None,
    resume: bool = False,
) -> None:
    model_name = _model_name(model)
    total_batches = _expected_long_deck_batches(payload)
    output_dir = output_dir_override or jobs_root / job_id
    store.update_job(job_id, status="running", current_stage="preparing_long_deck_plan")
    store.update_long_deck_progress(job_id, total_batches=total_batches, completed_batches=0, failed_batches=0)
    progress_counts = {"completed": 0, "failed": 0}

    def progress_logger(message: str) -> None:
        current_stage = _long_deck_stage_from_progress(message, total_batches)
        current_batch = None
        batch_match = re.search(r"\bbatch_(\d+)\b", message)
        if batch_match:
            current_batch = f"batch_{batch_match.group(1)}"
        progress_changed = False
        if message.startswith(("Completed batch_", "Skipping batch_")):
            progress_counts["completed"] += 1
            progress_changed = True
        if message.startswith("Failed batch_"):
            progress_counts["failed"] += 1
            progress_changed = True
        if current_stage is not None or progress_changed:
            store.update_long_deck_progress(
                job_id,
                current_stage=current_stage,
                total_batches=total_batches,
                completed_batches=progress_counts["completed"],
                failed_batches=progress_counts["failed"],
                current_batch=current_batch,
            )
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
        _write_long_deck_request_artifact(payload, output_dir)
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
                resume=resume,
            ),
            model,
            progress_logger=progress_logger,
            cancel_checker=lambda: store.is_cancel_requested(job_id),
        )
        store.update_long_deck_progress(
            job_id,
            total_batches=run_report.total_batches,
            completed_batches=len(run_report.completed_batches),
            failed_batches=len(run_report.failed_batches),
        )

        render_report: LongDeckRenderReport | None = None
        if run_report.merged_deck_ir_path is not None and run_report.status in {
            "succeeded",
            "failed_quality_gate",
            "partial_failed_quality_gate",
        }:
            package_mode: Literal["normal", "recovery"] = (
                "normal" if run_report.status == "succeeded" else "recovery"
            )
            _create_long_deck_ppt_master_package(
                deck_ir_path=run_report.merged_deck_ir_path,
                output_dir=output_dir,
                payload=payload,
                package_mode=package_mode,
                source_quality_gate_status=_read_quality_gate_status(
                    run_report.long_deck_quality_gate_path,
                    fallback=run_report.status,
                ),
                source_quality_gate_report_path=run_report.long_deck_quality_gate_path,
            )

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
        if run_report.status in {"cancelled", "partial_cancelled"}:
            store.update_job(
                job_id,
                status=run_report.status,
                error_message=run_report.error_message or "Long deck job was cancelled.",
                accepted=False,
                qa_score=qa_score,
                current_stage=run_report.status,
            )
            return

        if run_report.status in {"failed_quality_gate", "partial_failed_quality_gate"}:
            store.update_job(
                job_id,
                status=run_report.status,
                error_message=run_report.error_message or "Long deck quality gate failed before render.",
                accepted=False,
                qa_score=qa_score,
                current_stage=run_report.status,
            )
            return

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
    app.state.plans_root = root / "plans"
    _backfill_presentation_request_history(app.state.job_store, jobs_root)

    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((WEBUI_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/presentation-interviews",
        response_model=PresentationInterviewState,
        status_code=201,
    )
    def start_presentation_interview(
        payload: StartPresentationInterviewRequest,
    ) -> PresentationInterviewState:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")
        try:
            model = _create_interview_chat_model()
            user_message = InterviewMessage(role="user", content=payload.message.strip())
            decision = run_requirements_interview_turn(
                model,
                [user_message],
                timeout_seconds=LLM_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=502, detail=f"Could not analyze presentation requirements: {detail}") from exc

        assistant_content = decision.assistant_message
        if decision.question:
            assistant_content = f"{assistant_content}\n\n{decision.question}"
        messages = [
            user_message,
            InterviewMessage(role="assistant", content=assistant_content),
        ]
        return _save_presentation_interview_state(
            app.state.job_store,
            interview_id=uuid.uuid4().hex,
            messages=messages,
            decision=decision,
            turn_count=1,
        )

    @app.get(
        "/api/presentation-interviews/{interview_id}",
        response_model=PresentationInterviewState,
    )
    def get_presentation_interview(interview_id: str) -> PresentationInterviewState:
        record = app.state.job_store.get_presentation_interview(interview_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Presentation interview not found.")
        return _presentation_interview_state_from_record(record)

    @app.post(
        "/api/presentation-interviews/{interview_id}/messages",
        response_model=PresentationInterviewState,
    )
    def continue_presentation_interview(
        interview_id: str,
        payload: ContinuePresentationInterviewRequest,
    ) -> PresentationInterviewState:
        record = app.state.job_store.get_presentation_interview(interview_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Presentation interview not found.")
        state = _presentation_interview_state_from_record(record)
        if state.turn_count >= 20:
            raise HTTPException(status_code=409, detail="Presentation interview reached its safety turn limit.")
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")

        user_message = InterviewMessage(
            role="user",
            content=payload.message.strip(),
            selected_option_id=payload.selected_option_id,
        )
        model_messages = [*state.messages, user_message]
        try:
            model = _create_interview_chat_model()
            decision = run_requirements_interview_turn(
                model,
                model_messages,
                previous_brief=state.decision.brief,
                timeout_seconds=LLM_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=502, detail=f"Could not continue presentation interview: {detail}") from exc

        assistant_content = decision.assistant_message
        if decision.question:
            assistant_content = f"{assistant_content}\n\n{decision.question}"
        persisted_messages = [
            *model_messages,
            InterviewMessage(role="assistant", content=assistant_content),
        ]
        return _save_presentation_interview_state(
            app.state.job_store,
            interview_id=interview_id,
            messages=persisted_messages,
            decision=decision,
            turn_count=state.turn_count + 1,
        )

    @app.post("/api/jobs", response_model=CreateJobResponse, status_code=202)
    def create_job(payload: CreateJobRequest, background_tasks: BackgroundTasks) -> CreateJobResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")

        try:
            model = _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not initialize OpenAI chat model: {detail}") from exc

        job = app.state.job_store.create_job(job_type="short_deck")
        _save_presentation_request_snapshot(app.state.job_store, job.job_id, payload)
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
        if _uses_v2_generation(payload):
            try:
                client = _create_v2_model_client()
            except (V2ProviderError, ValueError) as exc:
                detail = sanitize_error_message(exc)
                raise HTTPException(
                    status_code=503,
                    detail=f"Could not initialize the v2 model provider: {detail}",
                ) from exc

            job = app.state.job_store.create_job(job_type="long_deck_v2")
            _save_presentation_request_snapshot(app.state.job_store, job.job_id, payload)
            app.state.job_store.update_long_deck_progress(
                job.job_id,
                current_stage="v2_intake",
                total_batches=payload.slide_count,
                completed_batches=0,
                failed_batches=0,
            )
            background_tasks.add_task(
                _run_v2_long_deck_job,
                app.state.job_store,
                app.state.jobs_root,
                job.job_id,
                client,
                payload,
            )
            return CreateJobResponse(job_id=job.job_id, status=job.status)

        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")

        try:
            model = _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not initialize OpenAI chat model: {detail}") from exc

        job = app.state.job_store.create_job(job_type="long_deck")
        _save_presentation_request_snapshot(app.state.job_store, job.job_id, payload)
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

    @app.post("/api/long-deck-jobs/{job_id}/resume", response_model=CreateJobResponse, status_code=202)
    def resume_long_deck_job(job_id: str, background_tasks: BackgroundTasks) -> CreateJobResponse:
        original_job = app.state.job_store.get_job(job_id)
        if original_job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if original_job.job_type not in {"long_deck", "long_deck_v2"}:
            raise HTTPException(status_code=400, detail="Only long deck jobs can be resumed.")

        output_dir = app.state.jobs_root / job_id
        try:
            payload = _load_long_deck_request_artifact(output_dir)
            model = _create_v2_model_client() if _uses_v2_generation(payload) else _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not prepare long deck resume job: {detail}") from exc

        resume_job_type = "long_deck_v2" if _uses_v2_generation(payload) else "long_deck"
        resume_job = app.state.job_store.create_job(job_type=resume_job_type)
        _save_presentation_request_snapshot(
            app.state.job_store,
            resume_job.job_id,
            payload,
            resumed_from_job_id=job_id,
        )
        if _uses_v2_generation(payload):
            app.state.job_store.update_long_deck_progress(
                resume_job.job_id,
                current_stage="v2_intake",
                total_batches=payload.slide_count,
                completed_batches=0,
                failed_batches=0,
            )
            background_tasks.add_task(
                _run_v2_long_deck_job,
                app.state.job_store,
                app.state.jobs_root,
                resume_job.job_id,
                model,
                payload,
                output_dir_override=output_dir,
                resume=True,
            )
        else:
            app.state.job_store.update_progress(resume_job.job_id, current_stage="preparing_long_deck_plan")
            background_tasks.add_task(
                _run_long_deck_job,
                app.state.job_store,
                app.state.jobs_root,
                resume_job.job_id,
                model,
                payload,
                output_dir_override=output_dir,
                resume=True,
            )
        return CreateJobResponse(job_id=resume_job.job_id, status=resume_job.status)

    @app.post("/api/deck-plans", response_model=DeckPlanResponse, status_code=202)
    def create_deck_plan(
        payload: CreateLongDeckJobRequest,
        background_tasks: BackgroundTasks,
    ) -> DeckPlanResponse:
        if payload.slide_count < 4:
            raise HTTPException(
                status_code=400,
                detail="Deck plans support 4-100 page presentations; short decks generate directly.",
            )
        try:
            client = _create_v2_model_client()
        except (V2ProviderError, ValueError) as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(
                status_code=503,
                detail=f"Could not initialize the v2 model provider: {detail}",
            ) from exc
        payload = payload.model_copy(update={"deck_type": "visual_design_v2"})
        record = app.state.job_store.create_deck_plan(request_json=payload.model_dump_json())
        background_tasks.add_task(
            _run_deck_plan,
            app.state.job_store,
            app.state.plans_root,
            record.plan_id,
            client,
            payload,
        )
        return _deck_plan_response(record)

    @app.get("/api/deck-plans/{plan_id}", response_model=DeckPlanResponse)
    def get_deck_plan(plan_id: str) -> DeckPlanResponse:
        record = app.state.job_store.get_deck_plan(plan_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Deck plan not found.")
        return _deck_plan_response(record)

    @app.put("/api/deck-plans/{plan_id}", response_model=DeckPlanResponse)
    def update_deck_plan(plan_id: str, payload: UpdateDeckPlanRequest) -> DeckPlanResponse:
        record = app.state.job_store.get_deck_plan(plan_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Deck plan not found.")
        if record.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"Deck plan is '{record.status}'; only a ready plan can be edited.",
            )
        try:
            skeleton_from_editable_plan(payload.plan)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stored = json.loads(record.plan_json) if record.plan_json else {}
        stored["plan"] = payload.plan.model_dump(mode="json")
        record = app.state.job_store.update_deck_plan(
            plan_id, plan_json=json.dumps(stored, ensure_ascii=False)
        )
        return _deck_plan_response(record)

    @app.post(
        "/api/deck-plans/{plan_id}/confirm",
        response_model=CreateJobResponse,
        status_code=202,
    )
    def confirm_deck_plan(
        plan_id: str,
        background_tasks: BackgroundTasks,
        payload: ConfirmDeckPlanRequest | None = None,
    ) -> CreateJobResponse:
        record = app.state.job_store.get_deck_plan(plan_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Deck plan not found.")
        if record.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"Deck plan is '{record.status}'; only a ready plan can be confirmed.",
            )
        stored = json.loads(record.plan_json)
        editable = (
            payload.plan
            if payload is not None and payload.plan is not None
            else EditableDeckPlan.model_validate(stored["plan"])
        )
        try:
            skeleton = skeleton_from_editable_plan(editable)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        brief = V2ContentBrief.model_validate(stored["brief"]).model_copy(
            update={"deck_title": editable.deck_title, "subtitle": editable.subtitle}
        )

        try:
            client = _create_v2_model_client()
        except (V2ProviderError, ValueError) as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(
                status_code=503,
                detail=f"Could not initialize the v2 model provider: {detail}",
            ) from exc

        request_payload = CreateLongDeckJobRequest.model_validate_json(
            record.request_json
        ).model_copy(
            update={"slide_count": skeleton.total_pages, "deck_type": "visual_design_v2"}
        )

        job = app.state.job_store.create_job(job_type="long_deck_v2")
        _seed_plan_checkpoints(app.state.jobs_root / job.job_id, brief, skeleton)
        _save_presentation_request_snapshot(app.state.job_store, job.job_id, request_payload)
        app.state.job_store.update_long_deck_progress(
            job.job_id,
            current_stage="v2_intake",
            total_batches=request_payload.slide_count,
            completed_batches=0,
            failed_batches=0,
        )
        app.state.job_store.update_deck_plan(
            plan_id,
            status="confirmed",
            job_id=job.job_id,
            plan_json=json.dumps(
                {"brief": stored["brief"], "plan": editable.model_dump(mode="json")},
                ensure_ascii=False,
            ),
        )
        background_tasks.add_task(
            _run_v2_long_deck_job,
            app.state.job_store,
            app.state.jobs_root,
            job.job_id,
            client,
            request_payload,
            resume=True,
        )
        return CreateJobResponse(job_id=job.job_id, status=job.status)

    @app.post(
        "/api/jobs/{job_id}/revisions",
        response_model=DeckRevisionResponse,
        status_code=202,
    )
    def create_deck_revision(
        job_id: str,
        payload: CreateDeckRevisionRequest,
        background_tasks: BackgroundTasks,
    ) -> DeckRevisionResponse:
        store: JobStore = app.state.job_store
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type != "long_deck_v2":
            raise HTTPException(
                status_code=400,
                detail="Only v2 presentations support conversational revisions.",
            )
        if job.status not in {"succeeded", "failed_quality_gate"}:
            raise HTTPException(
                status_code=409,
                detail=f"Job is '{job.status}'; wait for generation to finish before revising.",
            )
        checkpoints_dir = app.state.jobs_root / job_id / "checkpoints"
        if not checkpoints_dir.is_dir():
            raise HTTPException(
                status_code=409,
                detail="This job has no revision checkpoints (it may predate revisions).",
            )
        if store.has_running_deck_revision(job_id):
            raise HTTPException(
                status_code=409,
                detail="A revision is already running for this presentation.",
            )
        try:
            client = _create_v2_model_client()
        except (V2ProviderError, ValueError) as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(
                status_code=503,
                detail=f"Could not initialize the v2 model provider: {detail}",
            ) from exc
        record = store.create_deck_revision(job_id=job_id, message=payload.message)
        background_tasks.add_task(
            _run_deck_revision,
            store,
            app.state.jobs_root,
            job_id,
            record.revision_id,
            client,
            payload.message,
            payload.page_numbers,
        )
        return _deck_revision_response(record)

    @app.get("/api/jobs/{job_id}/revisions", response_model=DeckRevisionListResponse)
    def list_deck_revisions(job_id: str) -> DeckRevisionListResponse:
        if app.state.job_store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        records = app.state.job_store.list_deck_revisions(job_id)
        return DeckRevisionListResponse(
            items=[_deck_revision_response(record) for record in records]
        )

    @app.get(
        "/api/jobs/{job_id}/revisions/{revision_id}",
        response_model=DeckRevisionResponse,
    )
    def get_deck_revision(job_id: str, revision_id: str) -> DeckRevisionResponse:
        record = app.state.job_store.get_deck_revision(revision_id)
        if record is None or record.job_id != job_id:
            raise HTTPException(status_code=404, detail="Revision not found.")
        return _deck_revision_response(record)

    @app.post(
        "/api/long-deck-jobs/{job_id}/prepare-ppt-master-execution",
        response_model=PptMasterExecutionResponse,
    )
    def prepare_long_deck_ppt_master_execution(job_id: str) -> PptMasterExecutionResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type != "long_deck":
            raise HTTPException(status_code=400, detail="Only long deck jobs can prepare PPT Master execution.")

        job_dir = app.state.jobs_root / job_id
        try:
            plan = prepare_ppt_master_execution(job_id, job_dir)
            plan_artifact = _ensure_artifact(
                app.state.job_store,
                job_id=job_id,
                name=PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
                kind="json",
                path=job_dir / PPT_MASTER_EXECUTION_PLAN_FILENAME,
            )
            if plan.status == "output_detected":
                _ensure_registered_ppt_master_output(app.state.job_store, app.state.jobs_root, job)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not prepare PPT Master execution plan: {sanitize_error_message(exc)}",
            ) from exc

        return PptMasterExecutionResponse(
            status=plan.status,
            plan_artifact_id=plan_artifact.artifact_id,
            project_dir=str(plan.project_dir) if plan.project_dir is not None else None,
            output_dir=str(plan.output_dir),
            expected_pptx_path=str(plan.expected_pptx_path),
            suggested_steps=plan.suggested_steps,
            message=_ppt_master_execution_message(plan.status),
        )

    @app.post(
        "/api/long-deck-jobs/{job_id}/bootstrap-ppt-master-project",
        response_model=PptMasterVisualProjectResponse,
    )
    def bootstrap_long_deck_ppt_master_visual_project(job_id: str) -> PptMasterVisualProjectResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type != "long_deck":
            raise HTTPException(status_code=400, detail="Only long deck jobs can bootstrap PPT Master projects.")

        job_dir = app.state.jobs_root / job_id
        try:
            project = bootstrap_ppt_master_visual_project(job_id, job_dir)
            registration = register_ppt_master_visual_project_artifacts(
                app.state.job_store,
                job_id=job_id,
                job_dir=job_dir,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not bootstrap PPT Master visual project: {sanitize_error_message(exc)}",
            ) from exc

        return _visual_project_response_from_manifest(
            project,
            manifest_artifact_id=registration.manifest_artifact.artifact_id,
            instructions_artifact_id=(
                registration.instructions_artifact.artifact_id
                if registration.instructions_artifact is not None
                else None
            ),
        )

    @app.post(
        "/api/long-deck-jobs/{job_id}/run-ppt-master-local-export",
        response_model=PptMasterRunnerResponse,
    )
    def run_long_deck_ppt_master_local_export(job_id: str) -> PptMasterRunnerResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type != "long_deck":
            raise HTTPException(status_code=400, detail="Only long deck jobs can run PPT Master local export.")

        job_dir = app.state.jobs_root / job_id
        try:
            result = run_ppt_master_local_export(
                job_id,
                job_dir,
                store=app.state.job_store,
            )
            result_artifact = register_ppt_master_runner_result_artifact(
                app.state.job_store,
                job_id=job_id,
                job_dir=job_dir,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not run PPT Master local export: {sanitize_error_message(exc)}",
            ) from exc

        return _runner_response_from_result(result, result_artifact_id=result_artifact.artifact_id)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str) -> JobResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type not in {"long_deck", "long_deck_v2"}:
            raise HTTPException(status_code=400, detail="Only long deck jobs can be cancelled.")
        if job.status not in {"pending", "running"}:
            return _job_response(app.state.job_store, app.state.jobs_root, job)

        app.state.job_store.request_cancel(job_id)
        cancelled = app.state.job_store.get_job(job_id)
        if cancelled is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _job_response(app.state.job_store, app.state.jobs_root, cancelled)

    @app.get("/api/jobs/latest", response_model=JobResponse)
    def get_latest_job(job_type: str | None = None) -> JobResponse:
        job = app.state.job_store.get_latest_job(job_type=job_type)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        job = _expire_stale_job(app.state.job_store, job)
        return _job_response(app.state.job_store, app.state.jobs_root, job)

    @app.get("/api/presentations", response_model=PresentationHistoryResponse)
    def list_presentation_history(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        status: JobStatus | None = None,
        query: str | None = Query(default=None, max_length=200),
    ) -> PresentationHistoryResponse:
        records, total = app.state.job_store.list_presentation_history(
            limit=limit,
            offset=offset,
            status=status,
            query=query,
        )
        return PresentationHistoryResponse(
            items=[_presentation_history_item(record) for record in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> JobResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        job = _expire_stale_job(app.state.job_store, job)
        return _job_response(app.state.job_store, app.state.jobs_root, job)

    @app.get("/api/jobs/{job_id}/preview-slides")
    def list_job_slide_previews(job_id: str) -> dict[str, Any]:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        job_dir = app.state.jobs_root / job_id
        if job.job_type == "long_deck_v2":
            numbers, update_token = _v2_preview_available_slide_numbers(job_dir)
            preview_kind = "v2_html" if numbers else "none"
            highlights = list(_cached_v2_visual_highlights(str(job_dir.resolve()), update_token))
        elif job.job_type == "long_deck":
            slides = _ppt_master_preview_slides(job_dir)
            numbers = list(range(1, len(slides) + 1))
            update_token = max((path.stat().st_mtime_ns for path in slides), default=0)
            preview_kind = "ppt_master_svg" if numbers else "none"
            highlights = list(_cached_svg_visual_highlights(str(job_dir.resolve()), update_token))
        else:
            numbers = []
            update_token = 0
            preview_kind = "none"
            highlights = []
        return {
            "available_slide_numbers": numbers,
            "highlight_slide_numbers": highlights,
            "total_requested": max(job.total_batches or 0, len(numbers)),
            "preview_kind": preview_kind,
            "update_token": str(update_token),
        }

    @app.get("/api/jobs/{job_id}/preview-slides/{slide_number}")
    def preview_job_slide(job_id: str, slide_number: int) -> Response:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type not in {"long_deck", "long_deck_v2"}:
            raise HTTPException(status_code=404, detail="Slide preview is available only for long deck jobs.")
        if slide_number < 1:
            raise HTTPException(status_code=404, detail="Slide preview not found.")

        job_dir = app.state.jobs_root / job_id
        if job.job_type == "long_deck_v2":
            preview = _v2_preview_page(job_dir, slide_number)
            if preview is None:
                raise HTTPException(status_code=404, detail="Slide preview not found.")
            page, deck = preview
            return HTMLResponse(v2_page_to_embedded_html(page, deck))

        slides = _ppt_master_preview_slides(job_dir)
        if slide_number > len(slides):
            raise HTTPException(status_code=404, detail="Slide preview not found.")
        return FileResponse(slides[slide_number - 1], media_type="image/svg+xml")

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
