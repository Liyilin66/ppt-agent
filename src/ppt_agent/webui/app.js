/* Browser-only controller for the framework-free ppt-agent workspace. */

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
const workspaceGrid = document.getElementById("workspaceGrid");
const projectTitle = document.getElementById("projectTitle");
const headerNewTaskButton = document.getElementById("headerNewTaskButton");
const viewEyebrow = document.getElementById("viewEyebrow");
const viewDescription = document.getElementById("viewDescription");
const viewContextLabel = document.getElementById("viewContextLabel");
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
const liveSlideThumbnails = document.getElementById("liveSlideThumbnails");
const liveSlidePreview = document.getElementById("liveSlidePreview");
const liveCanvasEmpty = document.getElementById("liveCanvasEmpty");
const liveSlideCount = document.getElementById("liveSlideCount");
const liveCanvasTitle = document.getElementById("liveCanvasTitle");
const liveCanvasSubtitle = document.getElementById("liveCanvasSubtitle");
const liveStatusDot = document.getElementById("liveStatusDot");
const followLatestSlideButton = document.getElementById("followLatestSlideButton");
const studioGeneratedPages = document.getElementById("studioGeneratedPages");
const studioCurrentPage = document.getElementById("studioCurrentPage");
const studioElapsedSeconds = document.getElementById("studioElapsedSeconds");
const studioAgentActivity = document.getElementById("studioAgentActivity");
const studioQualityStatus = document.getElementById("studioQualityStatus");
const studioOutputStatus = document.getElementById("studioOutputStatus");
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
let interviewRequestInFlight = false;
let currentPreviewKey = "";
let activeView = "create";
let livePreviewManifest = null;
let livePreviewKey = "";
let livePreviewSelectedSlide = null;
let livePreviewLatestSlide = null;
let livePreviewFollowingLatest = true;
let liveThumbnailObserver = null;
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
  studioElapsedSeconds.textContent = String(value);
}

const viewMetadata = {
  create: {
    eyebrow: "Create with Agent",
    title: "创建演示",
    description: "通过对话把想法变成可执行的演示需求。",
    context: "需求访谈"
  },
  rebuild: {
    eyebrow: "Image understanding & rebuild",
    title: "图片重建",
    description: "判断图片类型，把演示页面重建为可编辑 PPT。",
    context: "图片理解"
  },
  outline: {
    eyebrow: "Outline & script",
    title: "大纲脚本",
    description: "生成前逐页确认大纲、要点和演讲备注。",
    context: "生成前确认"
  },
  studio: {
    eyebrow: "Live generation studio",
    title: "生成工作台",
    description: "逐页查看生成结果、当前进度和 Agent 正在执行的步骤。",
    context: "实时生成"
  },
  preview: {
    eyebrow: "Presentation preview",
    title: "页面预览",
    description: "查看视觉高光页、章节结构和整套演示的完成度。",
    context: "视觉审阅"
  },
  history: {
    eyebrow: "Presentation library",
    title: "演示历史",
    description: "查找之前的创建请求、生成状态和最终交付文件。",
    context: "本地 SQLite"
  },
  delivery: {
    eyebrow: "Delivery center",
    title: "交付中心",
    description: "下载可编辑 PPTX、质量报告和 PPT Master 技术产物。",
    context: "文件与质量"
  }
};

function currentProjectTopic() {
  return document.getElementById("long_topic").value.trim();
}

function updateViewHeader() {
  const metadata = viewMetadata[activeView] || viewMetadata.create;
  const topic = currentProjectTopic();
  viewEyebrow.textContent = metadata.eyebrow;
  projectTitle.textContent = activeView === "create" || !topic ? metadata.title : topic;
  viewDescription.textContent = metadata.description;
  viewContextLabel.textContent = metadata.context;
}

function setAppView(view) {
  if (!viewMetadata[view]) return;
  activeView = view;
  workspaceGrid.dataset.view = view;
  document.querySelectorAll("[data-app-view]").forEach((node) => {
    node.hidden = node.dataset.appView !== view;
  });
  document.querySelectorAll("[data-view-target]").forEach((control) => {
    control.classList.toggle("is-active", control.dataset.viewTarget === view);
  });
  headerNewTaskButton.hidden = view === "create";
  primaryDownloadTop.hidden = view === "create";
  updateViewHeader();
  window.scrollTo({top: 0, behavior: "smooth"});
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
  const colors = ["#8b7cf7", "#38bdf8", "#f472b6", "#fbbf24", "#34d399", "#a78bfa", "#22d3ee", "#fb7185"];
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
  updateViewHeader();
  const isV2 = job.job_type === "long_deck_v2" || (job.current_stage || "").startsWith("v2_");
  totalBatchesMeta.textContent = String(job.total_batches || 0);
  railFailedBatches.textContent = String(job.failed_batches || 0);
  renderElapsedClock();
  effortSummary.textContent = Number(job.total_batches || 0) >= 10 ? "较高" : "标准";
  const qualityFailed = job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate";
  qualitySummary.textContent = qualityFailed ? "需恢复" : (job.accepted === true ? "通过" : "未评估");
  railQualityStatus.textContent = qualitySummary.textContent;
  studioQualityStatus.textContent = qualitySummary.textContent;
  outputSummary.textContent = job.ppt_master_output?.detected ? "已交付" : (job.status === "succeeded" ? "已生成" : "未生成");
  studioOutputStatus.textContent = outputSummary.textContent;
  const deliveredSlideCount = job.ppt_master_output?.slide_count ?? (isV2 && job.status === "succeeded" ? job.total_batches : null);
  deliverySlideCount.textContent = deliveredSlideCount == null ? "未知" : `${deliveredSlideCount} 页`;
  deliveryStatus.textContent = job.ppt_master_output?.detected || (isV2 && job.status === "succeeded") ? "已注册到当前 job，可直接下载" : "生成后可直接下载";
  updateStageTrack(job);
  updateNarrativeHealth(job);
  renderChapterAllocation(job.ppt_master_output?.slide_count || (isV2 ? Number(job.total_batches || 100) : 30));
  updateRevisionPanel(job);
}

function jobErrorText(job) {
  const qualityFailed = job.status === "failed_quality_gate" || job.status === "partial_failed_quality_gate";
  if (qualityFailed && job.ppt_master_output?.detected) {
    return "旧 renderer 质量门禁未通过；PPT Master 恢复成片已注册，可继续下载验收。";
  }
  return job.error_message || "";
}

