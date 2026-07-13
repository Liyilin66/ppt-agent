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
)
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


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="icon" href="data:,">
    <title>ppt-agent PPT 生成器</title>
    <style>
      :root {
        color-scheme: light;
        font-family: Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif;
        color: #182230;
        background: #eef2f5;
        --ink: #182230;
        --muted: #637083;
        --line: #dce3e8;
        --surface: #ffffff;
        --surface-soft: #f5f8f9;
        --mint: #e7f5f2;
        --teal: #087f78;
        --teal-dark: #05645f;
        --cobalt: #315cc8;
        --coral: #dc684e;
        --yellow: #e6aa22;
        --success: #15805d;
        --danger: #ad3f36;
      }

      * {
        box-sizing: border-box;
        letter-spacing: 0;
      }

      html {
        scroll-behavior: smooth;
      }

      body {
        margin: 0;
        min-width: 320px;
        background: #eef2f5;
      }

      button, input, select, textarea, summary {
        font: inherit;
      }

      button, .button-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 40px;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 9px 14px;
        background: var(--teal);
        color: #ffffff;
        font-weight: 700;
        text-decoration: none;
        cursor: pointer;
      }

      button:hover, .button-link:hover {
        background: var(--teal-dark);
      }

      button:focus-visible, .button-link:focus-visible, input:focus-visible, select:focus-visible,
      textarea:focus-visible, summary:focus-visible {
        outline: 3px solid rgba(49, 92, 200, 0.22);
        outline-offset: 2px;
      }

      button:disabled {
        cursor: not-allowed;
        opacity: 0.52;
      }

      .secondary-button {
        border-color: #cbd5dd;
        background: #ffffff;
        color: var(--ink);
      }

      .secondary-button:hover {
        background: #f3f6f8;
      }

      .app-shell {
        display: grid;
        grid-template-columns: 236px minmax(0, 1fr);
        min-height: 100vh;
      }

      .side-nav {
        position: sticky;
        top: 0;
        align-self: start;
        display: flex;
        flex-direction: column;
        min-height: 100vh;
        padding: 20px 14px;
        border-right: 1px solid var(--line);
        background: #fbfcfd;
        overflow-y: auto;
      }

      .brand {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 0 8px 22px;
        font-size: 20px;
        font-weight: 800;
        color: var(--ink);
      }

      .brand-mark {
        display: grid;
        place-items: center;
        width: 34px;
        height: 34px;
        border-radius: 7px;
        background: var(--teal);
        color: #ffffff;
        font-weight: 900;
      }

      .nav-list {
        display: grid;
        gap: 6px;
      }

      .nav-item {
        justify-content: flex-start;
        width: 100%;
        min-height: 42px;
        border-color: transparent;
        background: transparent;
        color: #445165;
        font-weight: 650;
      }

      .nav-item:hover, .nav-item.is-active {
        background: var(--mint);
        color: var(--teal-dark);
      }

      .nav-spacer {
        flex: 1;
      }

      .profile-row {
        padding: 14px 10px 4px;
        border-top: 1px solid var(--line);
      }

      .profile-row strong, .profile-row span {
        display: block;
      }

      .profile-row span {
        margin-top: 3px;
        color: var(--muted);
        font-size: 13px;
      }

      .app-main {
        min-width: 0;
        background: var(--surface);
      }

      .project-header {
        position: sticky;
        top: 0;
        z-index: 5;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 20px;
        padding: 18px 28px 0;
        border-bottom: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.96);
      }

      .project-title-row {
        min-width: 0;
      }

      .eyebrow {
        margin: 0 0 5px;
        color: var(--teal-dark);
        font-size: 12px;
        font-weight: 800;
        text-transform: uppercase;
      }

      h1, h2, h3, p {
        margin-top: 0;
      }

      h1 {
        margin-bottom: 4px;
        font-size: 24px;
        line-height: 1.3;
      }

      h2 {
        margin-bottom: 6px;
        font-size: 18px;
      }

      h3 {
        margin-bottom: 8px;
        font-size: 15px;
      }

      p {
        margin-bottom: 14px;
        color: var(--muted);
        line-height: 1.6;
      }

      .header-actions {
        display: flex;
        align-items: center;
        gap: 10px;
      }

      .project-tabs {
        grid-column: 1 / -1;
        display: flex;
        gap: 28px;
      }

      .project-tab {
        min-height: 38px;
        padding: 0 2px 10px;
        border: 0;
        border-bottom: 3px solid transparent;
        border-radius: 0;
        background: transparent;
        color: var(--muted);
      }

      .project-tab:hover, .project-tab.is-active {
        border-bottom-color: var(--teal);
        background: transparent;
        color: var(--ink);
      }

      .workspace-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 284px;
        gap: 20px;
        max-width: 1500px;
        margin: 0 auto;
        padding: 22px 24px 48px;
      }

      .source-panel, .right-rail {
        min-width: 0;
      }

      .source-panel {
        align-self: start;
        margin-top: 18px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--surface-soft);
      }

      .source-summary {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 8px;
        padding-bottom: 12px;
        border-bottom: 1px solid var(--line);
      }

      .source-summary strong {
        color: var(--teal-dark);
        font-size: 22px;
      }

      .source-list {
        display: grid;
        gap: 0;
        margin-top: 8px;
      }

      .source-row {
        padding: 11px 0;
        border-bottom: 1px solid var(--line);
      }

      .source-row:last-child {
        border-bottom: 0;
      }

      .source-row strong, .source-row span {
        display: block;
      }

      .source-row strong {
        font-size: 14px;
      }

      .source-row span {
        margin-top: 3px;
        color: var(--muted);
        font-size: 12px;
      }

      .center-column {
        display: grid;
        gap: 18px;
        min-width: 0;
      }

      .center-column > .product-section {
        width: 100%;
        min-width: 0;
        overflow: hidden;
      }

      .product-section {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #ffffff;
        scroll-margin-top: 190px;
      }

      .section-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        padding: 14px 18px;
        border-bottom: 1px solid var(--line);
      }

      .section-head p {
        margin-bottom: 0;
      }

      .stage-track {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        padding: 16px 18px;
        background: #fbfcfd;
      }

      .stage-step {
        position: relative;
        min-width: 0;
        padding: 24px 8px 0;
        border-top: 2px solid #cbd5dd;
        text-align: center;
      }

      .stage-step::before {
        position: absolute;
        top: -9px;
        left: calc(50% - 8px);
        width: 16px;
        height: 16px;
        border: 3px solid #ffffff;
        border-radius: 50%;
        background: #aab6c2;
        box-shadow: 0 0 0 1px #aab6c2;
        content: "";
      }

      .stage-step.is-complete {
        border-top-color: var(--teal);
      }

      .stage-step.is-complete::before, .stage-step.is-active::before {
        background: var(--teal);
        box-shadow: 0 0 0 1px var(--teal);
      }

      .stage-step.is-active {
        border-top-color: var(--teal);
        color: var(--teal-dark);
      }

      .stage-step strong, .stage-step span {
        display: block;
      }

      .stage-step strong {
        overflow-wrap: anywhere;
        font-size: 13px;
      }

      .stage-step span {
        margin-top: 5px;
        color: var(--muted);
        font-size: 11px;
      }

      .metric-strip {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 0 18px 12px;
        border: 1px solid var(--line);
        border-radius: 7px;
      }

      .metric {
        min-width: 0;
        padding: 11px 13px;
        border-right: 1px solid var(--line);
      }

      .metric:last-child {
        border-right: 0;
      }

      .metric > span, .metric > strong {
        display: block;
      }

      .metric > span {
        color: var(--muted);
        font-size: 12px;
      }

      .metric > strong {
        margin-top: 5px;
        overflow-wrap: anywhere;
        font-size: 18px;
      }

      .metric strong span {
        display: inline;
        color: inherit;
        font-size: inherit;
      }

      .task-message {
        margin: 0 18px 10px;
        padding: 9px 12px;
        border-left: 4px solid var(--yellow);
        background: #fff8e7;
        color: #775415;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .task-message:empty {
        display: none;
      }

      .task-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin: 0 18px 8px;
      }

      .task-footer {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 10px;
        align-items: center;
        margin: 0 18px 8px;
      }

      .task-footer .task-message,
      .task-footer .task-actions {
        margin: 0;
      }

      .task-meta {
        margin: 0 18px 13px;
        color: var(--muted);
        font-size: 12px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .preview-body {
        padding: 18px 20px 20px;
      }

      .slide-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }

      .slide-frame {
        position: relative;
        overflow: hidden;
        aspect-ratio: 16 / 9;
        border: 1px solid #cbd5dd;
        border-radius: 7px;
        background: #eff3f5;
      }

      .slide-frame iframe {
        display: block;
        width: 100%;
        height: 100%;
        border: 0;
        background: #eff3f5;
      }

      .slide-number {
        position: absolute;
        top: 8px;
        left: 8px;
        display: grid;
        place-items: center;
        width: 24px;
        height: 24px;
        border-radius: 5px;
        background: rgba(24, 34, 48, 0.82);
        color: #ffffff;
        font-size: 11px;
        font-weight: 800;
      }

      .preview-empty {
        display: grid;
        place-items: center;
        min-height: 190px;
        border: 1px dashed #b8c4cd;
        border-radius: 7px;
        color: var(--muted);
        text-align: center;
      }

      .chapter-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 16px;
        margin-top: 22px;
      }

      .chapter-strip {
        display: grid;
        grid-template-columns: repeat(8, minmax(0, 1fr));
        gap: 8px;
        margin-top: 10px;
      }

      .chapter-item {
        min-width: 0;
        padding: 10px 9px;
        border: 1px solid var(--line);
        border-top: 5px solid var(--chapter-color, var(--teal));
        border-radius: 6px;
        background: #ffffff;
      }

      .chapter-item strong, .chapter-item span {
        display: block;
      }

      .chapter-item strong {
        min-height: 38px;
        overflow-wrap: anywhere;
        font-size: 12px;
      }

      .chapter-item span {
        margin-top: 7px;
        color: var(--muted);
        font-size: 11px;
      }

      .create-panel {
        padding: 0;
      }

      .interview-shell {
        display: grid;
        grid-template-columns: minmax(0, 1.25fr) minmax(300px, 0.75fr);
      }

      .conversation-pane {
        display: grid;
        grid-template-rows: minmax(180px, 1fr) auto auto;
        min-width: 0;
        border-right: 1px solid var(--line);
      }

      .conversation-stream {
        display: grid;
        align-content: start;
        gap: 12px;
        max-height: 420px;
        min-height: 180px;
        padding: 18px;
        overflow-y: auto;
      }

      .conversation-message {
        max-width: 84%;
        padding: 10px 12px;
        border-left: 3px solid var(--teal);
        background: #f3f7f7;
        color: #334154;
        font-size: 13px;
        line-height: 1.6;
        white-space: pre-wrap;
      }

      .conversation-message[data-role="user"] {
        justify-self: end;
        border-right: 3px solid var(--cobalt);
        border-left: 0;
        background: #eef3ff;
      }

      .conversation-message strong {
        display: block;
        margin-bottom: 3px;
        color: var(--ink);
        font-size: 11px;
      }

      .interview-question-panel {
        margin: 0 18px 14px;
        border-radius: 8px;
        padding: 16px;
        background: #2c2f2e;
        color: #f7f8f7;
      }

      .interview-question-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 12px;
      }

      .interview-question-head h3 {
        margin: 0;
        color: #ffffff;
        font-size: 16px;
        line-height: 1.5;
      }

      .interview-round {
        flex: 0 0 auto;
        color: #aeb5b2;
        font-size: 11px;
        white-space: nowrap;
      }

      .interview-options {
        display: grid;
      }

      .interview-option {
        display: grid;
        grid-template-columns: 34px minmax(0, 1fr) auto;
        gap: 11px;
        justify-content: stretch;
        min-height: 54px;
        border: 0;
        border-top: 1px solid #494d4b;
        border-radius: 0;
        padding: 9px 0;
        background: transparent;
        color: #e8ebe9;
        text-align: left;
      }

      .interview-option:first-child {
        border-top: 0;
      }

      .interview-option:hover {
        background: #3a3e3c;
      }

      .option-number {
        display: grid;
        place-items: center;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        background: #454947;
        color: #d7dcda;
        font-weight: 800;
      }

      .option-copy strong, .option-copy span {
        display: block;
      }

      .option-copy strong {
        color: #ffffff;
        font-size: 13px;
      }

      .option-copy span {
        margin-top: 2px;
        color: #aeb5b2;
        font-size: 11px;
        font-weight: 500;
      }

      .option-arrow {
        align-self: center;
        color: #aeb5b2;
        font-size: 20px;
      }

      .interview-question-foot {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 12px;
        margin-top: 10px;
        padding-top: 10px;
        border-top: 1px solid #494d4b;
      }

      .interview-question-foot button {
        min-height: 34px;
        border-color: #5a5f5c;
        background: #454947;
        color: #ffffff;
        font-size: 12px;
      }

      .interview-composer {
        padding: 14px 18px 18px;
        border-top: 1px solid var(--line);
        background: #fbfcfd;
      }

      .interview-composer textarea {
        min-height: 88px;
        border-color: #aebbc5;
        background: #ffffff;
      }

      .interview-question-panel .interview-composer {
        margin-top: 12px;
        padding: 12px 0 0;
        border-top: 1px solid #494d4b;
        background: transparent;
      }

      .interview-question-panel .interview-composer textarea {
        min-height: 68px;
        border-color: #6d7470;
      }

      .interview-question-panel .composer-actions span {
        color: #aeb5b2;
      }

      .conversation-message.is-pending {
        color: var(--muted);
        font-style: italic;
      }

      .generation-confirmation {
        margin: 0 18px 18px;
        border: 1px solid #9bcfc7;
        border-radius: 8px;
        padding: 16px;
        background: #effaf7;
      }

      .generation-confirmation h3 {
        margin-bottom: 5px;
        font-size: 17px;
      }

      .generation-confirmation > p {
        margin-bottom: 12px;
        font-size: 12px;
      }

      .confirmation-summary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        border-top: 1px solid #c6e4df;
        border-bottom: 1px solid #c6e4df;
      }

      .confirmation-summary div {
        min-width: 0;
        padding: 10px 8px 10px 0;
      }

      .confirmation-summary span,
      .confirmation-summary strong {
        display: block;
      }

      .confirmation-summary span {
        color: var(--muted);
        font-size: 10px;
      }

      .confirmation-summary strong {
        margin-top: 3px;
        overflow-wrap: anywhere;
        color: var(--ink);
        font-size: 12px;
      }

      .confirmation-actions {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        margin-top: 14px;
      }

      .composer-actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-top: 9px;
      }

      .composer-actions span {
        color: var(--muted);
        font-size: 11px;
      }

      .brief-pane {
        min-width: 0;
        padding: 18px;
        background: var(--surface-soft);
      }

      .brief-pane-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 10px;
        padding-bottom: 13px;
        border-bottom: 1px solid var(--line);
      }

      .brief-pane-head h3 {
        margin-bottom: 3px;
      }

      .brief-status {
        border-radius: 4px;
        padding: 4px 7px;
        background: #e8edef;
        color: var(--muted);
        font-size: 11px;
        font-weight: 800;
      }

      .brief-status.is-ready {
        background: #e2f3eb;
        color: var(--success);
      }

      .brief-summary-grid {
        display: grid;
        gap: 0;
        margin: 10px 0 14px;
      }

      .brief-summary-row {
        padding: 9px 0;
        border-bottom: 1px solid var(--line);
      }

      .brief-summary-row span, .brief-summary-row strong {
        display: block;
      }

      .brief-summary-row span {
        color: var(--muted);
        font-size: 11px;
      }

      .brief-summary-row strong {
        margin-top: 3px;
        overflow-wrap: anywhere;
        font-size: 13px;
        line-height: 1.45;
      }

      .brief-pane .embedded-form {
        margin-top: 12px;
      }

      .brief-pane .form-grid {
        grid-template-columns: 1fr;
      }

      .brief-pane .full {
        grid-column: auto;
      }

      .brief-pane textarea {
        min-height: 180px;
      }

      .brief-pane .form-actions {
        align-items: stretch;
        flex-direction: column;
      }

      .brief-pane .form-actions button {
        width: 100%;
      }

      .interview-section-actions {
        display: flex;
        gap: 8px;
      }

      .form-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }

      label {
        display: grid;
        gap: 6px;
        min-width: 0;
        color: #334154;
        font-size: 13px;
        font-weight: 700;
      }

      input, select, textarea {
        width: 100%;
        min-width: 0;
        border: 1px solid #bdc9d2;
        border-radius: 6px;
        padding: 10px 11px;
        background: #ffffff;
        color: var(--ink);
      }

      textarea {
        min-height: 110px;
        resize: vertical;
        line-height: 1.55;
      }

      .full {
        grid-column: 1 / -1;
      }

      .advanced-panel, .technical-panel {
        margin-top: 14px;
        border: 1px solid var(--line);
        border-radius: 7px;
        background: #fbfcfd;
      }

      summary {
        padding: 13px 15px;
        color: #3f4c5e;
        font-weight: 750;
        cursor: pointer;
      }

      details[open] > summary {
        border-bottom: 1px solid var(--line);
      }

      .details-body {
        padding: 15px;
      }

      .form-actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
        margin-top: 16px;
      }

      .form-actions p {
        margin-bottom: 0;
        font-size: 12px;
      }

      .right-rail {
        display: grid;
        align-content: start;
        align-self: start;
        gap: 16px;
      }

      .task-meta {
        overflow-wrap: anywhere;
      }

      .rail-panel {
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #ffffff;
      }

      .delivery-file {
        margin: 12px 0;
        padding: 12px;
        border: 1px solid var(--line);
        border-radius: 7px;
        background: var(--surface-soft);
      }

      .delivery-file strong, .delivery-file span {
        display: block;
        overflow-wrap: anywhere;
      }

      .delivery-file span {
        margin-top: 5px;
        color: var(--muted);
        font-size: 12px;
      }

      .download-stack {
        display: grid;
        gap: 8px;
      }

      .health-score {
        display: flex;
        align-items: baseline;
        gap: 8px;
        margin: 8px 0 12px;
      }

      .health-score strong {
        color: var(--teal-dark);
        font-size: 42px;
      }

      .health-score span {
        color: var(--success);
        font-weight: 800;
      }

      .health-list {
        display: grid;
        gap: 9px;
        padding: 0;
        list-style: none;
      }

      .health-list li {
        padding-left: 13px;
        border-left: 3px solid var(--teal);
        color: #3f4c5e;
        font-size: 13px;
        line-height: 1.45;
      }

      .health-list li.is-warning {
        border-left-color: var(--coral);
      }

      .quality-list {
        display: grid;
        gap: 10px;
        margin: 12px 0 0;
      }

      .quality-row {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        padding-bottom: 9px;
        border-bottom: 1px solid var(--line);
        font-size: 13px;
      }

      .history-toolbar {
        display: grid;
        grid-template-columns: minmax(220px, 1fr) 190px auto;
        gap: 10px;
        padding: 16px 20px;
        border-bottom: 1px solid var(--line);
        background: var(--surface-soft);
      }

      .history-toolbar input, .history-toolbar select {
        width: 100%;
        min-height: 40px;
        border: 1px solid #bdcad4;
        border-radius: 6px;
        padding: 8px 11px;
        background: #ffffff;
        color: var(--ink);
      }

      .history-summary {
        margin: 0;
        padding: 10px 20px;
        border-bottom: 1px solid var(--line);
        font-size: 12px;
      }

      .history-list {
        display: grid;
      }

      .history-row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(110px, auto) minmax(180px, auto);
        align-items: center;
        gap: 18px;
        min-width: 0;
        padding: 16px 20px;
        border-bottom: 1px solid var(--line);
      }

      .history-row:last-child {
        border-bottom: 0;
      }

      .history-row:hover {
        background: #f8fbfb;
      }

      .history-title {
        margin: 0 0 5px;
        color: var(--ink);
        font-size: 15px;
        line-height: 1.4;
      }

      .history-meta, .history-requirements {
        margin: 0;
        color: var(--muted);
        font-size: 12px;
        line-height: 1.5;
      }

      .history-requirements {
        display: -webkit-box;
        margin-top: 4px;
        overflow: hidden;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
      }

      .history-state {
        display: grid;
        justify-items: start;
        gap: 5px;
      }

      .history-status {
        display: inline-flex;
        align-items: center;
        min-height: 26px;
        border-radius: 4px;
        padding: 4px 8px;
        background: #edf1f4;
        color: #4d5b6c;
        font-size: 12px;
        font-weight: 800;
      }

      .history-status[data-tone="success"] {
        background: #e7f5ef;
        color: var(--success);
      }

      .history-status[data-tone="warning"] {
        background: #fff4dc;
        color: #93640a;
      }

      .history-status[data-tone="danger"] {
        background: #fbeae7;
        color: var(--danger);
      }

      .history-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }

      .history-actions button, .history-actions .button-link {
        min-height: 36px;
        padding: 7px 10px;
        font-size: 12px;
      }

      .history-empty {
        padding: 34px 20px;
        color: var(--muted);
        text-align: center;
      }

      .technical-wrap {
        padding: 18px 20px 20px;
      }

      #artifacts {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px 14px;
        margin: 0;
        padding: 0;
        list-style: none;
      }

      #artifacts li {
        min-width: 0;
        padding: 8px 0;
        border-bottom: 1px solid var(--line);
      }

      #artifacts a {
        color: var(--cobalt);
        font-size: 13px;
        font-weight: 700;
        overflow-wrap: anywhere;
      }

      .artifact-group-label {
        grid-column: 1 / -1;
        margin-top: 8px;
        border-bottom-color: #bac7d1 !important;
        color: var(--ink);
        font-weight: 850;
      }

      .metadata-grid {
        display: grid;
        grid-template-columns: minmax(130px, 0.3fr) minmax(0, 1fr);
        gap: 8px 14px;
        margin: 0 0 16px;
      }

      .metadata-grid dt {
        color: var(--muted);
        font-weight: 700;
      }

      .metadata-grid dd {
        min-width: 0;
        margin: 0;
        overflow-wrap: anywhere;
        white-space: pre-wrap;
      }

      .hint {
        margin-bottom: 0;
        font-size: 12px;
      }

      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
      }

      [hidden] {
        display: none !important;
      }

      @media (max-width: 1220px) {
        .workspace-grid {
          grid-template-columns: 1fr;
        }

        .right-rail {
          grid-column: auto;
          grid-template-columns: repeat(3, minmax(0, 1fr));
        }

      }

      @media (max-width: 920px) {
        .app-shell {
          grid-template-columns: 1fr;
        }

        .side-nav {
          position: static;
          min-height: auto;
          border-right: 0;
          border-bottom: 1px solid var(--line);
        }

        .nav-list {
          grid-template-columns: repeat(4, minmax(0, 1fr));
        }

        .profile-row, .nav-spacer {
          display: none;
        }

        .side-nav .source-panel {
          display: none;
        }

        .project-header {
          position: static;
        }

        .product-section {
          scroll-margin-top: 16px;
        }

        .workspace-grid {
          grid-template-columns: 1fr;
        }

        .interview-shell {
          grid-template-columns: 1fr;
        }

        .conversation-pane {
          border-right: 0;
          border-bottom: 1px solid var(--line);
        }

        .right-rail {
          grid-template-columns: 1fr;
        }
      }

      @media (max-width: 680px) {
        .project-header {
          grid-template-columns: 1fr;
          padding: 16px 16px 0;
        }

        .header-actions {
          flex-wrap: wrap;
        }

        .project-tabs {
          gap: 18px;
          overflow-x: auto;
        }

        .workspace-grid {
          padding: 14px 12px 32px;
        }

        .stage-track, .metric-strip, .slide-grid, .form-grid,
        .chapter-strip, #artifacts {
          grid-template-columns: 1fr;
        }

        .stage-step {
          padding: 10px 0 10px 28px;
          border-top: 0;
          border-left: 2px solid #cbd5dd;
          text-align: left;
        }

        .stage-step::before {
          top: 12px;
          left: -9px;
        }

        .metric {
          border-right: 0;
          border-bottom: 1px solid var(--line);
        }

        .metric:last-child {
          border-bottom: 0;
        }

        .nav-list {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .form-actions {
          align-items: stretch;
          flex-direction: column;
        }

        .task-footer {
          grid-template-columns: 1fr;
        }

        .history-toolbar, .history-row {
          grid-template-columns: 1fr;
        }

        .history-actions {
          justify-content: stretch;
        }

        .history-actions > * {
          flex: 1;
        }

        .section-head, .interview-question-head, .composer-actions, .confirmation-actions,
        .interview-question-foot, .brief-pane-head {
          align-items: stretch;
          flex-direction: column;
        }

        .interview-section-actions {
          width: 100%;
        }

        .interview-section-actions button {
          flex: 1;
        }

        .conversation-message {
          max-width: 94%;
        }
      }
    </style>
  </head>
  <body>
    <div class="app-shell">
      <aside class="side-nav" aria-label="主导航">
        <div class="brand"><span class="brand-mark">P</span><span>ppt-agent</span></div>
        <nav class="nav-list">
          <button class="nav-item is-active" type="button" data-scroll-target="projectWorkspace">项目工作台</button>
          <button class="nav-item" type="button" data-scroll-target="createPanel">创建演示</button>
          <button class="nav-item" type="button" data-scroll-target="historyPanel">演示历史</button>
          <button class="nav-item" type="button" data-scroll-target="previewPanel">页面预览</button>
          <button class="nav-item" type="button" data-scroll-target="technicalPanel">技术详情</button>
        </nav>
        <aside class="source-panel" aria-labelledby="sourcePanelTitle">
          <div class="source-summary">
            <div><p class="eyebrow">Source coverage</p><h2 id="sourcePanelTitle">资料覆盖度</h2></div>
            <strong id="sourceCoverageScore">0%</strong>
          </div>
          <div class="source-list">
            <div class="source-row"><strong>需求简报</strong><span id="sourceRequestState">等待任务</span></div>
            <div class="source-row"><strong>章节规划</strong><span id="sourcePlanState">等待任务</span></div>
            <div class="source-row"><strong>页面内容</strong><span id="sourceDeckState">等待任务</span></div>
            <div class="source-row"><strong>质量证据</strong><span id="sourceQaState">等待任务</span></div>
            <div class="source-row"><strong>可编辑成片</strong><span id="sourceOutputState">等待任务</span></div>
          </div>
          <p class="hint">覆盖度来自当前 job 的真实 artifacts，不代表外部资料可信度评分。</p>
        </aside>
        <div class="nav-spacer"></div>
        <div class="profile-row"><strong>Local workspace</strong><span>内容、质量与交付统一管理</span></div>
      </aside>

      <main class="app-main">
        <header class="project-header">
          <div class="project-title-row">
            <p class="eyebrow">Presentation workspace</p>
            <h1 id="projectTitle">AI 产品经理如何设计 Agent 产品</h1>
            <p>从资料、叙事到可编辑成片，一次看清当前进度和下一步。</p>
          </div>
          <div class="header-actions">
            <button class="secondary-button" type="button" data-scroll-target="createPanel">新建任务</button>
            <a id="primaryDownloadTop" class="button-link" href="#technicalPanel">查看交付</a>
          </div>
          <nav class="project-tabs" aria-label="项目阶段">
            <button class="project-tab" type="button" data-scroll-target="createPanel">大纲</button>
            <button class="project-tab is-active" type="button" data-scroll-target="projectWorkspace">生成</button>
            <button class="project-tab" type="button" data-scroll-target="previewPanel">预览</button>
            <button class="project-tab" type="button" data-scroll-target="technicalPanel">交付</button>
          </nav>
        </header>

        <div class="workspace-grid">
          <div class="center-column">
            <section id="projectWorkspace" class="product-section">
              <div class="section-head">
                <div><p class="eyebrow">Live task</p><h2>生成进度</h2><p>每一步都对应可检查的中间产物。</p></div>
                <span id="jobStatus">未开始</span>
              </div>
              <div id="stageTrack" class="stage-track" aria-label="生成阶段">
                <div class="stage-step" data-stage="planning"><strong>1. 内容规划</strong><span>大纲与结构</span></div>
                <div class="stage-step" data-stage="generating"><strong>2. 页面生成</strong><span id="stageGenerationDetail">0 / 30 页</span></div>
                <div class="stage-step" data-stage="quality"><strong>3. 全页质检</strong><span id="stageQualityDetail">等待生成</span></div>
                <div class="stage-step" data-stage="export"><strong>4. 可编辑导出</strong><span id="stageExportDetail">等待质检</span></div>
                <div class="stage-step" data-stage="done"><strong>5. 完成</strong><span id="stageDoneDetail">等待交付</span></div>
              </div>
              <div class="metric-strip">
                <div class="metric"><span>任务进度</span><strong><span id="completedBatches">0</span> / <span id="totalBatches">0</span></strong></div>
                <div class="metric"><span>质量结果</span><strong id="qualitySummary">未评估</strong></div>
                <div class="metric"><span>生成结果</span><strong id="outputSummary">未生成</strong></div>
                <div class="metric"><span>预计难度</span><strong id="effortSummary">标准</strong></div>
              </div>
              <p id="longRunningNotice" class="task-message"></p>
              <div class="task-footer">
                <p id="errorMessage" class="task-message"></p>
                <div class="task-actions">
                  <button id="cancelJobButton" class="secondary-button" type="button" disabled>取消任务</button>
                  <button id="resumeJobButton" type="button" disabled>继续/重试演示</button>
                </div>
              </div>
              <p class="task-meta">任务 ID：<span id="jobId">暂无</span> · 当前阶段：<span id="currentStage">暂无</span> · 进度单元 <span id="currentBatch">暂无</span> / <span id="totalBatchesMeta">0</span> · 失败 <span id="failedBatches">0</span> · 运行 <span id="elapsedSeconds">0</span> 秒</p>
            </section>

            <section id="previewPanel" class="product-section">
              <div class="section-head">
                <div><p class="eyebrow">Storyboard</p><h2>演示预览</h2><p id="previewSummary">生成后从真实 SVG visual project 读取代表页面。</p></div>
                <a id="primaryDownloadLink" class="button-link" href="#technicalPanel">查看全部文件</a>
              </div>
              <div class="preview-body">
                <div id="slideGrid" class="slide-grid">
                  <div class="slide-frame"><span class="slide-number" id="previewSlideNumber1">1</span><iframe id="previewSlide1" title="第 1 页预览" sandbox="allow-scripts" loading="lazy" hidden></iframe></div>
                  <div class="slide-frame"><span class="slide-number" id="previewSlideNumber2">2</span><iframe id="previewSlide2" title="中间页面预览" sandbox="allow-scripts" loading="lazy" hidden></iframe></div>
                  <div class="slide-frame"><span class="slide-number" id="previewSlideNumber3">3</span><iframe id="previewSlide3" title="末页预览" sandbox="allow-scripts" loading="lazy" hidden></iframe></div>
                </div>
                <div id="previewEmpty" class="preview-empty">页面生成后会在这里逐步出现，无需等待整份 PPT 完成。</div>
                <div class="chapter-head"><div><p class="eyebrow">Editable outline</p><h3>章节页数分配</h3></div><span id="chapterTotal">当前 30 页</span></div>
                <div id="chapterStrip" class="chapter-strip"></div>
              </div>
            </section>

            <section id="createPanel" class="product-section">
              <div class="section-head">
                <div><p class="eyebrow">Create with Agent</p><h2>和 Agent 一起定义演示</h2><p>像聊天一样说出想法。Agent 理解充分后会直接准备生成；需要时可以继续对话调整。</p></div>
                <div class="interview-section-actions">
                  <button id="manualBriefButton" class="secondary-button" type="button">手动调整</button>
                  <button id="newInterviewButton" class="secondary-button" type="button">重新开始</button>
                </div>
              </div>
              <div class="create-panel">
                <div class="interview-shell">
                  <div class="conversation-pane">
                    <div id="interviewMessages" class="conversation-stream" aria-live="polite">
                      <div class="conversation-message" data-role="assistant"><strong>PPT Agent</strong>告诉我你想做什么演示。哪怕只有一个模糊想法也可以，我会一步一步帮你把内容、观众、页数和视觉方向问清楚。</div>
                    </div>
                    <section id="interviewQuestionPanel" class="interview-question-panel" hidden>
                      <div class="interview-question-head">
                        <h3 id="interviewQuestion">Agent 正在整理需求</h3>
                        <span id="interviewRound" class="interview-round">自适应访谈</span>
                      </div>
                      <div id="interviewOptions" class="interview-options"></div>
                      <div class="interview-question-foot">
                        <button id="skipInterviewQuestionButton" type="button">不确定，暂时跳过</button>
                      </div>
                    </section>
                    <section id="generationConfirmation" class="generation-confirmation" hidden>
                      <p class="eyebrow">Ready to create</p>
                      <h3>Agent 已经理解，可以开始生成</h3>
                      <p>长演示会在正式生成前保留这次确认，避免页数、观众或视觉方向理解错误。</p>
                      <div class="confirmation-summary">
                        <div><span>主题</span><strong id="confirmationTopic">待确认</strong></div>
                        <div><span>目标观众</span><strong id="confirmationAudience">待确认</strong></div>
                        <div><span>页数</span><strong id="confirmationSlideCount">待确认</strong></div>
                        <div><span>视觉方向</span><strong id="confirmationVisual">待确认</strong></div>
                      </div>
                      <div class="confirmation-actions">
                        <button id="continueInterviewButton" class="secondary-button" type="button">继续调整</button>
                        <button id="confirmGenerationButton" type="button">开始生成 PPT</button>
                      </div>
                    </section>
                    <form id="interviewComposer" class="interview-composer">
                      <label class="sr-only" for="interviewInput">告诉 Agent 你的演示需求</label>
                      <textarea id="interviewInput" required placeholder="例如：我想做一份给大学生看的生态环境保护演示，但还不知道从哪里开始。"></textarea>
                      <div class="composer-actions">
                        <span id="interviewHint">描述越具体，Agent 需要追问的问题越少。</span>
                        <button id="sendInterviewButton" type="submit">发送给 Agent</button>
                      </div>
                    </form>
                  </div>

                  <aside class="brief-pane" aria-labelledby="briefPaneTitle">
                    <div class="brief-pane-head">
                      <div><p class="eyebrow">Agent understanding</p><h3 id="briefPaneTitle">Agent 理解</h3></div>
                      <span id="briefStatus" class="brief-status">等待描述</span>
                    </div>
                    <div class="brief-summary-grid">
                      <div class="brief-summary-row"><span>主题</span><strong id="briefTopic">待确认</strong></div>
                      <div class="brief-summary-row"><span>目标观众</span><strong id="briefAudience">待确认</strong></div>
                      <div class="brief-summary-row"><span>页数</span><strong id="briefSlideCount">待确认</strong></div>
                      <div class="brief-summary-row"><span>用途与内容重点</span><strong id="briefFocus">待确认</strong></div>
                      <div class="brief-summary-row"><span>视觉方向</span><strong id="briefVisual">待确认</strong></div>
                    </div>
                    <p id="briefReadinessHint" class="hint">这里会实时显示 Agent 当前理解，不需要你填写表单。</p>
                    <form id="longDeckForm" class="embedded-form" hidden>
                      <div class="form-grid">
                        <label>主题<input id="long_topic" name="topic" required placeholder="例如：从 0 到 1 设计未来智慧校园"></label>
                        <label>目标观众<input id="long_audience" name="audience" required placeholder="例如：大学生与年轻产品经理"></label>
                        <label>页数<input id="long_slide_count" name="slide_count" type="number" min="1" max="100" step="1" value="30" required></label>
                        <label class="full">PPT 详细要求<textarea id="long_user_requirements" name="user_requirements" required placeholder="写清内容重点、表达风格、章节要求与视觉偏好；系统会保存在当前浏览器。"></textarea></label>
                      </div>
                      <div class="form-actions"><p id="generationStrategyHint">系统会根据页数自动选择生成模式并检查内容质量。</p><button id="generateLongDeckButton" type="submit">确认并生成 30 页 PPT</button></div>
                    </form>
                  </aside>
                </div>
              </div>
            </section>

            <section id="historyPanel" class="product-section">
              <div class="section-head">
                <div><p class="eyebrow">Presentation history</p><h2>演示历史</h2><p>创建请求、生成状态和最终可编辑 PPTX 都保存在本地 SQLite 工作区。</p></div>
              </div>
              <div class="history-toolbar">
                <label class="sr-only" for="historySearch">搜索历史演示</label>
                <input id="historySearch" type="search" placeholder="搜索主题、观众或任务 ID">
                <label class="sr-only" for="historyStatusFilter">按状态筛选</label>
                <select id="historyStatusFilter">
                  <option value="">全部状态</option>
                  <option value="succeeded">已交付</option>
                  <option value="running">生成中</option>
                  <option value="pending">排队中</option>
                  <option value="failed_quality_gate">质量门禁失败</option>
                  <option value="failed">运行失败</option>
                  <option value="cancelled">已取消</option>
                </select>
                <button id="refreshHistoryButton" class="secondary-button" type="button">刷新</button>
              </div>
              <p id="historySummary" class="history-summary">正在读取本地历史记录...</p>
              <div id="historyList" class="history-list" aria-live="polite"></div>
              <div id="historyEmpty" class="history-empty" hidden>还没有演示记录。创建第一份演示后会自动出现在这里。</div>
            </section>

            <section id="technicalPanel" class="product-section">
              <div class="section-head"><div><p class="eyebrow">Artifacts</p><h2>交付与技术详情</h2><p>成片、质量证据、源文件和 PPT Master 链路按用途整理。</p></div></div>
              <div class="technical-wrap">
                <details class="technical-panel">
                  <summary>查看全部生成文件</summary>
                  <div class="details-body"><h3>生成文件</h3><ul id="artifacts"></ul></div>
                </details>
                <details class="technical-panel">
                  <summary>PPT Master 技术链路</summary>
                  <div class="details-body">
      <section id="pptMasterPackageSection" hidden>
        <h2>PPT Master 渲染包</h2>
        <p id="pptMasterPackageMessage"></p>
        <dl class="metadata-grid">
          <dt>package 状态</dt>
          <dd id="pptMasterGenerated">未生成</dd>
          <dt>原因</dt>
          <dd id="pptMasterReason">未评估</dd>
          <dt>建议</dt>
          <dd id="pptMasterSuggestion">无</dd>
          <dt>ppt-master 检测</dt>
          <dd id="pptMasterAvailable">未知</dd>
          <dt>官方仓库</dt>
          <dd id="pptMasterExpectedRepo">未知</dd>
          <dt>package_mode</dt>
          <dd id="pptMasterPackageMode">未知</dd>
          <dt>quality_gate</dt>
          <dd id="pptMasterQualityGate">未知</dd>
          <dt>PPT_MASTER_DIR / root</dt>
          <dd id="pptMasterRoot">未检测到</dd>
          <dt>missing_paths</dt>
          <dd id="pptMasterMissingPaths">无</dd>
        </dl>
        <p class="hint">当前阶段不会自动运行 ppt-master，只提供 handoff package。用户可以把 run_prompt.md 交给本地 ppt-master workflow 使用。</p>
      </section>

      <section id="pptMasterExecutionSection" hidden>
        <h2>PPT Master 执行桥</h2>
        <p id="pptMasterExecutionMessage"></p>
        <dl class="metadata-grid">
          <dt>execution status</dt>
          <dd id="pptMasterExecutionStatus">未准备</dd>
          <dt>output_dir</dt>
          <dd id="pptMasterExecutionOutputDir">未检测到</dd>
          <dt>expected pptx</dt>
          <dd id="pptMasterExecutionExpectedPptx">未检测到</dd>
          <dt>execution plan</dt>
          <dd id="pptMasterExecutionPlanState">未生成</dd>
          <dt>下一步</dt>
          <dd id="pptMasterExecutionSteps">无</dd>
        </dl>
        <button id="preparePptMasterExecutionButton" type="button" disabled>准备 PPT Master 执行计划</button>
        <p class="hint">执行桥只生成 plan 和注册已有输出；当前阶段不会自动运行 ppt-master。</p>
      </section>

      <section id="pptMasterVisualProjectSection" hidden>
        <h2>PPT Master Visual Project</h2>
        <p id="pptMasterVisualProjectMessage"></p>
        <dl class="metadata-grid">
          <dt>bootstrap status</dt>
          <dd id="pptMasterVisualProjectStatus">未准备</dd>
          <dt>project_dir</dt>
          <dd id="pptMasterVisualProjectDir">未检测到</dd>
          <dt>PROJECT_INSTRUCTIONS.md</dt>
          <dd id="pptMasterVisualProjectInstructionsState">未生成</dd>
          <dt>source.md</dt>
          <dd id="pptMasterVisualProjectSourcePath">未检测到</dd>
          <dt>run_prompt.md</dt>
          <dd id="pptMasterVisualProjectPromptPath">未检测到</dd>
          <dt>svg_output</dt>
          <dd id="pptMasterVisualProjectSvgOutput">未检测到</dd>
          <dt>svg_final</dt>
          <dd id="pptMasterVisualProjectSvgFinal">未检测到</dd>
          <dt>expected pptx</dt>
          <dd id="pptMasterVisualProjectExpectedPptx">未检测到</dd>
          <dt>下一步</dt>
          <dd id="pptMasterVisualProjectSteps">无</dd>
        </dl>
        <button id="bootstrapPptMasterProjectButton" type="button" disabled>准备 PPT Master Visual Project</button>
        <p class="hint">只创建本地 project scaffold，不调用模型、不生成 SVG、不运行完整 ppt-master workflow。</p>
      </section>

      <section id="pptMasterRunnerSection" hidden>
        <h2>PPT Master 本地导出</h2>
        <p id="pptMasterRunnerMessage"></p>
        <dl class="metadata-grid">
          <dt>runner status</dt>
          <dd id="pptMasterRunnerStatus">未运行</dd>
          <dt>需要外部 AI 生成 project</dt>
          <dd id="pptMasterRunnerRequiresExternal">未知</dd>
          <dt>project_dir</dt>
          <dd id="pptMasterRunnerProjectDir">未检测到</dd>
          <dt>output_dir</dt>
          <dd id="pptMasterRunnerOutputDir">未检测到</dd>
          <dt>pptx_path</dt>
          <dd id="pptMasterRunnerPptxPath">未检测到</dd>
          <dt>slide_count</dt>
          <dd id="pptMasterRunnerSlideCount">未知</dd>
          <dt>registered</dt>
          <dd id="pptMasterRunnerRegistered">false</dd>
          <dt>runner result</dt>
          <dd id="pptMasterRunnerResultState">未生成</dd>
        </dl>
        <button id="runPptMasterLocalExportButton" type="button" disabled>运行 PPT Master 本地导出</button>
        <p class="hint">不会调用模型，只运行本地可脚本化导出步骤；如果还没有 visual project，会提示先用本地 AI IDE / ppt-master skill 生成。</p>
      </section>

      <section id="pptMasterOutputSection" hidden>
        <h2>PPT Master 生成结果</h2>
        <p id="pptMasterOutputMessage"></p>
        <dl class="metadata-grid">
          <dt>是否检测到 PPTX</dt>
          <dd id="pptMasterOutputDetected">未检测到</dd>
          <dt>slide_count</dt>
          <dd id="pptMasterOutputSlideCount">未知</dd>
          <dt>generation_status</dt>
          <dd id="pptMasterOutputGenerationStatus">未知</dd>
          <dt>output_dir</dt>
          <dd id="pptMasterOutputDir">未检测到</dd>
          <dt>generation_notes.md</dt>
          <dd id="pptMasterOutputHasNotes">未知</dd>
        </dl>
        <p class="hint">如果这里还没有结果，请先用本地 PPT Master package 生成 PPTX，再运行注册脚本。</p>
      </section>
                  </div>
                </details>
              </div>
            </section>
          </div>

          <aside class="right-rail">
            <section class="rail-panel">
              <p class="eyebrow">Editable delivery</p><h2>可编辑成片</h2>
              <div class="delivery-file"><strong id="deliveryFileName">等待生成 PPTX</strong><span id="deliveryStatus">生成后可直接下载</span></div>
              <div class="quality-list">
                <div class="quality-row"><span>页数</span><strong id="deliverySlideCount">未知</strong></div>
                <div class="quality-row"><span>编辑性</span><strong id="deliveryEditable">原生对象优先</strong></div>
              </div>
              <div class="download-stack"><a id="deliveryDownload" class="button-link" href="#technicalPanel">查看交付文件</a></div>
            </section>

            <section class="rail-panel">
              <p class="eyebrow">Narrative health</p><h2>叙事健康度</h2>
              <div class="health-score"><strong id="narrativeHealthScore">--</strong><span id="narrativeHealthLabel">等待评估</span></div>
              <ul class="health-list">
                <li id="healthStructure">章节结构尚未生成</li>
                <li id="healthQuality">质量门禁尚未运行</li>
                <li id="healthDelivery">可编辑成片尚未注册</li>
              </ul>
            </section>

            <section class="rail-panel">
              <p class="eyebrow">Quality & cost</p><h2>质量与成本</h2>
              <div class="quality-list">
                <div class="quality-row"><span>质量门禁</span><strong id="railQualityStatus">未评估</strong></div>
                <div class="quality-row"><span>失败 batches</span><strong id="railFailedBatches">0</strong></div>
                <div class="quality-row"><span>运行时间</span><strong><span id="railElapsedSeconds">0</span> 秒</strong></div>
                <div class="quality-row"><span>预算状态</span><strong>当前 Web 未计费</strong></div>
              </div>
            </section>
          </aside>
        </div>
      </main>
    </div>

    <script>
      const longDeckForm = document.getElementById("longDeckForm");
      const longDeckButton = document.getElementById("generateLongDeckButton");
      const longSlideCount = document.getElementById("long_slide_count");
      const generationStrategyHint = document.getElementById("generationStrategyHint");
      const jobId = document.getElementById("jobId");
      const jobStatus = document.getElementById("jobStatus");
      const currentStage = document.getElementById("currentStage");
      const currentBatch = document.getElementById("currentBatch");
      const totalBatches = document.getElementById("totalBatches");
      const completedBatches = document.getElementById("completedBatches");
      const failedBatches = document.getElementById("failedBatches");
      const elapsedSeconds = document.getElementById("elapsedSeconds");
      const longRunningNotice = document.getElementById("longRunningNotice");
      const errorMessage = document.getElementById("errorMessage");
      const artifacts = document.getElementById("artifacts");
      const pptMasterPackageSection = document.getElementById("pptMasterPackageSection");
      const pptMasterPackageMessage = document.getElementById("pptMasterPackageMessage");
      const pptMasterGenerated = document.getElementById("pptMasterGenerated");
      const pptMasterReason = document.getElementById("pptMasterReason");
      const pptMasterSuggestion = document.getElementById("pptMasterSuggestion");
      const pptMasterAvailable = document.getElementById("pptMasterAvailable");
      const pptMasterExpectedRepo = document.getElementById("pptMasterExpectedRepo");
      const pptMasterPackageMode = document.getElementById("pptMasterPackageMode");
      const pptMasterQualityGate = document.getElementById("pptMasterQualityGate");
      const pptMasterRoot = document.getElementById("pptMasterRoot");
      const pptMasterMissingPaths = document.getElementById("pptMasterMissingPaths");
      const pptMasterExecutionSection = document.getElementById("pptMasterExecutionSection");
      const pptMasterExecutionMessage = document.getElementById("pptMasterExecutionMessage");
      const pptMasterExecutionStatus = document.getElementById("pptMasterExecutionStatus");
      const pptMasterExecutionOutputDir = document.getElementById("pptMasterExecutionOutputDir");
      const pptMasterExecutionExpectedPptx = document.getElementById("pptMasterExecutionExpectedPptx");
      const pptMasterExecutionPlanState = document.getElementById("pptMasterExecutionPlanState");
      const pptMasterExecutionSteps = document.getElementById("pptMasterExecutionSteps");
      const preparePptMasterExecutionButton = document.getElementById("preparePptMasterExecutionButton");
      const pptMasterVisualProjectSection = document.getElementById("pptMasterVisualProjectSection");
      const pptMasterVisualProjectMessage = document.getElementById("pptMasterVisualProjectMessage");
      const pptMasterVisualProjectStatus = document.getElementById("pptMasterVisualProjectStatus");
      const pptMasterVisualProjectDir = document.getElementById("pptMasterVisualProjectDir");
      const pptMasterVisualProjectInstructionsState = document.getElementById("pptMasterVisualProjectInstructionsState");
      const pptMasterVisualProjectSourcePath = document.getElementById("pptMasterVisualProjectSourcePath");
      const pptMasterVisualProjectPromptPath = document.getElementById("pptMasterVisualProjectPromptPath");
      const pptMasterVisualProjectSvgOutput = document.getElementById("pptMasterVisualProjectSvgOutput");
      const pptMasterVisualProjectSvgFinal = document.getElementById("pptMasterVisualProjectSvgFinal");
      const pptMasterVisualProjectExpectedPptx = document.getElementById("pptMasterVisualProjectExpectedPptx");
      const pptMasterVisualProjectSteps = document.getElementById("pptMasterVisualProjectSteps");
      const bootstrapPptMasterProjectButton = document.getElementById("bootstrapPptMasterProjectButton");
      const pptMasterRunnerSection = document.getElementById("pptMasterRunnerSection");
      const pptMasterRunnerMessage = document.getElementById("pptMasterRunnerMessage");
      const pptMasterRunnerStatus = document.getElementById("pptMasterRunnerStatus");
      const pptMasterRunnerRequiresExternal = document.getElementById("pptMasterRunnerRequiresExternal");
      const pptMasterRunnerProjectDir = document.getElementById("pptMasterRunnerProjectDir");
      const pptMasterRunnerOutputDir = document.getElementById("pptMasterRunnerOutputDir");
      const pptMasterRunnerPptxPath = document.getElementById("pptMasterRunnerPptxPath");
      const pptMasterRunnerSlideCount = document.getElementById("pptMasterRunnerSlideCount");
      const pptMasterRunnerRegistered = document.getElementById("pptMasterRunnerRegistered");
      const pptMasterRunnerResultState = document.getElementById("pptMasterRunnerResultState");
      const runPptMasterLocalExportButton = document.getElementById("runPptMasterLocalExportButton");
      const pptMasterOutputSection = document.getElementById("pptMasterOutputSection");
      const pptMasterOutputMessage = document.getElementById("pptMasterOutputMessage");
      const pptMasterOutputDetected = document.getElementById("pptMasterOutputDetected");
      const pptMasterOutputSlideCount = document.getElementById("pptMasterOutputSlideCount");
      const pptMasterOutputGenerationStatus = document.getElementById("pptMasterOutputGenerationStatus");
      const pptMasterOutputDir = document.getElementById("pptMasterOutputDir");
      const pptMasterOutputHasNotes = document.getElementById("pptMasterOutputHasNotes");
      const cancelJobButton = document.getElementById("cancelJobButton");
      const resumeJobButton = document.getElementById("resumeJobButton");
      const projectTitle = document.getElementById("projectTitle");
      const totalBatchesMeta = document.getElementById("totalBatchesMeta");
      const stageGenerationDetail = document.getElementById("stageGenerationDetail");
      const stageQualityDetail = document.getElementById("stageQualityDetail");
      const stageExportDetail = document.getElementById("stageExportDetail");
      const stageDoneDetail = document.getElementById("stageDoneDetail");
      const qualitySummary = document.getElementById("qualitySummary");
      const outputSummary = document.getElementById("outputSummary");
      const effortSummary = document.getElementById("effortSummary");
      const previewSummary = document.getElementById("previewSummary");
      const previewEmpty = document.getElementById("previewEmpty");
      const previewSlides = [
        document.getElementById("previewSlide1"),
        document.getElementById("previewSlide2"),
        document.getElementById("previewSlide3")
      ];
      const previewSlideNumber1 = document.getElementById("previewSlideNumber1");
      const previewSlideNumber2 = document.getElementById("previewSlideNumber2");
      const previewSlideNumber3 = document.getElementById("previewSlideNumber3");
      const chapterStrip = document.getElementById("chapterStrip");
      const chapterTotal = document.getElementById("chapterTotal");
      const sourceCoverageScore = document.getElementById("sourceCoverageScore");
      const sourceRequestState = document.getElementById("sourceRequestState");
      const sourcePlanState = document.getElementById("sourcePlanState");
      const sourceDeckState = document.getElementById("sourceDeckState");
      const sourceQaState = document.getElementById("sourceQaState");
      const sourceOutputState = document.getElementById("sourceOutputState");
      const primaryDownloadTop = document.getElementById("primaryDownloadTop");
      const primaryDownloadLink = document.getElementById("primaryDownloadLink");
      const deliveryDownload = document.getElementById("deliveryDownload");
      const deliveryFileName = document.getElementById("deliveryFileName");
      const deliveryStatus = document.getElementById("deliveryStatus");
      const deliverySlideCount = document.getElementById("deliverySlideCount");
      const narrativeHealthScore = document.getElementById("narrativeHealthScore");
      const narrativeHealthLabel = document.getElementById("narrativeHealthLabel");
      const healthStructure = document.getElementById("healthStructure");
      const healthQuality = document.getElementById("healthQuality");
      const healthDelivery = document.getElementById("healthDelivery");
      const railQualityStatus = document.getElementById("railQualityStatus");
      const railFailedBatches = document.getElementById("railFailedBatches");
      const railElapsedSeconds = document.getElementById("railElapsedSeconds");
      const historySearch = document.getElementById("historySearch");
      const historyStatusFilter = document.getElementById("historyStatusFilter");
      const refreshHistoryButton = document.getElementById("refreshHistoryButton");
      const historySummary = document.getElementById("historySummary");
      const historyList = document.getElementById("historyList");
      const historyEmpty = document.getElementById("historyEmpty");
      const interviewComposer = document.getElementById("interviewComposer");
      const interviewInput = document.getElementById("interviewInput");
      const sendInterviewButton = document.getElementById("sendInterviewButton");
      const interviewHint = document.getElementById("interviewHint");
      const interviewMessages = document.getElementById("interviewMessages");
      const interviewQuestionPanel = document.getElementById("interviewQuestionPanel");
      const interviewQuestion = document.getElementById("interviewQuestion");
      const interviewRound = document.getElementById("interviewRound");
      const interviewOptions = document.getElementById("interviewOptions");
      const skipInterviewQuestionButton = document.getElementById("skipInterviewQuestionButton");
      const generationConfirmation = document.getElementById("generationConfirmation");
      const confirmationTopic = document.getElementById("confirmationTopic");
      const confirmationAudience = document.getElementById("confirmationAudience");
      const confirmationSlideCount = document.getElementById("confirmationSlideCount");
      const confirmationVisual = document.getElementById("confirmationVisual");
      const continueInterviewButton = document.getElementById("continueInterviewButton");
      const confirmGenerationButton = document.getElementById("confirmGenerationButton");
      const manualBriefButton = document.getElementById("manualBriefButton");
      const newInterviewButton = document.getElementById("newInterviewButton");
      const briefStatus = document.getElementById("briefStatus");
      const briefTopic = document.getElementById("briefTopic");
      const briefAudience = document.getElementById("briefAudience");
      const briefSlideCount = document.getElementById("briefSlideCount");
      const briefFocus = document.getElementById("briefFocus");
      const briefVisual = document.getElementById("briefVisual");
      const briefReadinessHint = document.getElementById("briefReadinessHint");
      const lastLongDeckJobStorageKey = "ppt_agent_last_long_deck_job_id";
      const chapterDraftStorageKey = "ppt_agent_chapter_draft";
      const longDeckDraftStorageKey = "ppt_agent_long_deck_form_draft";
      const presentationInterviewStorageKey = "ppt_agent_presentation_interview_id";
      const pptMasterArtifactNames = new Set([
        "ppt_master_source",
        "ppt_master_run_prompt",
        "ppt_master_package_manifest",
        "ppt_master_package_README",
        "ppt_master_execution_plan",
        "ppt_master_visual_project_manifest",
        "ppt_master_project_instructions",
        "ppt_master_runner_result",
        "ppt_master_generated_pptx",
        "ppt_master_generation_notes",
        "ppt_master_output_manifest"
      ]);
      const artifactDisplayNames = {
        generated_long_deck: "可编辑长演示 PPTX",
        generated_long_deck_v2: "高质量可编辑长演示 PPTX",
        generated_long_deck_v2_design: "长演示自由布局设计稿",
        generated_long_deck_v2_qa_report: "长演示全页质量报告",
        generated_long_deck_v2_run_report: "长演示运行与成本报告",
        generated_long_deck_ir: "合并后的 Deck IR",
        generated_long_deck_plan: "长演示章节规划",
        generated_long_deck_qa: "全页 QA 报告",
        generated_long_deck_quality_gate: "硬质量门禁报告",
        long_deck_request: "长演示需求简报",
        long_deck_run_report: "长演示运行报告",
        long_deck_render_report: "长演示渲染报告",
        ppt_master_source: "PPT Master Source Markdown",
        ppt_master_run_prompt: "PPT Master Run Prompt",
        ppt_master_package_manifest: "PPT Master Package Manifest",
        ppt_master_package_README: "PPT Master Package README",
        ppt_master_execution_plan: "PPT Master Execution Plan",
        ppt_master_visual_project_manifest: "PPT Master Visual Project Manifest",
        ppt_master_project_instructions: "PPT Master Project Instructions",
        ppt_master_runner_result: "PPT Master Runner Result",
        ppt_master_generated_pptx: "PPT Master Generated PPTX",
        ppt_master_generation_notes: "PPT Master Generation Notes",
        ppt_master_output_manifest: "PPT Master Output Manifest"
      };
      let pollTimer = null;
      let historySearchTimer = null;
      let activeJobId = null;
      let activeInterviewId = null;
      let activeInterviewState = null;
      let manualBriefVisible = false;
      let interviewRequestInFlight = false;
      let currentPreviewKey = "";
      let elapsedJobId = null;
      let elapsedBaseSeconds = 0;
      let elapsedSyncedAt = Date.now();
      let elapsedRunning = false;

      function currentElapsedSeconds() {
        const localDelta = elapsedRunning ? Math.floor((Date.now() - elapsedSyncedAt) / 1000) : 0;
        return elapsedBaseSeconds + Math.max(0, localDelta);
      }

      function renderElapsedClock() {
        const value = currentElapsedSeconds();
        elapsedSeconds.textContent = String(value);
        railElapsedSeconds.textContent = String(value);
      }

      function resetElapsedClock() {
        elapsedJobId = null;
        elapsedBaseSeconds = 0;
        elapsedSyncedAt = Date.now();
        elapsedRunning = true;
        renderElapsedClock();
      }

      function syncElapsedClock(job) {
        const serverSeconds = Number(job.elapsed_seconds || 0);
        const terminal = isTerminalStatus(job.status);
        if (elapsedJobId !== job.job_id) {
          elapsedJobId = job.job_id;
          elapsedBaseSeconds = serverSeconds;
          elapsedSyncedAt = Date.now();
        } else if (terminal) {
          elapsedBaseSeconds = serverSeconds;
          elapsedSyncedAt = Date.now();
        } else {
          const localSeconds = currentElapsedSeconds();
          if (serverSeconds > localSeconds) {
            elapsedBaseSeconds = serverSeconds;
            elapsedSyncedAt = Date.now();
          }
        }
        elapsedRunning = !terminal;
        renderElapsedClock();
      }

      function isTerminalStatus(status) {
        return status === "succeeded"
          || status === "failed"
          || status === "failed_quality_gate"
          || status === "partial_failed_quality_gate"
          || status === "cancelled"
          || status === "partial_cancelled";
      }

      function booleanLabel(value) {
        if (value === true) {
          return "true";
        }
        if (value === false) {
          return "false";
        }
        return "未知";
      }

      function setLinkTarget(link, href, text) {
        link.href = href || "#technicalPanel";
        if (text) {
          link.textContent = text;
        }
      }

      function renderChapterAllocation(slideCount = 30) {
        const defaultChapters = ["角色定位", "需求洞察", "Agent 边界", "工作流设计", "评估体系", "交付与治理", "案例拆解", "成长路线"];
        let chapterNames = defaultChapters;
        try {
          const stored = JSON.parse(localStorage.getItem(chapterDraftStorageKey) || "null");
          if (Array.isArray(stored) && stored.length === defaultChapters.length) {
            chapterNames = stored;
          }
        } catch (error) {
          chapterNames = defaultChapters;
        }
        const colors = ["#087f78", "#315cc8", "#dc684e", "#e6aa22", "#15805d", "#6e5ba5", "#2d829c", "#b85b79"];
        const base = Math.floor(slideCount / chapterNames.length);
        let remainder = slideCount % chapterNames.length;
        chapterStrip.replaceChildren();
        chapterNames.forEach((name, index) => {
          const pages = base + (remainder > 0 ? 1 : 0);
          remainder -= remainder > 0 ? 1 : 0;
          const item = document.createElement("div");
          item.className = "chapter-item";
          item.style.setProperty("--chapter-color", colors[index]);
          const title = document.createElement("strong");
          title.contentEditable = "true";
          title.spellcheck = false;
          title.textContent = name;
          title.title = "点击编辑章节名；仅保存在当前浏览器";
          const pageLabel = document.createElement("span");
          pageLabel.textContent = `${pages} 页`;
          title.addEventListener("blur", () => {
            const values = Array.from(chapterStrip.querySelectorAll("strong")).map((node) => node.textContent.trim() || "未命名章节");
            localStorage.setItem(chapterDraftStorageKey, JSON.stringify(values));
          });
          item.append(title, pageLabel);
          chapterStrip.appendChild(item);
        });
        chapterTotal.textContent = `当前 ${slideCount} 页 · 章节名可本地编辑`;
      }

      function updateStageTrack(job) {
        const stages = Array.from(document.querySelectorAll(".stage-step"));
        let activeIndex = 0;
        const stage = job.current_stage || "";
        if (/generating_batch_|generating_v2_page_|v2_page_|merging_long_deck_ir|generate_deck/.test(stage)) activeIndex = 1;
        if (/qa|quality_gate|failed_quality_gate/.test(stage) || job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate") activeIndex = 2;
        if (/rendering|save_artifacts/.test(stage)) activeIndex = 3;
        if (job.ppt_master_output?.detected || job.status === "succeeded") activeIndex = 4;
        stages.forEach((node, index) => {
          node.classList.toggle("is-complete", index < activeIndex || (activeIndex === 4 && index === 4));
          node.classList.toggle("is-active", index === activeIndex && activeIndex < 4);
        });
        const total = Number(job.total_batches || 0);
        const completed = Number(job.completed_batches || 0);
        const isV2 = job.job_type === "long_deck_v2" || stage.startsWith("v2_") || stage.startsWith("generating_v2_page_");
        const targetSlides = job.ppt_master_output?.slide_count || (isV2 ? Number(job.total_batches || 100) : 30);
        const generatedSlides = total ? Math.min(targetSlides, Math.round((completed / total) * targetSlides)) : 0;
        stageGenerationDetail.textContent = `${generatedSlides} / ${targetSlides} 页`;
        stageQualityDetail.textContent = /quality_gate/.test(job.status || "") ? "发现需恢复内容" : (job.accepted === true ? "质量门禁已通过" : "等待生成");
        stageExportDetail.textContent = job.ppt_master_output?.detected ? "可编辑 PPTX 已注册" : "等待质检";
        stageDoneDetail.textContent = job.ppt_master_output?.detected || job.status === "succeeded" ? "任务可交付" : "等待交付";
      }

      function updateNarrativeHealth(job) {
        let score = 35;
        const v2Delivered = job.job_type === "long_deck_v2" && job.status === "succeeded";
        const delivered = Boolean(job.ppt_master_output?.detected || v2Delivered);
        const merged = Number(job.completed_batches || 0) > 0 && Number(job.completed_batches || 0) === Number(job.total_batches || 0);
        if (merged) score += 20;
        if (job.ppt_master_package?.generated) score += 15;
        if (job.accepted === true) score += 20;
        if (delivered) score += 10;
        score = Math.min(score, 100);
        narrativeHealthScore.textContent = String(score);
        narrativeHealthLabel.textContent = job.ppt_master_output?.detected ? "已恢复交付" : (v2Delivered ? "高质量成片已交付" : (job.accepted === true ? "结构健康" : "仍需验证"));
        healthStructure.textContent = merged ? "完整 Deck IR 与章节推进已生成" : "章节结构仍在生成或尚未合并";
        healthQuality.textContent = job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate" ? "旧 renderer 质量门禁未通过，已保留恢复路径" : (job.accepted === true ? "质量门禁已通过" : "质量门禁尚未通过");
        healthQuality.classList.toggle("is-warning", job.accepted !== true);
        healthDelivery.textContent = job.ppt_master_output?.detected ? "PPT Master 可编辑成片已注册" : (v2Delivered ? "可编辑长演示已生成" : "可编辑成片尚未注册");
        healthDelivery.classList.toggle("is-warning", !delivered);
      }

      function updateProductDashboard(job) {
        projectTitle.textContent = document.getElementById("long_topic").value.trim() || "PPT 项目工作台";
        const isV2 = job.job_type === "long_deck_v2" || (job.current_stage || "").startsWith("v2_");
        totalBatchesMeta.textContent = String(job.total_batches || 0);
        railFailedBatches.textContent = String(job.failed_batches || 0);
        renderElapsedClock();
        effortSummary.textContent = Number(job.total_batches || 0) >= 10 ? "较高" : "标准";
        const qualityFailed = job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate";
        qualitySummary.textContent = qualityFailed ? "需恢复" : (job.accepted === true ? "通过" : "未评估");
        railQualityStatus.textContent = qualitySummary.textContent;
        outputSummary.textContent = job.ppt_master_output?.detected ? "已交付" : (job.status === "succeeded" ? "已生成" : "未生成");
        const deliveredSlideCount = job.ppt_master_output?.slide_count ?? (isV2 && job.status === "succeeded" ? job.total_batches : null);
        deliverySlideCount.textContent = deliveredSlideCount == null ? "未知" : `${deliveredSlideCount} 页`;
        deliveryStatus.textContent = job.ppt_master_output?.detected || (isV2 && job.status === "succeeded") ? "已注册到当前 job，可直接下载" : "生成后可直接下载";
        updateStageTrack(job);
        updateNarrativeHealth(job);
        renderChapterAllocation(job.ppt_master_output?.slide_count || (isV2 ? Number(job.total_batches || 100) : 30));
      }

      function jobErrorText(job) {
        const qualityFailed = job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate";
        if (qualityFailed && job.ppt_master_output?.detected) {
          return "旧 renderer 质量门禁未通过；PPT Master 恢复成片已注册，可继续下载验收。";
        }
        return job.error_message || "";
      }

      async function updateSlidePreviews(id) {
        let manifest;
        try {
          manifest = await requestJson(`/api/jobs/${id}/preview-slides`);
        } catch (error) {
          return;
        }
        const available = Array.from(new Set(manifest.available_slide_numbers || []))
          .map(Number)
          .filter((value) => Number.isInteger(value) && value > 0)
          .sort((left, right) => left - right);
        if (!available.length) {
          currentPreviewKey = "";
          previewEmpty.hidden = false;
          previewSummary.textContent = "页面生成后会在这里逐步出现，无需等待整份 PPT 完成。";
          previewSlides.forEach((frame) => {
            frame.hidden = true;
            frame.removeAttribute("src");
          });
          return;
        }

        const highlighted = Array.from(new Set(manifest.highlight_slide_numbers || []))
          .map(Number)
          .filter((value) => available.includes(value));
        const selected = highlighted.slice(0, 3);
        available.forEach((value) => {
          if (selected.length < 3 && !selected.includes(value)) selected.push(value);
        });
        selected.sort((left, right) => left - right);
        const previewKey = `${id}:${manifest.update_token || "0"}:${selected.join(",")}`;
        previewEmpty.hidden = true;
        previewSummary.textContent = `已生成 ${available.length} 页，正在展示视觉高光页 ${selected.join(" / ")}。`;
        const numberLabels = [previewSlideNumber1, previewSlideNumber2, previewSlideNumber3];
        previewSlides.forEach((frame, index) => {
          const slideNumber = selected[index];
          if (!slideNumber) {
            frame.hidden = true;
            frame.removeAttribute("src");
            return;
          }
          numberLabels[index].textContent = String(slideNumber);
          frame.hidden = false;
          if (previewKey !== currentPreviewKey) {
            frame.src = `/api/jobs/${id}/preview-slides/${slideNumber}?v=${manifest.update_token || Date.now()}`;
          }
        });
        currentPreviewKey = previewKey;
      }

      function updateArtifactDrivenUi(artifactList) {
        const names = new Set(artifactList.map((artifact) => artifact.name));
        const checks = [
          [sourceRequestState, names.has("long_deck_request")],
          [sourcePlanState, names.has("generated_long_deck_plan")],
          [sourceDeckState, names.has("generated_long_deck_ir") || names.has("generated_long_deck_v2_design")],
          [sourceQaState, names.has("generated_long_deck_qa") || names.has("generated_long_deck_quality_gate") || names.has("generated_long_deck_v2_qa_report")],
          [sourceOutputState, names.has("ppt_master_generated_pptx") || names.has("generated_long_deck") || names.has("generated_long_deck_v2") || names.has("generated_pptx")]
        ];
        let covered = 0;
        checks.forEach(([node, present]) => {
          node.textContent = present ? "已覆盖" : "未生成";
          covered += present ? 1 : 0;
        });
        sourceCoverageScore.textContent = `${Math.round((covered / checks.length) * 100)}%`;
        const primary = artifactList.find((artifact) => artifact.name === "ppt_master_generated_pptx")
          || artifactList.find((artifact) => artifact.kind === "pptx");
        if (primary) {
          deliveryFileName.textContent = artifactLabel(primary);
          [primaryDownloadTop, primaryDownloadLink, deliveryDownload].forEach((link) => setLinkTarget(link, primary.download_url, "下载可编辑 PPTX"));
        } else {
          deliveryFileName.textContent = "等待生成 PPTX";
          [primaryDownloadTop, primaryDownloadLink, deliveryDownload].forEach((link) => setLinkTarget(link, "#technicalPanel", "查看交付文件"));
        }
      }

      function clearPptMasterPackage() {
        pptMasterPackageSection.hidden = true;
        pptMasterPackageMessage.textContent = "";
        pptMasterGenerated.textContent = "未生成";
        pptMasterReason.textContent = "未评估";
        pptMasterSuggestion.textContent = "无";
        pptMasterAvailable.textContent = "未知";
        pptMasterExpectedRepo.textContent = "未知";
        pptMasterPackageMode.textContent = "未知";
        pptMasterQualityGate.textContent = "未知";
        pptMasterRoot.textContent = "未检测到";
        pptMasterMissingPaths.textContent = "无";
      }

      function clearPptMasterOutput() {
        pptMasterOutputSection.hidden = true;
        pptMasterOutputMessage.textContent = "";
        pptMasterOutputDetected.textContent = "未检测到";
        pptMasterOutputSlideCount.textContent = "未知";
        pptMasterOutputGenerationStatus.textContent = "未知";
        pptMasterOutputDir.textContent = "未检测到";
        pptMasterOutputHasNotes.textContent = "未知";
      }

      function clearPptMasterExecution() {
        pptMasterExecutionSection.hidden = true;
        pptMasterExecutionMessage.textContent = "";
        pptMasterExecutionStatus.textContent = "未准备";
        pptMasterExecutionOutputDir.textContent = "未检测到";
        pptMasterExecutionExpectedPptx.textContent = "未检测到";
        pptMasterExecutionPlanState.textContent = "未生成";
        pptMasterExecutionSteps.textContent = "无";
        preparePptMasterExecutionButton.disabled = true;
      }

      function clearPptMasterVisualProject() {
        pptMasterVisualProjectSection.hidden = true;
        pptMasterVisualProjectMessage.textContent = "";
        pptMasterVisualProjectStatus.textContent = "未准备";
        pptMasterVisualProjectDir.textContent = "未检测到";
        pptMasterVisualProjectInstructionsState.textContent = "未生成";
        pptMasterVisualProjectSourcePath.textContent = "未检测到";
        pptMasterVisualProjectPromptPath.textContent = "未检测到";
        pptMasterVisualProjectSvgOutput.textContent = "未检测到";
        pptMasterVisualProjectSvgFinal.textContent = "未检测到";
        pptMasterVisualProjectExpectedPptx.textContent = "未检测到";
        pptMasterVisualProjectSteps.textContent = "无";
        bootstrapPptMasterProjectButton.disabled = true;
      }

      function clearPptMasterRunner() {
        pptMasterRunnerSection.hidden = true;
        pptMasterRunnerMessage.textContent = "";
        pptMasterRunnerStatus.textContent = "未运行";
        pptMasterRunnerRequiresExternal.textContent = "未知";
        pptMasterRunnerProjectDir.textContent = "未检测到";
        pptMasterRunnerOutputDir.textContent = "未检测到";
        pptMasterRunnerPptxPath.textContent = "未检测到";
        pptMasterRunnerSlideCount.textContent = "未知";
        pptMasterRunnerRegistered.textContent = "false";
        pptMasterRunnerResultState.textContent = "未生成";
        runPptMasterLocalExportButton.disabled = true;
      }

      function updatePptMasterPackage(job) {
        if (!isLongDeckJob(job) || !job.ppt_master_package) {
          clearPptMasterPackage();
          return;
        }

        const packageInfo = job.ppt_master_package;
        pptMasterPackageSection.hidden = false;
        pptMasterPackageMessage.textContent = packageInfo.message || "";
        if (packageInfo.generated && packageInfo.package_mode === "recovery") {
          pptMasterGenerated.textContent = "Recovery package 已生成";
        } else {
          pptMasterGenerated.textContent = packageInfo.generated ? "已生成" : "未生成";
        }
        if (packageInfo.reason === "job_timeout_before_merge" || packageInfo.reason === "batch_generation_failed_before_merge") {
          pptMasterReason.textContent = "长 PPT 尚未完成合并，当前没有完整 Deck IR";
          pptMasterSuggestion.textContent = "点击“继续/重试长 PPT”，系统会从已完成 batch 后继续。";
          pptMasterAvailable.textContent = "未评估";
          pptMasterExpectedRepo.textContent = "未评估";
          pptMasterPackageMode.textContent = "未评估";
          pptMasterQualityGate.textContent = "未评估";
          pptMasterRoot.textContent = "未评估";
          pptMasterMissingPaths.textContent = "未评估";
          return;
        }
        pptMasterReason.textContent = packageInfo.reason || "未评估";
        pptMasterSuggestion.textContent = packageInfo.generated ? "下载 package artifacts 后交给本地 ppt-master workflow。" : "等待长 PPT 生成完成。";
        pptMasterAvailable.textContent = booleanLabel(packageInfo.available);
        pptMasterExpectedRepo.textContent = booleanLabel(packageInfo.is_expected_repo);
        pptMasterPackageMode.textContent = packageInfo.package_mode || "未知";
        pptMasterQualityGate.textContent = packageInfo.source_quality_gate_status || "未知";
        pptMasterRoot.textContent = packageInfo.ppt_master_root || "未检测到";
        const missingPaths = packageInfo.missing_paths || [];
        pptMasterMissingPaths.textContent = missingPaths.length ? missingPaths.join(", ") : "无";
      }

      function updatePptMasterExecution(job) {
        if (!isLongDeckJob(job) || !job.ppt_master_execution) {
          clearPptMasterExecution();
          return;
        }
        const execution = job.ppt_master_execution;
        pptMasterExecutionSection.hidden = false;
        pptMasterExecutionMessage.textContent = execution.message || "";
        pptMasterExecutionStatus.textContent = execution.status || "未准备";
        pptMasterExecutionOutputDir.textContent = execution.output_dir || "未检测到";
        pptMasterExecutionExpectedPptx.textContent = execution.expected_pptx_path || "未检测到";
        pptMasterExecutionPlanState.textContent = execution.plan_artifact_id ? "已生成" : "未生成";
        const steps = execution.suggested_steps || [];
        pptMasterExecutionSteps.textContent = steps.length ? steps.join("\\n") : "无";
        preparePptMasterExecutionButton.disabled = !activeJobId;
      }

      function updatePptMasterVisualProject(job) {
        if (!isLongDeckJob(job) || !job.ppt_master_visual_project) {
          clearPptMasterVisualProject();
          return;
        }
        const project = job.ppt_master_visual_project;
        pptMasterVisualProjectSection.hidden = false;
        pptMasterVisualProjectMessage.textContent = project.message || "";
        pptMasterVisualProjectStatus.textContent = project.status || "未准备";
        pptMasterVisualProjectDir.textContent = project.project_dir || "未检测到";
        pptMasterVisualProjectInstructionsState.textContent = project.instructions_artifact_id ? "已生成" : "未生成";
        pptMasterVisualProjectSourcePath.textContent = project.project_source_path || "未检测到";
        pptMasterVisualProjectPromptPath.textContent = project.project_prompt_path || "未检测到";
        pptMasterVisualProjectSvgOutput.textContent = project.expected_svg_output_dir || "未检测到";
        pptMasterVisualProjectSvgFinal.textContent = project.expected_svg_final_dir || "未检测到";
        pptMasterVisualProjectExpectedPptx.textContent = project.expected_pptx_path || "未检测到";
        const steps = project.next_steps || [];
        pptMasterVisualProjectSteps.textContent = steps.length ? steps.join("\\n") : "无";
        bootstrapPptMasterProjectButton.disabled = !activeJobId;
      }

      function updatePptMasterRunner(job) {
        if (!isLongDeckJob(job) || !job.ppt_master_runner) {
          clearPptMasterRunner();
          return;
        }
        const runner = job.ppt_master_runner;
        pptMasterRunnerSection.hidden = false;
        pptMasterRunnerMessage.textContent = runner.message || "";
        pptMasterRunnerStatus.textContent = runner.status || "未运行";
        pptMasterRunnerRequiresExternal.textContent = runner.requires_external_ai_generation ? "true" : "false";
        pptMasterRunnerProjectDir.textContent = runner.project_dir || "未检测到";
        pptMasterRunnerOutputDir.textContent = runner.output_dir || "未检测到";
        pptMasterRunnerPptxPath.textContent = runner.pptx_path || "未检测到";
        pptMasterRunnerSlideCount.textContent = runner.slide_count == null ? "未知" : String(runner.slide_count);
        pptMasterRunnerRegistered.textContent = runner.registered ? "true" : "false";
        pptMasterRunnerResultState.textContent = runner.result_artifact_id ? "已生成" : "未生成";
        runPptMasterLocalExportButton.disabled = !activeJobId;
      }

      function updatePptMasterOutput(job) {
        if (!isLongDeckJob(job) || !job.ppt_master_output) {
          clearPptMasterOutput();
          return;
        }
        const output = job.ppt_master_output;
        pptMasterOutputSection.hidden = false;
        pptMasterOutputMessage.textContent = output.message || "";
        pptMasterOutputDetected.textContent = output.detected ? "已检测到" : "未检测到";
        pptMasterOutputSlideCount.textContent = output.slide_count == null ? "未知" : String(output.slide_count);
        pptMasterOutputGenerationStatus.textContent = output.generation_status || "未知";
        pptMasterOutputDir.textContent = output.output_dir || "未检测到";
        pptMasterOutputHasNotes.textContent = output.notes_artifact_id ? "已检测到" : "未检测到";
      }

      function artifactLabel(artifact) {
        return artifactDisplayNames[artifact.name] || `${artifact.name}.${artifact.kind}`;
      }

      function appendArtifactGroupLabel(text) {
        const item = document.createElement("li");
        item.className = "artifact-group-label";
        item.textContent = text;
        artifacts.appendChild(item);
      }

      function appendArtifactLink(artifact) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = artifact.download_url;
        link.textContent = `下载 ${artifactLabel(artifact)}`;
        item.appendChild(link);
        artifacts.appendChild(item);
      }

      function setBusy(isBusy) {
        longDeckButton.disabled = isBusy;
      }

      function isLongDeckJob(job) {
        return job.job_type === "long_deck" || job.job_type === "long_deck_v2" || Boolean(job.total_batches);
      }

      function updateActionButtons(job) {
        const terminal = isTerminalStatus(job.status);
        cancelJobButton.disabled = !(isLongDeckJob(job) && !terminal && !job.cancel_requested);
        resumeJobButton.disabled = !(isLongDeckJob(job) && (job.status === "failed" || job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate" || job.status === "cancelled" || job.status === "partial_cancelled"));
      }

      const statusText = {
        idle: "未开始",
        submitting: "提交中",
        pending: "等待中",
        running: "生成中",
        succeeded: "已完成",
        failed: "失败",
        failed_quality_gate: "质量门禁失败",
        partial_failed_quality_gate: "部分生成后未通过质量门禁",
        cancelled: "已取消",
        partial_cancelled: "部分完成后取消"
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
        running_long_deck_quality_gate: "正在执行长 PPT质量门禁",
        rendering_long_deck_pptx: "正在渲染长 PPT PPTX",
        failed_quality_gate: "未通过质量门禁",
        partial_failed_quality_gate: "部分生成后未通过质量门禁",
        completed: "已完成",
        cancel_requested: "已请求取消，当前 batch 完成后停止",
        cancelled: "已取消",
        partial_cancelled: "部分完成后取消",
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
        const v2PageMatch = /^generating_v2_page_(\d+)_of_(\d+)$/.exec(stage || "");
        if (v2PageMatch) {
          return `正在生成自由布局页面：${v2PageMatch[1]}/${v2PageMatch[2]}`;
        }
        const v2StageText = {
          v2_intake: "正在整理演示需求",
          v2_brief: "正在形成内容简报",
          v2_theme: "正在设计视觉主题",
          v2_outline: "正在规划长演示叙事结构",
          v2_page_briefs: "正在细化逐页内容",
          v2_page_designs: "正在并发生成自由布局页面",
          v2_quality_gate: "正在执行全页质量检查",
          v2_rendering_complete: "可编辑 PPTX 已导出",
          v2_completed: "长演示已完成",
          v2_quality_gate_failed: "全页质量检查未通过",
          v2_cancelled: "长演示已取消",
          v2_failed: "长演示生成失败"
        };
        if (v2StageText[stage]) return v2StageText[stage];
        return stageText[stage] || stage || "暂无";
      }

      function setProgress(job) {
        currentStage.textContent = stageLabel(job.current_stage);
        currentBatch.textContent = job.current_batch || "暂无";
        totalBatches.textContent = String(job.total_batches || 0);
        totalBatchesMeta.textContent = String(job.total_batches || 0);
        completedBatches.textContent = String(job.completed_batches || 0);
        failedBatches.textContent = String(job.failed_batches || 0);
        railFailedBatches.textContent = String(job.failed_batches || 0);
        syncElapsedClock(job);
        const isTerminal = isTerminalStatus(job.status);
        if (job.cancel_requested && !isTerminal) {
          longRunningNotice.textContent = "取消请求已发送；当前 batch 完成后会停止。";
        } else if (!isTerminal && (job.elapsed_seconds || 0) >= 300) {
          longRunningNotice.textContent = "任务运行时间较长，请检查后端日志。";
        } else {
          longRunningNotice.textContent = "";
        }
        updateActionButtons(job);
      }

      function clearArtifacts() {
        artifacts.replaceChildren();
      }

      function rememberActiveJob(job) {
        if (job?.job_id) {
          localStorage.setItem(lastLongDeckJobStorageKey, job.job_id);
        }
      }

      function forgetActiveJob() {
        localStorage.removeItem(lastLongDeckJobStorageKey);
      }

      function buildShortDeckPayload() {
        return {
          topic: document.getElementById("long_topic").value.trim(),
          audience: document.getElementById("long_audience").value.trim(),
          slides: Number(longSlideCount.value),
          user_requirements: document.getElementById("long_user_requirements").value.trim(),
          min_qa_score: 80,
          max_attempts: 2,
          interview_id: activeInterviewId
        };
      }

      function buildLongDeckPayload() {
        return {
          topic: document.getElementById("long_topic").value.trim(),
          audience: document.getElementById("long_audience").value.trim(),
          slide_count: Number(document.getElementById("long_slide_count").value),
          language: "zh-CN",
          deck_type: "technical_product_share",
          user_requirements: document.getElementById("long_user_requirements").value.trim(),
          interview_id: activeInterviewId
        };
      }

      function saveLongDeckDraft() {
        const draft = {
          topic: document.getElementById("long_topic").value,
          audience: document.getElementById("long_audience").value,
          slide_count: Number(longSlideCount.value),
          user_requirements: document.getElementById("long_user_requirements").value
        };
        localStorage.setItem(longDeckDraftStorageKey, JSON.stringify(draft));
      }

      function loadLongDeckDraft() {
        try {
          const draft = JSON.parse(localStorage.getItem(longDeckDraftStorageKey) || "null");
          if (!draft || typeof draft !== "object") return;
          if (typeof draft.topic === "string") document.getElementById("long_topic").value = draft.topic;
          if (typeof draft.audience === "string") document.getElementById("long_audience").value = draft.audience;
          if (typeof draft.user_requirements === "string") document.getElementById("long_user_requirements").value = draft.user_requirements;
          const pageCount = Number(draft.slide_count);
          if (Number.isInteger(pageCount) && pageCount >= 1 && pageCount <= 100) {
            longSlideCount.value = String(pageCount);
          }
        } catch (error) {
          localStorage.removeItem(longDeckDraftStorageKey);
        }
      }

      function updateGenerationChoice() {
        const pageCount = Math.max(1, Math.min(100, Number(longSlideCount.value) || 30));
        longDeckButton.textContent = `确认并生成 ${pageCount} 页 PPT`;
        if (pageCount <= 10) {
          generationStrategyHint.textContent = `${pageCount} 页将使用快速生成模式；系统会自动规划、质检并导出可编辑 PPTX。`;
        } else if (pageCount === 30) {
          generationStrategyHint.textContent = "30 页将使用稳定批次模式；系统会自动保存进度、处理重试并检查内容质量。";
        } else {
          generationStrategyHint.textContent = `${pageCount} 页将使用高质量生成模式；系统会自动保存进度并检查每一页，预计耗时较长。`;
        }
        stageGenerationDetail.textContent = `0 / ${pageCount} 页`;
        renderChapterAllocation(pageCount);
      }

      async function requestJson(url, options) {
        const response = await fetch(url, options);
        const body = await response.json();
        if (!response.ok) {
          throw new Error(body.detail || "请求失败");
        }
        return body;
      }

      function appendInterviewMessage(role, content) {
        const message = document.createElement("div");
        message.className = "conversation-message";
        message.dataset.role = role;
        const label = document.createElement("strong");
        label.textContent = role === "user" ? "你" : "PPT Agent";
        message.appendChild(label);
        message.appendChild(document.createTextNode(content));
        interviewMessages.appendChild(message);
      }

      function resetBriefSummary() {
        briefTopic.textContent = "待确认";
        briefAudience.textContent = "待确认";
        briefSlideCount.textContent = "待确认";
        briefFocus.textContent = "待确认";
        briefVisual.textContent = "待确认";
        briefStatus.textContent = "等待描述";
        briefStatus.classList.remove("is-ready");
        briefReadinessHint.textContent = "Agent 会在对话中实时整理，信息充分后开放最终确认。";
      }

      function applyBriefToGenerationForm(brief) {
        if (brief.topic) document.getElementById("long_topic").value = brief.topic;
        if (brief.audience) document.getElementById("long_audience").value = brief.audience;
        if (brief.slide_count) longSlideCount.value = String(brief.slide_count);
        if (brief.user_requirements) {
          document.getElementById("long_user_requirements").value = brief.user_requirements;
        }
        saveLongDeckDraft();
        updateGenerationChoice();
      }

      function renderBriefDraft(brief, isReady) {
        briefTopic.textContent = brief.topic || "待确认";
        briefAudience.textContent = brief.audience || "待确认";
        briefSlideCount.textContent = brief.slide_count ? `${brief.slide_count} 页` : "待确认";
        const focus = [brief.purpose, ...(brief.content_focus || [])].filter(Boolean);
        briefFocus.textContent = focus.length ? focus.join(" · ") : "待确认";
        briefVisual.textContent = brief.visual_direction || brief.tone || "待确认";
        briefStatus.textContent = isReady ? "已理解" : "理解中";
        briefStatus.classList.toggle("is-ready", isReady);
        briefReadinessHint.textContent = isReady
          ? "Agent 已经掌握生成所需信息；你可以直接开始，或继续用对话调整。"
          : "Agent 正在补齐会影响内容、结构和视觉结果的关键决策。";
        if (isReady) {
          applyBriefToGenerationForm(brief);
          projectTitle.textContent = brief.topic || "PPT 项目工作台";
        }
        longDeckForm.hidden = !manualBriefVisible;
      }

      function renderGenerationConfirmation(brief) {
        confirmationTopic.textContent = brief.topic || "待确认";
        confirmationAudience.textContent = brief.audience || "待确认";
        confirmationSlideCount.textContent = brief.slide_count ? `${brief.slide_count} 页` : "待确认";
        confirmationVisual.textContent = brief.visual_direction || brief.tone || "待确认";
        confirmGenerationButton.textContent = `开始生成 ${brief.slide_count || ""} 页 PPT`.replace("  页", "");
      }

      function renderInterviewState(state, allowAutoStart = false) {
        activeInterviewId = state.interview_id;
        activeInterviewState = state;
        localStorage.setItem(presentationInterviewStorageKey, state.interview_id);
        interviewMessages.replaceChildren();
        state.messages.forEach((message, index) => {
          let content = message.content;
          const isCurrentAssistant = index === state.messages.length - 1 && message.role === "assistant";
          if (isCurrentAssistant && state.status === "clarifying" && state.decision.question) {
            const suffix = `\n\n${state.decision.question}`;
            if (content.endsWith(suffix)) content = content.slice(0, -suffix.length);
          }
          appendInterviewMessage(message.role, content);
        });
        interviewMessages.scrollTop = interviewMessages.scrollHeight;

        const decision = state.decision;
        const isReady = state.status === "ready";
        renderBriefDraft(decision.brief, isReady);
        interviewQuestionPanel.hidden = isReady;
        interviewComposer.hidden = isReady;
        generationConfirmation.hidden = !isReady;
        interviewOptions.replaceChildren();
        if (!isReady) {
          interviewQuestion.textContent = decision.question;
          interviewRound.textContent = `第 ${state.turn_count} 轮 · 问题数量动态调整`;
          decision.options.forEach((option, index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "interview-option";
            const number = document.createElement("span");
            number.className = "option-number";
            number.textContent = String(index + 1);
            const copy = document.createElement("span");
            copy.className = "option-copy";
            const label = document.createElement("strong");
            label.textContent = option.label;
            const description = document.createElement("span");
            description.textContent = option.description || "选择这个方向";
            copy.append(label, description);
            const arrow = document.createElement("span");
            arrow.className = "option-arrow";
            arrow.textContent = "→";
            button.append(number, copy, arrow);
            button.addEventListener("click", () => {
              submitInterviewMessage(option.label, option.option_id);
            });
            interviewOptions.appendChild(button);
          });
          interviewOptions.after(interviewComposer);
          interviewInput.placeholder = "选择上面的建议，或者直接输入更符合你想法的回答。";
          interviewHint.textContent = "每次只回答一个问题，Agent 会继续判断是否还需要追问。";
          requestAnimationFrame(() => interviewInput.focus());
        } else {
          renderGenerationConfirmation(decision.brief);
          interviewHint.textContent = "需求已整理完成。";
          const canAutoStart = decision.auto_start || Number(decision.brief.slide_count) <= 10;
          if (allowAutoStart && canAutoStart) {
            generationConfirmation.hidden = true;
            setTimeout(() => longDeckForm.requestSubmit(), 0);
          }
        }
      }

      function setInterviewBusy(isBusy) {
        interviewRequestInFlight = isBusy;
        sendInterviewButton.disabled = isBusy;
        skipInterviewQuestionButton.disabled = isBusy;
        interviewComposer.setAttribute("aria-busy", String(isBusy));
        interviewOptions.querySelectorAll("button").forEach((button) => {
          button.disabled = isBusy;
        });
        sendInterviewButton.textContent = isBusy ? "Agent 正在思考..." : "发送给 Agent";
      }

      async function submitInterviewMessage(message, selectedOptionId = null) {
        const content = String(message || "").trim();
        if (!content || interviewRequestInFlight) return;
        appendInterviewMessage("user", content);
        interviewInput.value = "";
        const pendingMessage = document.createElement("div");
        pendingMessage.className = "conversation-message is-pending";
        pendingMessage.dataset.role = "assistant";
        pendingMessage.innerHTML = "<strong>PPT Agent</strong>正在快速整理这一轮需求...";
        interviewMessages.appendChild(pendingMessage);
        interviewMessages.scrollTop = interviewMessages.scrollHeight;
        setInterviewBusy(true);
        interviewHint.textContent = "Agent 正在判断需求是否已经足够具体...";
        try {
          const url = activeInterviewId
            ? `/api/presentation-interviews/${activeInterviewId}/messages`
            : "/api/presentation-interviews";
          const body = activeInterviewId
            ? {message: content, selected_option_id: selectedOptionId}
            : {message: content};
          const state = await requestJson(url, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body)
          });
          renderInterviewState(state, true);
        } catch (error) {
          pendingMessage.remove();
          appendInterviewMessage("assistant", `这轮需求分析没有完成：${error.message}。你可以直接重试。`);
          if (!interviewInput.value.trim()) interviewInput.value = content;
          interviewHint.textContent = "请求失败，没有丢失已经确认的内容。";
        } finally {
          setInterviewBusy(false);
        }
      }

      function resetPresentationInterview() {
        activeInterviewId = null;
        activeInterviewState = null;
        manualBriefVisible = false;
        localStorage.removeItem(presentationInterviewStorageKey);
        localStorage.removeItem(longDeckDraftStorageKey);
        interviewMessages.replaceChildren();
        appendInterviewMessage("assistant", "告诉我你想做什么演示。哪怕只有一个模糊想法也可以，我会一步一步帮你把内容、观众、页数和视觉方向问清楚。");
        generationConfirmation.after(interviewComposer);
        interviewQuestionPanel.hidden = true;
        generationConfirmation.hidden = true;
        interviewComposer.hidden = false;
        interviewInput.value = "";
        interviewInput.placeholder = "例如：我想做一份给大学生看的生态环境保护演示，但还不知道从哪里开始。";
        interviewHint.textContent = "描述越具体，Agent 需要追问的问题越少。";
        longDeckForm.reset();
        longDeckForm.hidden = true;
        resetBriefSummary();
        updateGenerationChoice();
      }

      async function restorePresentationInterview() {
        const interviewId = localStorage.getItem(presentationInterviewStorageKey);
        if (!interviewId) return;
        try {
          const state = await requestJson(`/api/presentation-interviews/${interviewId}`);
          renderInterviewState(state, false);
        } catch (error) {
          localStorage.removeItem(presentationInterviewStorageKey);
        }
      }

      function historyStatusTone(status) {
        if (status === "succeeded") return "success";
        if (status === "failed_quality_gate" || status === "partial_failed_quality_gate") return "warning";
        if (status === "failed" || status === "cancelled" || status === "partial_cancelled") return "danger";
        return "neutral";
      }

      function formatHistoryDate(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "时间未知";
        return new Intl.DateTimeFormat("zh-CN", {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit"
        }).format(date);
      }

      async function openPresentationFromHistory(item) {
        const job = await requestJson(`/api/jobs/${item.job_id}`);
        activeJobId = job.job_id;
        rememberActiveJob(job);
        jobId.textContent = job.job_id;
        setStatus(job.status, job.accepted, job.error_message || "");
        setProgress(job);
        updatePptMasterPackage(job);
        updatePptMasterExecution(job);
        updatePptMasterVisualProject(job);
        updatePptMasterRunner(job);
        updatePptMasterOutput(job);
        updateProductDashboard(job);
        projectTitle.textContent = item.topic;
        errorMessage.textContent = job.error_message ? jobErrorText(job) : "";
        await Promise.all([loadArtifacts(job.job_id), updateSlidePreviews(job.job_id)]);
        if (!isTerminalStatus(job.status)) {
          setBusy(true);
          schedulePoll(job.job_id, 1000);
        }
        document.getElementById("projectWorkspace").scrollIntoView({behavior: "smooth", block: "start"});
      }

      function renderPresentationHistory(items, total) {
        historyList.replaceChildren();
        historyEmpty.hidden = items.length > 0;
        historySummary.textContent = total === 0
          ? "本地 SQLite 中暂无匹配记录"
          : `共 ${total} 条记录，当前显示最近 ${items.length} 条`;
        for (const item of items) {
          const row = document.createElement("article");
          row.className = "history-row";

          const main = document.createElement("div");
          const title = document.createElement("h3");
          title.className = "history-title";
          title.textContent = item.topic;
          const meta = document.createElement("p");
          meta.className = "history-meta";
          meta.textContent = [
            item.slide_count ? `${item.slide_count} 页` : "页数未记录",
            item.audience || "观众未记录",
            formatHistoryDate(item.created_at)
          ].join(" · ");
          main.append(title, meta);
          if (item.user_requirements) {
            const requirements = document.createElement("p");
            requirements.className = "history-requirements";
            requirements.textContent = item.user_requirements;
            main.appendChild(requirements);
          }

          const state = document.createElement("div");
          state.className = "history-state";
          const status = document.createElement("span");
          status.className = "history-status";
          status.dataset.tone = historyStatusTone(item.status);
          status.textContent = statusText[item.status] || item.status;
          const qa = document.createElement("span");
          qa.className = "history-meta";
          qa.textContent = item.qa_score == null ? "QA 未评估" : `QA ${item.qa_score} 分`;
          state.append(status, qa);

          const actions = document.createElement("div");
          actions.className = "history-actions";
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.className = "secondary-button";
          openButton.textContent = "打开任务";
          openButton.addEventListener("click", () => {
            openPresentationFromHistory(item).catch((error) => {
              historySummary.textContent = `打开任务失败：${error.message}`;
            });
          });
          actions.appendChild(openButton);
          if (item.pptx_download_url) {
            const download = document.createElement("a");
            download.className = "button-link";
            download.href = item.pptx_download_url;
            download.textContent = "下载 PPTX";
            actions.appendChild(download);
          }

          row.append(main, state, actions);
          historyList.appendChild(row);
        }
      }

      async function loadPresentationHistory() {
        refreshHistoryButton.disabled = true;
        historySummary.textContent = "正在读取本地历史记录...";
        const params = new URLSearchParams({limit: "50"});
        const query = historySearch.value.trim();
        const status = historyStatusFilter.value;
        if (query) params.set("query", query);
        if (status) params.set("status", status);
        try {
          const body = await requestJson(`/api/presentations?${params.toString()}`);
          renderPresentationHistory(body.items, body.total);
        } catch (error) {
          historyList.replaceChildren();
          historyEmpty.hidden = true;
          historySummary.textContent = `历史记录读取失败：${error.message}`;
        } finally {
          refreshHistoryButton.disabled = false;
        }
      }

      async function loadArtifacts(id) {
        const body = await requestJson(`/api/jobs/${id}/artifacts`);
        clearArtifacts();
        const groups = [
          ["成片交付", (artifact) => artifact.kind === "pptx"],
          ["质量证据", (artifact) => /qa|quality_gate|render_report|run_report/.test(artifact.name)],
          ["内容与规划", (artifact) => /request|plan|deck_ir/.test(artifact.name) && !/^batch_/.test(artifact.name)],
          ["PPT Master 渲染包", (artifact) => pptMasterArtifactNames.has(artifact.name)],
          ["批次与调试文件", () => true]
        ];
        const remaining = [...body.artifacts];
        for (const [label, matcher] of groups) {
          const matched = remaining.filter(matcher);
          if (!matched.length) continue;
          appendArtifactGroupLabel(label);
          matched.forEach(appendArtifactLink);
          matched.forEach((artifact) => remaining.splice(remaining.indexOf(artifact), 1));
        }
        updateArtifactDrivenUi(body.artifacts);
      }

      async function loadLatestLongDeckJob() {
        const history = await requestJson("/api/presentations?limit=20");
        const latest = history.items.find((item) => (
          item.job_type === "long_deck" || item.job_type === "long_deck_v2"
        ));
        return latest ? requestJson(`/api/jobs/${latest.job_id}`) : null;
      }

      async function pollJob(id) {
        const job = await requestJson(`/api/jobs/${id}`);
        rememberActiveJob(job);
        setStatus(job.status, job.accepted, job.error_message || "");
        setProgress(job);
        updatePptMasterPackage(job);
        updatePptMasterExecution(job);
        updatePptMasterVisualProject(job);
        updatePptMasterRunner(job);
        updatePptMasterOutput(job);
        updateProductDashboard(job);
        await updateSlidePreviews(id);
        if (job.error_message) {
          errorMessage.textContent = jobErrorText(job);
        }
        if (isTerminalStatus(job.status)) {
          if (pollTimer) {
            clearTimeout(pollTimer);
          }
          pollTimer = null;
          setBusy(false);
          if (isTerminalStatus(job.status)) {
            rememberActiveJob(job);
          }
          await loadArtifacts(id);
          await loadPresentationHistory();
          return true;
        }
        return false;
      }

      function schedulePoll(id, delay = 1000) {
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(async () => {
          pollTimer = null;
          try {
            const finished = await pollJob(id);
            if (!finished) schedulePoll(id, 1000);
          } catch (error) {
            errorMessage.textContent = `状态更新暂时中断：${error.message}。正在自动重试。`;
            schedulePoll(id, 2000);
          }
        }, delay);
      }

      async function submitJob(url, payload) {
        if (pollTimer) {
          clearTimeout(pollTimer);
          pollTimer = null;
        }
        setBusy(true);
        setStatus("submitting");
        currentStage.textContent = "正在提交任务";
        resetElapsedClock();
        longRunningNotice.textContent = "";
        errorMessage.textContent = "";
        clearArtifacts();
        clearPptMasterPackage();
        clearPptMasterExecution();
        clearPptMasterVisualProject();
        clearPptMasterRunner();
        clearPptMasterOutput();
        updateArtifactDrivenUi([]);
        previewEmpty.hidden = false;
        currentPreviewKey = "";
        previewSlides.forEach((frame) => {
          frame.hidden = true;
          frame.removeAttribute("src");
        });

        try {
          const job = await requestJson(url, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
          });
          activeJobId = job.job_id;
          rememberActiveJob(job);
          jobId.textContent = job.job_id;
          setStatus(job.status, job.accepted, job.error_message || "");
          await loadPresentationHistory();
          const finished = await pollJob(job.job_id);
          if (!finished) {
            schedulePoll(job.job_id, 1000);
          }
        } catch (error) {
          errorMessage.textContent = error.message;
          setStatus("failed");
          setBusy(false);
        }
      }

      async function restoreLastLongDeckJob() {
        const rememberedId = localStorage.getItem(lastLongDeckJobStorageKey);
        if (rememberedId) {
          try {
            activeJobId = rememberedId;
            const job = await requestJson(`/api/jobs/${rememberedId}`);
            jobId.textContent = job.job_id;
            setStatus(job.status, job.accepted, job.error_message || "");
            setProgress(job);
            updatePptMasterPackage(job);
            updatePptMasterExecution(job);
            updatePptMasterVisualProject(job);
            updatePptMasterRunner(job);
            updatePptMasterOutput(job);
            updateProductDashboard(job);
            if (job.error_message) {
              errorMessage.textContent = jobErrorText(job);
            }
            await loadArtifacts(rememberedId);
            await updateSlidePreviews(rememberedId);
            if (!isTerminalStatus(job.status)) {
              setBusy(true);
              schedulePoll(rememberedId, 1000);
            }
            return;
          } catch (error) {
            forgetActiveJob();
          }
        }

        try {
          const latest = await loadLatestLongDeckJob();
          if (!latest) {
            return;
          }
          activeJobId = latest.job_id;
          rememberActiveJob(latest);
          jobId.textContent = latest.job_id;
          setStatus(latest.status, latest.accepted, latest.error_message || "");
          setProgress(latest);
          updatePptMasterPackage(latest);
          updatePptMasterExecution(latest);
          updatePptMasterVisualProject(latest);
          updatePptMasterRunner(latest);
          updatePptMasterOutput(latest);
          updateProductDashboard(latest);
          if (latest.error_message) {
            errorMessage.textContent = jobErrorText(latest);
          }
          await loadArtifacts(latest.job_id);
          await updateSlidePreviews(latest.job_id);
          if (!isTerminalStatus(latest.status)) {
            setBusy(true);
            schedulePoll(latest.job_id, 1000);
          }
        } catch (error) {
          errorMessage.textContent = error.message;
        }
      }

      longDeckForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        saveLongDeckDraft();
        const pageCount = Number(longSlideCount.value);
        if (pageCount <= 10) {
          await submitJob("/api/jobs", buildShortDeckPayload());
        } else {
          await submitJob("/api/long-deck-jobs", buildLongDeckPayload());
        }
      });

      cancelJobButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        try {
          const job = await requestJson(`/api/jobs/${activeJobId}/cancel`, {method: "POST"});
          setProgress(job);
          updatePptMasterPackage(job);
          updatePptMasterExecution(job);
          updatePptMasterVisualProject(job);
          updatePptMasterRunner(job);
          updatePptMasterOutput(job);
          setStatus(job.status, job.accepted, job.error_message || "");
        } catch (error) {
          errorMessage.textContent = error.message;
        }
      });

      preparePptMasterExecutionButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        preparePptMasterExecutionButton.disabled = true;
        try {
          await requestJson(`/api/long-deck-jobs/${activeJobId}/prepare-ppt-master-execution`, {method: "POST"});
          const job = await requestJson(`/api/jobs/${activeJobId}`);
          updatePptMasterPackage(job);
          updatePptMasterExecution(job);
          updatePptMasterVisualProject(job);
          updatePptMasterRunner(job);
          updatePptMasterOutput(job);
          updateProductDashboard(job);
          await updateSlidePreviews(activeJobId);
          await loadArtifacts(activeJobId);
        } catch (error) {
          errorMessage.textContent = error.message;
        } finally {
          preparePptMasterExecutionButton.disabled = false;
        }
      });

      bootstrapPptMasterProjectButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        bootstrapPptMasterProjectButton.disabled = true;
        try {
          await requestJson(`/api/long-deck-jobs/${activeJobId}/bootstrap-ppt-master-project`, {method: "POST"});
          const job = await requestJson(`/api/jobs/${activeJobId}`);
          updatePptMasterPackage(job);
          updatePptMasterExecution(job);
          updatePptMasterVisualProject(job);
          updatePptMasterRunner(job);
          updatePptMasterOutput(job);
          updateProductDashboard(job);
          await updateSlidePreviews(activeJobId);
          await loadArtifacts(activeJobId);
        } catch (error) {
          errorMessage.textContent = error.message;
        } finally {
          bootstrapPptMasterProjectButton.disabled = false;
        }
      });

      runPptMasterLocalExportButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        runPptMasterLocalExportButton.disabled = true;
        try {
          await requestJson(`/api/long-deck-jobs/${activeJobId}/run-ppt-master-local-export`, {method: "POST"});
          const job = await requestJson(`/api/jobs/${activeJobId}`);
          updatePptMasterPackage(job);
          updatePptMasterExecution(job);
          updatePptMasterVisualProject(job);
          updatePptMasterRunner(job);
          updatePptMasterOutput(job);
          updateProductDashboard(job);
          await updateSlidePreviews(activeJobId);
          await loadArtifacts(activeJobId);
        } catch (error) {
          errorMessage.textContent = error.message;
        } finally {
          runPptMasterLocalExportButton.disabled = false;
        }
      });

      resumeJobButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        await submitJob(`/api/long-deck-jobs/${activeJobId}/resume`, {});
      });

      interviewComposer.addEventListener("submit", async (event) => {
        event.preventDefault();
        await submitInterviewMessage(interviewInput.value);
      });

      skipInterviewQuestionButton.addEventListener("click", async () => {
        await submitInterviewMessage(
          "这个问题我暂时不确定，请根据已有信息给出合理建议并继续。",
          "skip"
        );
      });

      confirmGenerationButton.addEventListener("click", () => {
        longDeckForm.requestSubmit();
      });

      continueInterviewButton.addEventListener("click", () => {
        generationConfirmation.hidden = true;
        interviewComposer.hidden = false;
        generationConfirmation.after(interviewComposer);
        interviewInput.placeholder = "直接告诉 Agent 你想修改什么，例如：改成 15 页，面向小学生。";
        interviewHint.textContent = "继续用自然语言调整，Agent 会更新理解并再次准备生成。";
        interviewInput.focus();
      });

      manualBriefButton.addEventListener("click", () => {
        manualBriefVisible = true;
        longDeckForm.hidden = false;
        briefStatus.textContent = activeInterviewState?.status === "ready" ? "已理解" : "手动调整";
        briefStatus.classList.toggle("is-ready", activeInterviewState?.status === "ready");
        briefReadinessHint.textContent = "高级用户可以在这里直接修改 Agent 已整理的信息。";
        longDeckForm.scrollIntoView({behavior: "smooth", block: "nearest"});
      });

      newInterviewButton.addEventListener("click", () => {
        resetPresentationInterview();
        interviewInput.focus();
      });

      refreshHistoryButton.addEventListener("click", () => {
        loadPresentationHistory();
      });

      historyStatusFilter.addEventListener("change", () => {
        loadPresentationHistory();
      });

      historySearch.addEventListener("input", () => {
        if (historySearchTimer) clearTimeout(historySearchTimer);
        historySearchTimer = setTimeout(loadPresentationHistory, 250);
      });

      document.querySelectorAll("[data-scroll-target]").forEach((control) => {
        control.addEventListener("click", () => {
          const target = document.getElementById(control.dataset.scrollTarget);
          if (target) target.scrollIntoView({behavior: "smooth", block: "start"});
          document.querySelectorAll(".project-tab").forEach((tab) => tab.classList.toggle("is-active", tab === control));
          document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("is-active", item === control));
        });
      });

      document.getElementById("long_topic").addEventListener("input", (event) => {
        projectTitle.textContent = event.target.value.trim() || "PPT 项目工作台";
        saveLongDeckDraft();
      });

      [document.getElementById("long_audience"), document.getElementById("long_user_requirements")]
        .forEach((field) => field.addEventListener("input", saveLongDeckDraft));
      longSlideCount.addEventListener("input", () => {
        updateGenerationChoice();
        saveLongDeckDraft();
      });

      window.addEventListener("load", () => {
        loadLongDeckDraft();
        updateGenerationChoice();
        projectTitle.textContent = document.getElementById("long_topic").value.trim() || "PPT 项目工作台";
        setInterval(renderElapsedClock, 250);
        loadPresentationHistory();
        restorePresentationInterview();
        restoreLastLongDeckJob().catch((error) => {
          errorMessage.textContent = error.message;
        });
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
    interview_id: str | None = Field(default=None, min_length=1, max_length=64)


class CreateLongDeckJobRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(default=30, ge=11, le=100)
    language: str = Field(default="zh-CN", min_length=1)
    deck_type: str = Field(default="technical_product_share", min_length=1)
    user_requirements: str = Field(..., min_length=1)
    batch_size: int = Field(default=2, ge=1, le=10)
    max_batch_attempts: int = Field(default=1, ge=1, le=3)
    interview_id: str | None = Field(default=None, min_length=1, max_length=64)


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
    _backfill_presentation_request_history(app.state.job_store, jobs_root)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

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
        if payload.slide_count != 30:
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
            model = _create_v2_model_client() if payload.slide_count != 30 else _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not prepare long deck resume job: {detail}") from exc

        resume_job_type = "long_deck_v2" if payload.slide_count != 30 else "long_deck"
        resume_job = app.state.job_store.create_job(job_type=resume_job_type)
        _save_presentation_request_snapshot(
            app.state.job_store,
            resume_job.job_id,
            payload,
            resumed_from_job_id=job_id,
        )
        if payload.slide_count != 30:
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
            "total_requested": job.total_batches or len(numbers),
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
