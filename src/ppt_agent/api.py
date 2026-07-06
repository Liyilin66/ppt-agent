"""Private beta FastAPI backend for job-based deck builds."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field

from ppt_agent.generation import DeckGenerationRequest
from ppt_agent.job_store import ArtifactKind, ArtifactRecord, JobRecord, JobStore
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
from ppt_agent.runtime import StageEvent, sanitize_error_message, observed_stage


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


DEFAULT_LONG_DECK_JOB_TIMEOUT_SECONDS = 3600


def _long_deck_job_timeout_seconds() -> int:
    return _env_int("LONG_DECK_JOB_TIMEOUT_SECONDS", DEFAULT_LONG_DECK_JOB_TIMEOUT_SECONDS)


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

      .artifact-group-label {
        margin-top: 6px;
        color: #172033;
        font-weight: 800;
      }

      .metadata-grid {
        display: grid;
        grid-template-columns: minmax(150px, 0.35fr) 1fr;
        gap: 8px 14px;
        margin: 0 0 18px;
      }

      .metadata-grid dt {
        color: #506078;
        font-weight: 700;
      }

      .metadata-grid dd {
        margin: 0;
        overflow-wrap: anywhere;
      }

      .hint {
        margin-bottom: 0;
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
        <p>当前 batch：<span id="currentBatch">暂无</span> / <span id="totalBatches">0</span></p>
        <p>Batch 结果：已完成 <span id="completedBatches">0</span>，失败 <span id="failedBatches">0</span></p>
        <p>运行时间：<span id="elapsedSeconds">0</span> 秒</p>
        <p id="longRunningNotice"></p>
        <p id="errorMessage"></p>
        <button id="cancelJobButton" type="button" disabled>取消长 PPT任务</button>
        <button id="resumeJobButton" type="button" disabled>继续/重试长 PPT</button>
      </section>

      <section>
        <h2>生成文件</h2>
        <ul id="artifacts"></ul>
      </section>

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
    </main>

    <script>
      const form = document.getElementById("jobForm");
      const longDeckForm = document.getElementById("longDeckForm");
      const button = document.getElementById("generateButton");
      const longDeckButton = document.getElementById("generateLongDeckButton");
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
      const pptMasterOutputSection = document.getElementById("pptMasterOutputSection");
      const pptMasterOutputMessage = document.getElementById("pptMasterOutputMessage");
      const pptMasterOutputDetected = document.getElementById("pptMasterOutputDetected");
      const pptMasterOutputSlideCount = document.getElementById("pptMasterOutputSlideCount");
      const pptMasterOutputGenerationStatus = document.getElementById("pptMasterOutputGenerationStatus");
      const pptMasterOutputDir = document.getElementById("pptMasterOutputDir");
      const pptMasterOutputHasNotes = document.getElementById("pptMasterOutputHasNotes");
      const cancelJobButton = document.getElementById("cancelJobButton");
      const resumeJobButton = document.getElementById("resumeJobButton");
      const lastLongDeckJobStorageKey = "ppt_agent_last_long_deck_job_id";
      const pptMasterArtifactNames = new Set([
        "ppt_master_source",
        "ppt_master_run_prompt",
        "ppt_master_package_manifest",
        "ppt_master_package_README",
        "ppt_master_execution_plan",
        "ppt_master_generated_pptx",
        "ppt_master_generation_notes",
        "ppt_master_output_manifest"
      ]);
      const artifactDisplayNames = {
        ppt_master_source: "PPT Master Source Markdown",
        ppt_master_run_prompt: "PPT Master Run Prompt",
        ppt_master_package_manifest: "PPT Master Package Manifest",
        ppt_master_package_README: "PPT Master Package README",
        ppt_master_execution_plan: "PPT Master Execution Plan",
        ppt_master_generated_pptx: "PPT Master Generated PPTX",
        ppt_master_generation_notes: "PPT Master Generation Notes",
        ppt_master_output_manifest: "PPT Master Output Manifest"
      };
      let pollTimer = null;
      let activeJobId = null;

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
        button.disabled = isBusy;
        longDeckButton.disabled = isBusy;
      }

      function isLongDeckJob(job) {
        return job.job_type === "long_deck" || Boolean(job.total_batches);
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
        return stageText[stage] || stage || "暂无";
      }

      function setProgress(job) {
        currentStage.textContent = stageLabel(job.current_stage);
        currentBatch.textContent = job.current_batch || "暂无";
        totalBatches.textContent = String(job.total_batches || 0);
        completedBatches.textContent = String(job.completed_batches || 0);
        failedBatches.textContent = String(job.failed_batches || 0);
        elapsedSeconds.textContent = String(job.elapsed_seconds || 0);
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
        if (job && job.job_type === "long_deck") {
          localStorage.setItem(lastLongDeckJobStorageKey, job.job_id);
        }
      }

      function forgetActiveJob() {
        localStorage.removeItem(lastLongDeckJobStorageKey);
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
        const pptMasterArtifacts = [];
        for (const artifact of body.artifacts) {
          if (pptMasterArtifactNames.has(artifact.name)) {
            pptMasterArtifacts.push(artifact);
          } else {
            appendArtifactLink(artifact);
          }
        }
        if (pptMasterArtifacts.length) {
          appendArtifactGroupLabel("PPT Master 渲染包");
          for (const artifact of pptMasterArtifacts) {
            appendArtifactLink(artifact);
          }
        }
      }

      async function loadLatestLongDeckJob() {
        const response = await fetch("/api/jobs/latest?job_type=long_deck");
        if (!response.ok) {
          return null;
        }
        const body = await response.json();
        if (!body.job_id) {
          return null;
        }
        return body;
      }

      async function pollJob(id) {
        const job = await requestJson(`/api/jobs/${id}`);
        rememberActiveJob(job);
        setStatus(job.status, job.accepted, job.error_message || "");
        setProgress(job);
        updatePptMasterPackage(job);
        updatePptMasterExecution(job);
        updatePptMasterOutput(job);
        if (job.error_message) {
          errorMessage.textContent = job.error_message;
        }
        if (isTerminalStatus(job.status)) {
          if (pollTimer) {
            clearInterval(pollTimer);
          }
          pollTimer = null;
          setBusy(false);
          if (isTerminalStatus(job.status)) {
            rememberActiveJob(job);
          }
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
        clearPptMasterPackage();
        clearPptMasterExecution();
        clearPptMasterOutput();

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
            updatePptMasterOutput(job);
            if (job.error_message) {
              errorMessage.textContent = job.error_message;
            }
            await loadArtifacts(rememberedId);
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
          updatePptMasterOutput(latest);
          if (latest.error_message) {
            errorMessage.textContent = latest.error_message;
          }
          await loadArtifacts(latest.job_id);
        } catch (error) {
          errorMessage.textContent = error.message;
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

      cancelJobButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        try {
          const job = await requestJson(`/api/jobs/${activeJobId}/cancel`, {method: "POST"});
          setProgress(job);
          updatePptMasterPackage(job);
          updatePptMasterExecution(job);
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
          updatePptMasterOutput(job);
          await loadArtifacts(activeJobId);
        } catch (error) {
          errorMessage.textContent = error.message;
        } finally {
          preparePptMasterExecutionButton.disabled = false;
        }
      });

      resumeJobButton.addEventListener("click", async () => {
        if (!activeJobId) {
          return;
        }
        await submitJob(`/api/long-deck-jobs/${activeJobId}/resume`, {});
      });

      window.addEventListener("load", () => {
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
    output_dir: str | None = None
    expected_pptx_path: str | None = None
    suggested_steps: list[str] = Field(default_factory=list)
    message: str


class JobResponse(JobRecord):
    ppt_master_package: PptMasterPackageResponse | None = None
    ppt_master_execution: PptMasterExecutionResponse | None = None
    ppt_master_output: PptMasterOutputResponse | None = None


class ArtifactResponse(StrictModel):
    artifact_id: str
    name: str
    kind: ArtifactKind
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
        stage.startswith("generating_batch_")
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
            output_dir=str(plan.output_dir),
            expected_pptx_path=str(plan.expected_pptx_path),
            suggested_steps=plan.suggested_steps,
            message=_ppt_master_execution_message(plan.status),
        )

    output_dir = _ppt_master_output_dir_for_job(jobs_root, job.job_id)
    return PptMasterExecutionResponse(
        status="not_prepared",
        plan_artifact_id=None,
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
    output = _ppt_master_output_response(store, jobs_root, job)
    data["ppt_master_package"] = package.model_dump(mode="python") if package is not None else None
    data["ppt_master_execution"] = execution.model_dump(mode="python") if execution is not None else None
    data["ppt_master_output"] = output.model_dump(mode="python") if output is not None else None
    return JobResponse.model_validate(data)


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

    if relative_path == Path("ppt_master_package/source.md"):
        return None
    ppt_master_package_names = {
        Path("ppt_master_package/run_prompt.md"): PPT_MASTER_RUN_PROMPT_ARTIFACT,
        Path("ppt_master_package/README.md"): PPT_MASTER_README_ARTIFACT,
        Path("ppt_master_package/manifest.json"): PPT_MASTER_MANIFEST_ARTIFACT,
        Path(PPT_MASTER_EXECUTION_PLAN_FILENAME): PPT_MASTER_EXECUTION_PLAN_ARTIFACT,
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

        job = app.state.job_store.create_job(job_type="short_deck")
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

        job = app.state.job_store.create_job(job_type="long_deck")
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
        if original_job.job_type != "long_deck":
            raise HTTPException(status_code=400, detail="Only long deck jobs can be resumed.")
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set on the server.")

        output_dir = app.state.jobs_root / job_id
        try:
            payload = _load_long_deck_request_artifact(output_dir)
            model = _create_chat_model()
        except Exception as exc:
            detail = sanitize_error_message(exc)
            raise HTTPException(status_code=503, detail=f"Could not prepare long deck resume job: {detail}") from exc

        resume_job = app.state.job_store.create_job(job_type="long_deck")
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
            output_dir=str(plan.output_dir),
            expected_pptx_path=str(plan.expected_pptx_path),
            suggested_steps=plan.suggested_steps,
            message=_ppt_master_execution_message(plan.status),
        )

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str) -> JobResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.job_type != "long_deck":
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

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> JobResponse:
        job = app.state.job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        job = _expire_stale_job(app.state.job_store, job)
        return _job_response(app.state.job_store, app.state.jobs_root, job)

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