function liveSlideUrl(id, slideNumber, updateToken) {
  return `/api/jobs/${id}/preview-slides/${slideNumber}?v=${updateToken || Date.now()}`;
}

function selectLiveSlide(id, slideNumber, manifest, {followLatest = false} = {}) {
  if (!slideNumber) return;
  livePreviewSelectedSlide = slideNumber;
  livePreviewFollowingLatest = followLatest;
  const targetUrl = liveSlideUrl(id, slideNumber, manifest.update_token);
  if (liveSlidePreview.getAttribute("src") !== targetUrl) {
    liveSlidePreview.src = targetUrl;
  }
  liveSlidePreview.hidden = false;
  liveCanvasEmpty.hidden = true;
  liveCanvasTitle.textContent = `第 ${slideNumber} 页`;
  liveCanvasSubtitle.textContent = `已生成 ${manifest.available_slide_numbers.length} / ${manifest.total_requested || manifest.available_slide_numbers.length} 页`;
  studioCurrentPage.textContent = `第 ${slideNumber} 页`;
  followLatestSlideButton.disabled = slideNumber === livePreviewLatestSlide;
  liveSlideThumbnails.querySelectorAll(".live-slide-thumbnail").forEach((button) => {
    button.classList.toggle("is-selected", Number(button.dataset.slideNumber) === slideNumber);
  });
  const selectedPreview = liveSlideThumbnails.querySelector(
    `.live-slide-thumbnail[data-slide-number="${slideNumber}"] .live-slide-thumbnail-preview`
  );
  if (selectedPreview) mountLiveThumbnailPreview(selectedPreview);
}

function mountLiveThumbnailPreview(preview) {
  if (preview.querySelector("iframe")) return;
  const frame = document.createElement("iframe");
  frame.title = preview.dataset.previewTitle || "页面缩略图";
  frame.loading = "lazy";
  frame.tabIndex = -1;
  frame.setAttribute("sandbox", "allow-scripts");
  frame.src = preview.dataset.previewUrl;
  preview.appendChild(frame);
}

function unmountLiveThumbnailPreview(preview) {
  const frame = preview.querySelector("iframe");
  if (frame) frame.remove();
}

function observeLiveThumbnailPreviews() {
  if (liveThumbnailObserver) liveThumbnailObserver.disconnect();
  const previews = Array.from(
    liveSlideThumbnails.querySelectorAll(".live-slide-thumbnail-preview")
  );
  if (!("IntersectionObserver" in window)) {
    previews.forEach(mountLiveThumbnailPreview);
    return;
  }
  // Keep large decks responsive by mounting only thumbnails near the scroll viewport.
  liveThumbnailObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        mountLiveThumbnailPreview(entry.target);
      } else {
        unmountLiveThumbnailPreview(entry.target);
      }
    });
  }, {root: liveSlideThumbnails, rootMargin: "180px 0px"});
  previews.forEach((preview) => liveThumbnailObserver.observe(preview));
}

function resetLiveSlideWorkspace(totalRequested = Number(longSlideCount.value) || 0) {
  if (liveThumbnailObserver) {
    liveThumbnailObserver.disconnect();
    liveThumbnailObserver = null;
  }
  livePreviewManifest = null;
  livePreviewKey = "";
  livePreviewSelectedSlide = null;
  livePreviewLatestSlide = null;
  livePreviewFollowingLatest = true;
  liveSlidePreview.hidden = true;
  liveSlidePreview.removeAttribute("src");
  liveCanvasEmpty.hidden = false;
  liveCanvasTitle.textContent = "实时画布";
  liveCanvasSubtitle.textContent = "等待页面生成";
  liveSlideCount.textContent = `0 / ${totalRequested || 0} 页`;
  studioGeneratedPages.textContent = "0";
  studioCurrentPage.textContent = "等待中";
  followLatestSlideButton.disabled = true;
  liveSlideThumbnails.replaceChildren();
  const placeholders = Math.min(Math.max(totalRequested || 3, 3), 6);
  for (let index = 0; index < placeholders; index += 1) {
    const placeholder = document.createElement("div");
    placeholder.className = "live-slide-placeholder";
    liveSlideThumbnails.appendChild(placeholder);
  }
}

function renderLiveSlideWorkspace(id, manifest, available) {
  const totalRequested = Number(manifest.total_requested || available.length || longSlideCount.value || 0);
  livePreviewManifest = {...manifest, available_slide_numbers: available};
  liveSlideCount.textContent = `${available.length} / ${totalRequested} 页`;
  studioGeneratedPages.textContent = String(available.length);
  liveStatusDot.classList.toggle("is-running", available.length < totalRequested);

  if (!available.length) {
    resetLiveSlideWorkspace(totalRequested);
    liveStatusDot.classList.add("is-running");
    liveCanvasSubtitle.textContent = "正在规划内容与视觉主题";
    return;
  }

  const previousLatest = livePreviewLatestSlide;
  livePreviewLatestSlide = available[available.length - 1];
  const shouldFollowLatest = livePreviewFollowingLatest
    || livePreviewSelectedSlide == null
    || livePreviewSelectedSlide === previousLatest;
  const nextSelected = shouldFollowLatest ? livePreviewLatestSlide : livePreviewSelectedSlide;
  const nextKey = `${id}:${manifest.update_token || "0"}:${available.join(",")}`;

  if (nextKey !== livePreviewKey) {
    if (liveThumbnailObserver) {
      liveThumbnailObserver.disconnect();
      liveThumbnailObserver = null;
    }
    liveSlideThumbnails.replaceChildren();
    available.forEach((slideNumber) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "live-slide-thumbnail";
      button.dataset.slideNumber = String(slideNumber);
      button.setAttribute("aria-label", `查看第 ${slideNumber} 页`);

      const number = document.createElement("span");
      number.className = "live-slide-thumbnail-number";
      number.textContent = String(slideNumber);
      const preview = document.createElement("span");
      preview.className = "live-slide-thumbnail-preview";
      preview.dataset.previewTitle = `第 ${slideNumber} 页缩略图`;
      preview.dataset.previewUrl = liveSlideUrl(id, slideNumber, manifest.update_token);
      button.append(number, preview);
      button.addEventListener("click", () => {
        selectLiveSlide(id, slideNumber, livePreviewManifest, {followLatest: false});
      });
      liveSlideThumbnails.appendChild(button);
    });
    observeLiveThumbnailPreviews();
    livePreviewKey = nextKey;
  }

  selectLiveSlide(id, nextSelected, livePreviewManifest, {followLatest: shouldFollowLatest});
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
  renderLiveSlideWorkspace(id, manifest, available);
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
  const readableStage = stageLabel(job.current_stage);
  currentStage.textContent = readableStage;
  currentBatch.textContent = job.current_batch || "暂无";
  totalBatches.textContent = String(job.total_batches || 0);
  totalBatchesMeta.textContent = String(job.total_batches || 0);
  completedBatches.textContent = String(job.completed_batches || 0);
  failedBatches.textContent = String(job.failed_batches || 0);
  railFailedBatches.textContent = String(job.failed_batches || 0);
  studioAgentActivity.textContent = readableStage;
  if (job.current_batch) {
    studioCurrentPage.textContent = job.current_batch
      .replace(/^page_0*/, "第 ")
      .replace(/^batch_0*/, "第 ");
  }
  syncElapsedClock(job);
  const isTerminal = isTerminalStatus(job.status);
  liveStatusDot.classList.toggle("is-running", !isTerminal);
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

function interviewAttachmentIds() {
  if (!activeInterviewState || !Array.isArray(activeInterviewState.messages)) return [];
  const ids = [];
  activeInterviewState.messages.forEach((message) => {
    (message.attachments || []).forEach((attachment) => {
      if (attachment.upload_id && !ids.includes(attachment.upload_id)) {
        ids.push(attachment.upload_id);
      }
    });
  });
  return ids;
}

function buildLongDeckPayload() {
  return {
    topic: document.getElementById("long_topic").value.trim(),
    audience: document.getElementById("long_audience").value.trim(),
    slide_count: Number(document.getElementById("long_slide_count").value),
    language: "zh-CN",
    deck_type: "visual_design_v2",
    user_requirements: document.getElementById("long_user_requirements").value.trim(),
    interview_id: activeInterviewId,
    attachment_ids: interviewAttachmentIds()
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

function appendInterviewMessage(role, content, attachments = null) {
  const message = document.createElement("div");
  message.className = "conversation-message";
  message.dataset.role = role;
  const label = document.createElement("strong");
  label.textContent = role === "user" ? "你" : "PPT Agent";
  message.appendChild(label);
  message.appendChild(document.createTextNode(content));
  appendAttachmentChipsTo(message, attachments);
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
    updateViewHeader();
  }
  longDeckForm.hidden = true;
}

function renderGenerationConfirmation(brief) {
  confirmationTopic.textContent = brief.topic || "待确认";
  confirmationAudience.textContent = brief.audience || "待确认";
  confirmationSlideCount.textContent = brief.slide_count ? `${brief.slide_count} 页` : "待确认";
  confirmationVisual.textContent = brief.visual_direction || brief.tone || "待确认";
  const confirmedPageCount = Number(brief.slide_count) || 0;
  confirmGenerationButton.textContent = confirmedPageCount > 3
    ? `生成大纲与脚本（${confirmedPageCount} 页）`
    : `开始生成 ${confirmedPageCount || ""} 页 PPT`.replace("  页", "");
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
    appendInterviewMessage(message.role, content, message.attachments || null);
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
  if (interviewAttachmentPicker.hasPendingUploads()) {
    interviewHint.textContent = "附件还在上传中，请稍候再发送。";
    return;
  }
  const attachmentIds = interviewAttachmentPicker.ids();
  const attachmentSummaries = interviewAttachmentPicker.summaries();
  appendInterviewMessage("user", content, attachmentSummaries);
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
      ? {message: content, selected_option_id: selectedOptionId, attachment_ids: attachmentIds}
      : {message: content, attachment_ids: attachmentIds};
    const state = await requestJson(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    interviewAttachmentPicker.clear();
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
  updateViewHeader();
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
  document.getElementById("long_topic").value = item.topic || "";
  setAppView("studio");
  errorMessage.textContent = job.error_message ? jobErrorText(job) : "";
  await Promise.all([loadArtifacts(job.job_id), updateSlidePreviews(job.job_id)]);
  if (!isTerminalStatus(job.status)) {
    setBusy(true);
    schedulePoll(job.job_id, 1000);
  }
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

function prepareJobWorkspace(requestedPages) {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  setBusy(true);
  setStatus("submitting");
  setAppView("studio");
  currentStage.textContent = "正在提交任务";
  studioAgentActivity.textContent = "正在提交任务并准备内容规划。";
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
  resetLiveSlideWorkspace(requestedPages || 0);
  previewEmpty.hidden = false;
  currentPreviewKey = "";
  previewSlides.forEach((frame) => {
    frame.hidden = true;
    frame.removeAttribute("src");
  });
}

async function trackCreatedJob(job) {
  activeJobId = job.job_id;
  rememberActiveJob(job);
  jobId.textContent = job.job_id;
  setStatus(job.status, job.accepted, job.error_message || "");
  await loadPresentationHistory();
  const finished = await pollJob(job.job_id);
  if (!finished) {
    schedulePoll(job.job_id, 1000);
  }
}

async function submitJob(url, payload) {
  prepareJobWorkspace(Number(payload.slide_count || payload.slides || longSlideCount.value) || 0);

  try {
    const job = await requestJson(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    await trackCreatedJob(job);
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
      setAppView("studio");
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
    setAppView("studio");
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

/* ---------- Chat attachments (interview + revision composers) ---------- */

const MAX_ATTACHMENTS_PER_MESSAGE = 5;
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

function formatFileSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)}KB`;
  return `${bytes}B`;
}

function attachmentIcon(kind) {
  return kind === "image" ? "🖼" : "📄";
}

function normalizedAttachmentFile(file) {
  // Pasted screenshots arrive as a generic "image.png"; give them unique names.
  if (/^image\.(png|jpe?g|webp)$/i.test(file.name)) {
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
    const suffix = file.name.split(".").pop();
    return new File([file], `粘贴图片-${stamp}.${suffix}`, {type: file.type});
  }
  return file;
}

function createAttachmentPicker({input, button, chips, dropzone, pasteTarget, onError}) {
  const items = [];

  function render() {
    chips.replaceChildren();
    chips.hidden = items.length === 0;
    items.forEach((item, index) => {
      const chip = document.createElement("span");
      chip.className = "attachment-chip" + (item.uploading ? " is-uploading" : "") + (item.error ? " is-error" : "");
      const label = document.createElement("span");
      label.textContent = `${attachmentIcon(item.kind)} ${item.name}`
        + (item.uploading ? " · 上传中…" : item.error ? ` · ${item.error}` : ` · ${formatFileSize(item.size)}`);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.title = "移除附件";
      remove.addEventListener("click", () => {
        items.splice(index, 1);
        render();
      });
      chip.append(label, remove);
      chips.appendChild(chip);
    });
  }

  async function uploadFile(file) {
    if (items.filter((item) => !item.error).length >= MAX_ATTACHMENTS_PER_MESSAGE) {
      onError(`一条消息最多 ${MAX_ATTACHMENTS_PER_MESSAGE} 个附件`);
      return;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      onError(`「${file.name}」超过 20MB 限制`);
      return;
    }
    const item = {
      name: file.name,
      size: file.size,
      kind: /\.(png|jpe?g|webp)$/i.test(file.name) ? "image" : "document",
      uploading: true,
      error: null,
      upload_id: null
    };
    items.push(item);
    render();
    try {
      const response = await fetch(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        body: file
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "上传失败");
      item.upload_id = body.upload_id;
      item.kind = body.kind;
      item.uploading = false;
    } catch (error) {
      item.uploading = false;
      item.error = error.message;
    }
    render();
  }

  async function uploadAll(fileList) {
    for (const file of Array.from(fileList || [])) {
      await uploadFile(normalizedAttachmentFile(file));
    }
  }

  button.addEventListener("click", () => input.click());
  input.addEventListener("change", async () => {
    const files = Array.from(input.files || []);
    input.value = "";
    await uploadAll(files);
  });

  if (pasteTarget) {
    pasteTarget.addEventListener("paste", async (event) => {
      const files = Array.from(event.clipboardData?.files || []);
      if (!files.length) return;
      event.preventDefault();
      await uploadAll(files);
    });
  }

  if (dropzone) {
    let dragDepth = 0;
    dropzone.addEventListener("dragenter", (event) => {
      if (!event.dataTransfer?.types?.includes("Files")) return;
      event.preventDefault();
      dragDepth += 1;
      dropzone.classList.add("is-dragover");
    });
    dropzone.addEventListener("dragover", (event) => {
      if (!event.dataTransfer?.types?.includes("Files")) return;
      event.preventDefault();
    });
    dropzone.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) dropzone.classList.remove("is-dragover");
    });
    dropzone.addEventListener("drop", async (event) => {
      const files = Array.from(event.dataTransfer?.files || []);
      if (!files.length) return;
      event.preventDefault();
      dragDepth = 0;
      dropzone.classList.remove("is-dragover");
      await uploadAll(files);
    });
  }

  return {
    ids() {
      return items.filter((item) => item.upload_id).map((item) => item.upload_id);
    },
    summaries() {
      return items
        .filter((item) => item.upload_id)
        .map((item) => ({name: item.name, kind: item.kind}));
    },
    hasPendingUploads() {
      return items.some((item) => item.uploading);
    },
    clear() {
      items.length = 0;
      render();
    }
  };
}

function appendAttachmentChipsTo(messageNode, attachments) {
  if (!attachments || !attachments.length) return;
  const row = document.createElement("span");
  row.className = "message-attachments";
  attachments.forEach((attachment) => {
    const chip = document.createElement("span");
    chip.className = "message-attachment";
    chip.textContent = `${attachmentIcon(attachment.kind)} ${attachment.name}`;
    row.appendChild(chip);
  });
  messageNode.appendChild(row);
}

const interviewAttachmentPicker = createAttachmentPicker({
  input: document.getElementById("interviewFileInput"),
  button: document.getElementById("interviewAttachButton"),
  chips: document.getElementById("interviewAttachmentChips"),
  dropzone: document.getElementById("interviewComposer"),
  pasteTarget: document.getElementById("interviewInput"),
  onError(message) {
    interviewHint.textContent = message;
  }
});

const revisionAttachmentPicker = createAttachmentPicker({
  input: document.getElementById("revisionFileInput"),
  button: document.getElementById("revisionAttachButton"),
  chips: document.getElementById("revisionAttachmentChips"),
  dropzone: document.getElementById("revisionComposer"),
  pasteTarget: document.getElementById("revisionInput"),
  onError(message) {
    appendRevisionMessage("assistant", message);
  }
});

/* ---------- Post-generation revision chat ---------- */

const revisionPanel = document.getElementById("revisionPanel");
const revisionStatusChip = document.getElementById("revisionStatusChip");
const revisionMessages = document.getElementById("revisionMessages");
const revisionEmpty = document.getElementById("revisionEmpty");
const revisionComposer = document.getElementById("revisionComposer");
const revisionInput = document.getElementById("revisionInput");
const sendRevisionButton = document.getElementById("sendRevisionButton");
const revisionQuotePageButton = document.getElementById("revisionQuotePageButton");

let revisionJobId = null;
let revisionPollTimer = null;
let revisionBusy = false;

function appendRevisionMessage(role, content, options = {}) {
  const message = document.createElement("div");
  message.className = "revision-message" + (options.pending ? " is-pending" : "");
  message.dataset.role = role;
  const label = document.createElement("strong");
  label.textContent = role === "user" ? "你" : "PPT Agent";
  message.appendChild(label);
  message.appendChild(document.createTextNode(content));
  revisionMessages.appendChild(message);
  revisionEmpty.hidden = true;
  revisionMessages.scrollTop = revisionMessages.scrollHeight;
  return message;
}

function setRevisionBusy(busy) {
  revisionBusy = busy;
  sendRevisionButton.disabled = busy;
  revisionStatusChip.textContent = busy ? "修改中" : "待命";
}

function stopRevisionPolling() {
  if (revisionPollTimer) {
    clearTimeout(revisionPollTimer);
    revisionPollTimer = null;
  }
}

function resetRevisionPanel() {
  stopRevisionPolling();
  revisionMessages.querySelectorAll(".revision-message").forEach((node) => node.remove());
  revisionEmpty.hidden = false;
  setRevisionBusy(false);
}

function updateRevisionQuoteButton() {
  const slide = livePreviewSelectedSlide;
  revisionQuotePageButton.hidden = !slide;
  if (slide) {
    revisionQuotePageButton.textContent = `引用第 ${slide} 页`;
  }
}

function updateRevisionPanel(job) {
  const eligible = Boolean(
    job
    && (job.job_type === "long_deck_v2" || job.job_type === "image_rebuild")
    && (job.status === "succeeded" || job.status === "failed_quality_gate")
  );
  revisionPanel.hidden = !eligible;
  if (!eligible) {
    if (revisionJobId !== null) resetRevisionPanel();
    revisionJobId = null;
    return;
  }
  updateRevisionQuoteButton();
  if (revisionJobId !== job.job_id) {
    revisionJobId = job.job_id;
    loadDeckRevisions(job.job_id);
  }
}

async function loadDeckRevisions(jobId) {
  try {
    const data = await requestJson(`/api/jobs/${jobId}/revisions`);
    if (revisionJobId !== jobId) return;
    resetRevisionPanel();
    data.items.forEach((item) => {
      appendRevisionMessage("user", item.message);
      if (item.status === "succeeded") {
        appendRevisionMessage("assistant", item.reply || "已完成修改并重新渲染。");
      } else if (item.status === "failed") {
        appendRevisionMessage("assistant", `修改失败：${item.error_message || "未知原因"}`);
      } else {
        appendRevisionMessage("assistant", "正在修改对应页面…", {pending: true});
      }
    });
    const running = data.items.find((item) => item.status === "running");
    if (running) {
      setRevisionBusy(true);
      scheduleRevisionPoll(jobId, running.revision_id);
    }
  } catch (error) {
    /* history is best-effort; composer still works */
  }
}

function scheduleRevisionPoll(jobId, revisionId, delay = 1500) {
  stopRevisionPolling();
  revisionPollTimer = setTimeout(async () => {
    revisionPollTimer = null;
    if (revisionJobId !== jobId) return;
    try {
      const revision = await requestJson(`/api/jobs/${jobId}/revisions/${revisionId}`);
      if (revision.status === "running") {
        scheduleRevisionPoll(jobId, revisionId);
        return;
      }
      const pending = revisionMessages.querySelector(".revision-message.is-pending");
      if (pending) pending.remove();
      if (revision.status === "succeeded") {
        appendRevisionMessage("assistant", revision.reply || "已完成修改并重新渲染。");
      } else {
        appendRevisionMessage("assistant", `修改失败：${revision.error_message || "未知原因"}`);
      }
      setRevisionBusy(false);
      await pollJob(jobId);
    } catch (error) {
      scheduleRevisionPoll(jobId, revisionId, 3000);
    }
  }, delay);
}

revisionComposer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (revisionBusy || !revisionJobId) return;
  const message = revisionInput.value.trim();
  if (!message) return;
  if (revisionAttachmentPicker.hasPendingUploads()) {
    appendRevisionMessage("assistant", "附件还在上传中，请稍候再发送。");
    return;
  }
  const attachmentIds = revisionAttachmentPicker.ids();
  const userMessage = appendRevisionMessage("user", message);
  appendAttachmentChipsTo(userMessage, revisionAttachmentPicker.summaries());
  const pending = appendRevisionMessage("assistant", "正在规划并修改对应页面…", {pending: true});
  revisionInput.value = "";
  setRevisionBusy(true);
  try {
    const revision = await requestJson(`/api/jobs/${revisionJobId}/revisions`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        message,
        page_numbers: livePreviewSelectedSlide ? [livePreviewSelectedSlide] : null,
        attachment_ids: attachmentIds
      })
    });
    revisionAttachmentPicker.clear();
    scheduleRevisionPoll(revisionJobId, revision.revision_id, 1200);
  } catch (error) {
    pending.remove();
    appendRevisionMessage("assistant", `修改请求失败：${error.message}`);
    setRevisionBusy(false);
  }
});

revisionQuotePageButton.addEventListener("click", () => {
  if (!livePreviewSelectedSlide) return;
  const prefix = `第 ${livePreviewSelectedSlide} 页：`;
  if (!revisionInput.value.startsWith(prefix)) {
    revisionInput.value = prefix + revisionInput.value;
  }
  revisionInput.focus();
});

liveSlideThumbnails.addEventListener("click", () => {
  setTimeout(updateRevisionQuoteButton, 0);
});

/* ---------- Image understanding & rebuild ---------- */

const rebuildDropzone = document.getElementById("rebuildDropzone");
const rebuildFileInput = document.getElementById("rebuildFileInput");
const rebuildCards = document.getElementById("rebuildCards");
const rebuildFooter = document.getElementById("rebuildFooter");
const rebuildCountChip = document.getElementById("rebuildCountChip");
const rebuildDeckTitle = document.getElementById("rebuildDeckTitle");
const startRebuildButton = document.getElementById("startRebuildButton");

const REBUILD_ROUTES = [
  {value: "rebuild", label: "重建为可编辑页面"},
  {value: "design_from_content", label: "根据图片内容设计一页"},
  {value: "embed_with_notes", label: "把图片放入 PPT 并补充解读"},
  {value: "style_reference", label: "参考图片的视觉风格"},
  {value: "extract_text", label: "只提取文字和数据"}
];

const REBUILD_CATEGORY_META = {
  slide: {label: "演示页面", tone: "success"},
  informative: {label: "含可用信息", tone: "info"},
  unrelated: {label: "与演示无关", tone: "warning"}
};

let rebuildItems = [];

function rebuildReadyItems() {
  return rebuildItems.filter((item) => item.upload_id && !item.error && item.route);
}

function renderRebuildCards() {
  rebuildCards.replaceChildren();
  rebuildItems.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "rebuild-card";

    const thumb = document.createElement("div");
    thumb.className = "rebuild-thumb";
    if (item.upload_id) {
      const img = document.createElement("img");
      img.src = `/api/uploads/${item.upload_id}`;
      img.alt = item.name;
      thumb.appendChild(img);
    }

    const body = document.createElement("div");
    body.className = "rebuild-card-body";

    const head = document.createElement("div");
    head.className = "rebuild-card-head";
    const name = document.createElement("strong");
    name.textContent = item.name;
    head.appendChild(name);
    if (item.analysis) {
      const meta = REBUILD_CATEGORY_META[item.analysis.category] || REBUILD_CATEGORY_META.informative;
      const badge = document.createElement("span");
      badge.className = "rebuild-badge";
      badge.dataset.tone = meta.tone;
      badge.textContent = meta.label;
      head.appendChild(badge);
    } else if (!item.error) {
      const badge = document.createElement("span");
      badge.className = "rebuild-badge";
      badge.dataset.tone = "pending";
      badge.textContent = item.upload_id ? "识别中…" : "上传中…";
      head.appendChild(badge);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "rebuild-remove";
    remove.textContent = "×";
    remove.title = "移除这张图片";
    remove.addEventListener("click", () => {
      rebuildItems.splice(index, 1);
      renderRebuildCards();
    });
    head.appendChild(remove);
    body.appendChild(head);

    const note = document.createElement("p");
    note.className = "rebuild-card-note";
    if (item.error) {
      note.textContent = item.error;
      card.classList.add("is-error");
    } else if (item.analysis) {
      note.textContent = item.analysis.category === "unrelated"
        ? "这张图片不像演示页面。你希望把它作为页面素材、根据它创作内容，还是仅参考视觉风格？不选择用途就不会生成。"
        : (item.analysis.reasoning || item.analysis.description || "");
    } else {
      note.textContent = "Agent 正在判断图片类型…";
    }
    body.appendChild(note);

    if (item.analysis && !item.error) {
      const select = document.createElement("select");
      select.className = "rebuild-route-select";
      const routes = item.analysis.category === "slide"
        ? REBUILD_ROUTES
        : REBUILD_ROUTES.filter((route) => route.value !== "rebuild");
      if (item.analysis.category === "unrelated") {
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "请选择用途（否则不生成）";
        select.appendChild(placeholder);
      }
      routes.forEach((route) => {
        const option = document.createElement("option");
        option.value = route.value;
        const recommended = (
          (route.value === "rebuild" && item.analysis.category === "slide")
          || (route.value === "design_from_content" && item.analysis.category === "informative")
        );
        option.textContent = `${route.label}${recommended ? "（推荐）" : ""}`;
        select.appendChild(option);
      });
      select.value = item.route || "";
      select.addEventListener("input", () => {
        item.route = select.value || null;
        updateRebuildFooter();
      });
      body.appendChild(select);
    }

    card.append(thumb, body);
    rebuildCards.appendChild(card);
  });
  updateRebuildFooter();
}

function updateRebuildFooter() {
  const total = rebuildItems.length;
  rebuildCountChip.textContent = `${total} 张图片`;
  rebuildFooter.hidden = total === 0;
  const ready = rebuildReadyItems().length;
  const pending = rebuildItems.some((item) => !item.error && (!item.upload_id || !item.analysis));
  startRebuildButton.disabled = ready === 0 || pending;
  startRebuildButton.textContent = pending
    ? "识别中…"
    : ready > 0
      ? `开始重建（${ready} 页）`
      : "开始重建";
}

async function addRebuildFile(file) {
  if (rebuildItems.length >= 10) return;
  if (!/\.(png|jpe?g|webp)$/i.test(file.name)) return;
  if (file.size > MAX_ATTACHMENT_BYTES) {
    rebuildItems.push({name: file.name, error: "超过 20MB 限制", upload_id: null, route: null});
    renderRebuildCards();
    return;
  }
  const item = {name: file.name, upload_id: null, analysis: null, route: null, error: null};
  rebuildItems.push(item);
  renderRebuildCards();
  try {
    const uploadResponse = await fetch(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      body: file
    });
    const uploaded = await uploadResponse.json();
    if (!uploadResponse.ok) throw new Error(uploaded.detail || "上传失败");
    item.upload_id = uploaded.upload_id;
    item.name = uploaded.name;
    renderRebuildCards();
    const analysis = await requestJson("/api/image-analyses", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({upload_id: item.upload_id})
    });
    item.analysis = analysis;
    item.route = analysis.suggested_route || null;
  } catch (error) {
    item.error = `处理失败：${error.message}`;
  }
  renderRebuildCards();
}

async function addRebuildFiles(fileList) {
  for (const file of Array.from(fileList || [])) {
    await addRebuildFile(normalizedAttachmentFile(file));
  }
}

rebuildDropzone.addEventListener("click", () => rebuildFileInput.click());
rebuildFileInput.addEventListener("change", async () => {
  const files = Array.from(rebuildFileInput.files || []);
  rebuildFileInput.value = "";
  await addRebuildFiles(files);
});
rebuildDropzone.addEventListener("dragover", (event) => {
  if (!event.dataTransfer?.types?.includes("Files")) return;
  event.preventDefault();
  rebuildDropzone.classList.add("is-dragover");
});
rebuildDropzone.addEventListener("dragleave", () => {
  rebuildDropzone.classList.remove("is-dragover");
});
rebuildDropzone.addEventListener("drop", async (event) => {
  event.preventDefault();
  rebuildDropzone.classList.remove("is-dragover");
  await addRebuildFiles(event.dataTransfer?.files);
});
document.addEventListener("paste", async (event) => {
  if (activeView !== "rebuild") return;
  const files = Array.from(event.clipboardData?.files || []);
  if (!files.length) return;
  event.preventDefault();
  await addRebuildFiles(files);
});

startRebuildButton.addEventListener("click", async () => {
  const items = rebuildReadyItems().map((item) => ({
    upload_id: item.upload_id,
    route: item.route
  }));
  if (!items.length) return;
  startRebuildButton.disabled = true;
  try {
    const job = await requestJson("/api/image-rebuild-jobs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        items,
        deck_title: rebuildDeckTitle.value.trim() || "图片重建"
      })
    });
    rebuildItems = [];
    renderRebuildCards();
    prepareJobWorkspace(items.length);
    await trackCreatedJob(job);
  } catch (error) {
    startRebuildButton.disabled = false;
    alert(`创建重建任务失败：${error.message}`);
  }
});

/* ---------- Pre-generation outline & script confirmation ---------- */

const planStatusChip = document.getElementById("planStatusChip");
const planEmpty = document.getElementById("planEmpty");
const planLoading = document.getElementById("planLoading");
const planError = document.getElementById("planError");
const planErrorMessage = document.getElementById("planErrorMessage");
const planEditor = document.getElementById("planEditor");
const planSections = document.getElementById("planSections");
const planDeckTitle = document.getElementById("planDeckTitle");
const planDeckSubtitle = document.getElementById("planDeckSubtitle");
const planPageSummary = document.getElementById("planPageSummary");
const confirmPlanButton = document.getElementById("confirmPlanButton");
const retryPlanButton = document.getElementById("retryPlanButton");
const skipPlanButton = document.getElementById("skipPlanButton");
const skipPlanFooterButton = document.getElementById("skipPlanFooterButton");
const skipOutlineButton = document.getElementById("skipOutlineButton");

const activePlanStorageKey = "ppt_agent_active_deck_plan_id";
const planLayoutHints = [
  "auto", "cards", "two_column", "stats", "timeline",
  "comparison", "quote", "chart", "table", "list"
];
let activePlanId = null;
let activePlan = null;
let planPollTimer = null;

function stopPlanPolling() {
  if (planPollTimer) {
    clearTimeout(planPollTimer);
    planPollTimer = null;
  }
}

function showPlanState(state) {
  planEmpty.hidden = state !== "empty";
  planLoading.hidden = state !== "loading";
  planError.hidden = state !== "error";
  planEditor.hidden = state !== "editor";
}

function planContentPages(plan) {
  return plan.sections.reduce((sum, section) => sum + section.pages.length, 0);
}

function planStructuralPages(plan) {
  // Mirrors the server: cover + closing + one divider per section (+ TOC once the deck reaches 10 pages).
  const sectionCount = plan.sections.length;
  let structural = 2 + sectionCount;
  const toc = Math.ceil(sectionCount / 8);
  if (structural + planContentPages(plan) + toc >= 10) {
    structural += toc;
  }
  return structural;
}

function planTotalPages(plan) {
  return planStructuralPages(plan) + planContentPages(plan);
}

function updatePlanSummary() {
  if (!activePlan) return;
  const content = planContentPages(activePlan);
  const structural = planStructuralPages(activePlan);
  const total = structural + content;
  const valid = total >= 4 && total <= 100;
  planPageSummary.textContent =
    `内容页 ${content} + 结构页 ${structural}（封面、目录、章节页、结尾）= 共 ${total} 页`
    + (valid ? "" : " · 总页数需在 4-100 页之间");
  confirmPlanButton.disabled = !valid;
  confirmPlanButton.textContent = valid
    ? `确认大纲，开始生成 ${total} 页 PPT`
    : "确认大纲，开始生成 PPT";
}

function planFieldLabel(text, control) {
  const label = document.createElement("label");
  label.className = "plan-field";
  label.append(text, control);
  return label;
}

function buildPlanPageCard(section, page, pageIndex) {
  const card = document.createElement("article");
  card.className = "plan-page";

  const head = document.createElement("div");
  head.className = "plan-page-head";
  const number = document.createElement("span");
  number.className = "plan-page-number";
  number.textContent = String(pageIndex + 1);
  const titleInput = document.createElement("input");
  titleInput.type = "text";
  titleInput.className = "plan-page-title";
  titleInput.value = page.title;
  titleInput.placeholder = "这一页的标题";
  titleInput.addEventListener("input", () => {
    page.title = titleInput.value;
  });
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "plan-remove-page";
  removeButton.textContent = "删除此页";
  removeButton.disabled = section.pages.length <= 1;
  removeButton.title = section.pages.length <= 1 ? "每章至少保留一页" : "从这一章删除这一页";
  removeButton.addEventListener("click", () => {
    section.pages.splice(pageIndex, 1);
    renderPlanEditor();
  });
  head.append(number, titleInput, removeButton);

  const points = document.createElement("textarea");
  points.rows = 3;
  points.value = (page.points || []).join("\n");
  points.placeholder = "每行一条页面要点";
  points.addEventListener("input", () => {
    page.points = points.value.split("\n").map((line) => line.trim()).filter(Boolean);
  });

  const notes = document.createElement("textarea");
  notes.rows = 3;
  notes.value = page.speaker_notes || "";
  notes.placeholder = "这一页的演讲备注（口播稿），会写进 PPTX 备注";
  notes.addEventListener("input", () => {
    page.speaker_notes = notes.value;
  });

  const metaRow = document.createElement("div");
  metaRow.className = "plan-page-meta";
  const layoutSelect = document.createElement("select");
  planLayoutHints.forEach((hint) => {
    const option = document.createElement("option");
    option.value = hint;
    option.textContent = hint;
    layoutSelect.appendChild(option);
  });
  layoutSelect.value = planLayoutHints.includes(page.layout_hint) ? page.layout_hint : "auto";
  layoutSelect.addEventListener("input", () => {
    page.layout_hint = layoutSelect.value;
  });
  const dataIdea = document.createElement("input");
  dataIdea.type = "text";
  dataIdea.value = page.data_idea || "";
  dataIdea.placeholder = "图表/数据建议，可留空";
  dataIdea.addEventListener("input", () => {
    page.data_idea = dataIdea.value.trim() || null;
  });
  metaRow.append(
    planFieldLabel("版式", layoutSelect),
    planFieldLabel("图表建议", dataIdea)
  );

  card.append(
    head,
    planFieldLabel("页面要点", points),
    planFieldLabel("演讲备注", notes),
    metaRow
  );
  return card;
}

function buildPlanSectionCard(section, sectionIndex) {
  const card = document.createElement("section");
  card.className = "plan-section";

  const head = document.createElement("div");
  head.className = "plan-section-head";
  const badge = document.createElement("span");
  badge.className = "plan-section-badge";
  badge.textContent = `第 ${sectionIndex + 1} 章`;
  const titleInput = document.createElement("input");
  titleInput.type = "text";
  titleInput.className = "plan-section-title";
  titleInput.value = section.title;
  titleInput.placeholder = "章节标题";
  titleInput.addEventListener("input", () => {
    section.title = titleInput.value;
  });
  const pageCount = document.createElement("span");
  pageCount.className = "plan-section-count";
  pageCount.textContent = `${section.pages.length} 页内容`;
  head.append(badge, titleInput, pageCount);

  const goalInput = document.createElement("input");
  goalInput.type = "text";
  goalInput.value = section.goal || "";
  goalInput.placeholder = "这一章要讲清楚 / 说服什么";
  goalInput.addEventListener("input", () => {
    section.goal = goalInput.value;
  });

  const pages = document.createElement("div");
  pages.className = "plan-pages";
  section.pages.forEach((page, pageIndex) => {
    pages.appendChild(buildPlanPageCard(section, page, pageIndex));
  });

  const addButton = document.createElement("button");
  addButton.type = "button";
  addButton.className = "plan-add-page";
  addButton.textContent = "+ 在这一章添加一页";
  addButton.addEventListener("click", () => {
    section.pages.push({
      title: `${section.title || "新章节"} · 补充页`,
      summary: "",
      points: [],
      layout_hint: "auto",
      data_idea: null,
      speaker_notes: ""
    });
    renderPlanEditor();
  });

  card.append(head, planFieldLabel("章节目标", goalInput), pages, addButton);
  return card;
}

function renderPlanEditor() {
  if (!activePlan) return;
  planDeckTitle.value = activePlan.deck_title || "";
  planDeckSubtitle.value = activePlan.subtitle || "";
  planSections.replaceChildren();
  activePlan.sections.forEach((section, sectionIndex) => {
    planSections.appendChild(buildPlanSectionCard(section, sectionIndex));
  });
  updatePlanSummary();
  showPlanState("editor");
  planStatusChip.textContent = "待确认";
}

function showPlanFailure(message) {
  stopPlanPolling();
  planErrorMessage.textContent = `大纲规划失败：${message}`;
  showPlanState("error");
  planStatusChip.textContent = "规划失败";
}

function schedulePlanPoll(delay = 1500) {
  stopPlanPolling();
  planPollTimer = setTimeout(async () => {
    planPollTimer = null;
    if (!activePlanId) return;
    try {
      const plan = await requestJson(`/api/deck-plans/${activePlanId}`);
      applyPlanState(plan);
    } catch (error) {
      showPlanFailure(error.message);
    }
  }, delay);
}

function applyPlanState(plan) {
  if (plan.status === "planning") {
    showPlanState("loading");
    planStatusChip.textContent = "规划中";
    schedulePlanPoll();
    return;
  }
  if (plan.status === "ready") {
    activePlan = plan.plan;
    renderPlanEditor();
    return;
  }
  if (plan.status === "failed") {
    showPlanFailure(plan.error_message || "未知原因");
    return;
  }
  localStorage.removeItem(activePlanStorageKey);
  showPlanState("empty");
}

async function startDeckPlan() {
  stopPlanPolling();
  saveLongDeckDraft();
  activePlanId = null;
  activePlan = null;
  setAppView("outline");
  showPlanState("loading");
  planStatusChip.textContent = "规划中";
  try {
    const plan = await requestJson("/api/deck-plans", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(buildLongDeckPayload())
    });
    activePlanId = plan.plan_id;
    localStorage.setItem(activePlanStorageKey, plan.plan_id);
    schedulePlanPoll(800);
  } catch (error) {
    showPlanFailure(error.message);
  }
}

async function confirmDeckPlan() {
  if (!activePlanId || !activePlan) return;
  confirmPlanButton.disabled = true;
  planStatusChip.textContent = "正在启动生成";
  const total = planTotalPages(activePlan);
  try {
    const job = await requestJson(`/api/deck-plans/${activePlanId}/confirm`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({plan: activePlan})
    });
    localStorage.removeItem(activePlanStorageKey);
    longSlideCount.value = String(total);
    showPlanState("empty");
    planStatusChip.textContent = "已确认";
    prepareJobWorkspace(total);
    await trackCreatedJob(job);
  } catch (error) {
    planStatusChip.textContent = "待确认";
    updatePlanSummary();
    planPageSummary.textContent = `启动生成失败：${error.message}`;
  }
}

async function restoreActiveDeckPlan() {
  const remembered = localStorage.getItem(activePlanStorageKey);
  if (!remembered) return;
  try {
    const plan = await requestJson(`/api/deck-plans/${remembered}`);
    if (plan.status === "planning" || plan.status === "ready") {
      activePlanId = remembered;
      applyPlanState(plan);
    } else {
      localStorage.removeItem(activePlanStorageKey);
    }
  } catch (error) {
    localStorage.removeItem(activePlanStorageKey);
  }
}

function skipPlanAndGenerateDirectly() {
  stopPlanPolling();
  localStorage.removeItem(activePlanStorageKey);
  activePlanId = null;
  activePlan = null;
  longDeckForm.requestSubmit();
}

confirmPlanButton.addEventListener("click", () => {
  confirmDeckPlan();
});

retryPlanButton.addEventListener("click", () => {
  startDeckPlan();
});

skipPlanButton.addEventListener("click", skipPlanAndGenerateDirectly);
skipPlanFooterButton.addEventListener("click", skipPlanAndGenerateDirectly);
skipOutlineButton.addEventListener("click", skipPlanAndGenerateDirectly);

planDeckTitle.addEventListener("input", () => {
  if (activePlan) activePlan.deck_title = planDeckTitle.value;
});

planDeckSubtitle.addEventListener("input", () => {
  if (activePlan) activePlan.subtitle = planDeckSubtitle.value.trim() || null;
});

longDeckForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveLongDeckDraft();
  const pageCount = Number(longSlideCount.value);
  if (pageCount <= 3) {
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
  const pageCount = Number(longSlideCount.value);
  if (pageCount <= 3) {
    longDeckForm.requestSubmit();
    return;
  }
  startDeckPlan();
});

continueInterviewButton.addEventListener("click", () => {
  generationConfirmation.hidden = true;
  interviewComposer.hidden = false;
  generationConfirmation.after(interviewComposer);
  interviewInput.placeholder = "直接告诉 Agent 你想修改什么，例如：改成 15 页，面向小学生。";
  interviewHint.textContent = "继续用自然语言调整，Agent 会更新理解并再次准备生成。";
  interviewInput.focus();
});

newInterviewButton.addEventListener("click", () => {
  resetPresentationInterview();
  setAppView("create");
  interviewInput.focus();
});

followLatestSlideButton.addEventListener("click", () => {
  if (!activeJobId || !livePreviewManifest || !livePreviewLatestSlide) return;
  selectLiveSlide(activeJobId, livePreviewLatestSlide, livePreviewManifest, {followLatest: true});
});

[primaryDownloadTop, primaryDownloadLink, deliveryDownload].forEach((link) => {
  link.addEventListener("click", (event) => {
    if (link.getAttribute("href")?.startsWith("#")) {
      event.preventDefault();
      setAppView("delivery");
    }
  });
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

document.querySelectorAll("[data-view-target]").forEach((control) => {
  control.addEventListener("click", () => {
    setAppView(control.dataset.viewTarget);
  });
});

document.getElementById("long_topic").addEventListener("input", () => {
  updateViewHeader();
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
  setAppView("create");
  resetLiveSlideWorkspace(Number(longSlideCount.value) || 0);
  setInterval(renderElapsedClock, 250);
  loadPresentationHistory();
  restorePresentationInterview();
  restoreActiveDeckPlan();
  restoreLastLongDeckJob().catch((error) => {
    errorMessage.textContent = error.message;
  });
});
