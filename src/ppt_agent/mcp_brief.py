"""Requirement brief for MCP clarification: pure functions, no I/O beyond reading sources.

An Agent talking to the user fills this brief field by field. Every value
records where it came from, so the deck is only generated once the fields
that change the result are known (said by the user, read from a source, or
explicitly left to the Agent), and nothing missing is silently guessed.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from math import ceil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ppt_agent.deck_jobs import CreateLongDeckJobRequest
from ppt_agent.models import StrictModel
from ppt_agent.requirements_interview import PresentationBriefDraft

Origin = Literal["user", "source", "delegated", "default", "missing"]
UpdateOrigin = Literal["user", "source", "delegated"]
SourcePolicy = Literal["materials_only", "materials_and_web", "web_only", "none"]

# Priority order: the first missing field is the next question.
REQUIRED_FIELDS = ("purpose", "audience", "topic", "slide_count", "source_policy")
# Only facts a document can state; who the deck is for and why stay the user's call.
SOURCE_FIELDS = frozenset({"topic", "content_focus"})
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".md", ".txt"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MIN_SLIDES, MAX_SLIDES = 4, 100  # the v2 pipeline behind the MCP tools
SLIDES_PER_SOURCE_PAGE = 3
STRUCTURAL_SLIDES = 3  # cover, agenda and closing need no material
CHARS_PER_SOURCE_PAGE = 1500  # so one long Markdown section is not read as one page

FIELD_LABELS = {
    "topic": "主题", "audience": "受众", "slide_count": "页数", "language": "语言",
    "purpose": "目的", "tone": "语气", "visual_direction": "视觉方向",
    "content_focus": "重点内容", "constraints": "限制条件", "user_requirements": "补充说明",
    "style_profile": "风格", "source_policy": "资料使用方式", "sources": "资料",
}
POLICY_LABELS = {
    "materials_only": "只使用提供的资料，不联网",
    "materials_and_web": "以提供的资料为主，联网补充",
    "web_only": "联网检索资料",
    "none": "不使用外部资料，不写具体统计数字",
}
QUESTION_REASONS = {
    "purpose": "目的决定内容取舍和结论怎么写，是最影响成品的一项",
    "audience": "受众决定术语深浅、信息密度和举例方式",
    "topic": "需要明确这份演示讲什么",
    "slide_count": "页数决定内容展开的深度和章节数量",
    "source_policy": "决定页面上的数字和出处从哪里来",
}


class BriefValues(PresentationBriefDraft):
    style_profile: Literal["consulting", "launch", "training", "corporate"] | None = None
    source_policy: SourcePolicy | None = None


FIELDS = tuple(BriefValues.model_fields)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


class ClarifyBrief(StrictModel):
    values: BriefValues = Field(default_factory=BriefValues)
    origins: dict[str, Origin] = Field(
        default_factory=lambda: {name: "default" if name == "language" else "missing" for name in FIELDS})

    def origin(self, name: str) -> Origin:
        return self.origins.get(name, "missing")


class SkippedUpdate(StrictModel):
    field: str
    origin: UpdateOrigin
    reason: str


class UpdateResult(StrictModel):
    brief: ClarifyBrief
    skipped: list[SkippedUpdate] = Field(default_factory=list)


class SourceSummary(StrictModel):
    name: str
    path: str
    kind: Literal["document", "image"] = "document"  # images are page assets, not text evidence
    readable: bool
    sha256: str | None = None
    pages: int = 0
    page_kind: str | None = None  # "PDF" = physical pages; "logical" = heading sections
    characters: int = 0
    chapters: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Conflict(StrictModel):
    field: str
    message: str


class DefaultUsed(StrictModel):
    field: str
    value: str
    note: str


class NextQuestion(StrictModel):
    field: str
    kind: Literal["missing", "conflict"]
    reason: str


class BriefEvaluation(StrictModel):
    status: Literal["clarifying", "ready"]
    missing: list[str]
    conflicts: list[Conflict]
    defaults_used: list[DefaultUsed]
    next_question: NextQuestion | None
    brief_hash: str


def apply_updates(brief: ClarifyBrief, updates: Mapping[str, Mapping[str, Any]]) -> UpdateResult:
    """Merge ``{field: {"value": ..., "origin": ...}}``.

    Malformed updates raise before anything changes. A source value aimed at a
    field the user already answered is skipped and reported, never applied.
    """
    data = brief.values.model_dump()
    origins = dict(brief.origins)
    skipped = []
    for name, update in updates.items():
        if name not in FIELDS:
            raise ValueError(f"未知字段：{name}")
        origin, value = update.get("origin"), update.get("value")
        if origin not in ("user", "source", "delegated"):
            raise ValueError(f"{name} 的来源只能是 user、source 或 delegated，收到：{origin!r}")
        if origin == "source" and name not in SOURCE_FIELDS:
            raise ValueError(f"{name} 不能从资料中推断，只有主题和重点内容可以标记为 source")
        if _is_empty(value):
            raise ValueError(f"{name} 的值不能为空")
        if name in ("content_focus", "constraints") and isinstance(value, str):
            value = [value]
        if origin == "source" and origins.get(name) == "user":
            skipped.append(SkippedUpdate(field=name, origin=origin, reason="用户已明确给出，资料推断不覆盖"))
            continue
        data[name] = value
        origins[name] = origin
    values = BriefValues.model_validate(data)  # field validation is all-or-nothing
    return UpdateResult(brief=ClarifyBrief(values=values, origins=origins), skipped=skipped)


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def summarize_sources(paths: Iterable[str | Path]) -> list[SourceSummary]:
    """Pages, chapter titles and parse warnings per file; unreadable files say so.

    Images are listed as picture assets: usable on slides, never counted as
    pages of evidence and never parsed as documents.
    """
    from ppt_agent.v2.evidence import EvidenceStore

    paths = [Path(path) for path in paths]
    documents = [path for path in paths if path.suffix.lower() in DOCUMENT_SUFFIXES]
    store = EvidenceStore.from_paths(documents)
    by_path = {doc["path"]: doc for doc in store.documents}
    summaries = []
    for path in paths:
        resolved = str(path.resolve())
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            digest = _file_sha256(path) if path.is_file() else None
            summaries.append(SourceSummary(
                name=path.name, path=resolved, kind="image", readable=digest is not None, sha256=digest,
                warnings=[] if digest else ["未能读取该图片"]))
            continue
        if suffix not in DOCUMENT_SUFFIXES:
            summaries.append(SourceSummary(name=path.name, path=resolved, readable=False,
                                           warnings=[f"不支持的文件类型：{path.suffix or '无扩展名'}"]))
            continue
        doc = by_path.pop(resolved, None)  # a second copy of the same file was excluded
        warnings = [w for w in store.warnings if w.endswith(path.name) or f" {path.name}:" in w]
        if doc is None:
            summaries.append(SourceSummary(name=path.name, path=resolved, readable=False,
                                           warnings=warnings or ["未能读取该文件"]))
            continue
        characters = sum(len(page["text"].strip()) for page in doc["pages"])
        if characters == 0:
            warnings.append("没有提取到文字（可能是扫描件或图片 PDF），不能当作已读")
        summaries.append(SourceSummary(
            name=doc["name"], path=resolved, readable=characters > 0, sha256=doc["sha256"],
            pages=len(doc["pages"]), page_kind=doc["page_kind"], characters=characters,
            chapters=[c["title"] for c in store.chapters if c["doc_id"] == doc["doc_id"]], warnings=warnings))
    return summaries


def brief_hash(brief: ClarifyBrief, sources: Iterable[SourceSummary] = ()) -> str:
    """Fingerprint of what would be generated: the brief and the exact files.

    A confirmation must match it, so swapping a report or picture after the
    user agreed invalidates the agreement.
    """
    files = sorted(source.sha256 for source in sources if source.readable and source.sha256)
    payload = json.dumps({"brief": brief.values.model_dump(mode="json"), "files": files},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _conflicts(values: BriefValues, sources: list[SourceSummary]) -> list[Conflict]:
    conflicts = [Conflict(field="sources", message=f"「{s.name}」未能读取：{'；'.join(s.warnings)}")
                 for s in sources if not s.readable]
    readable = [s for s in sources if s.readable and s.kind == "document"]
    if values.slide_count is not None and not MIN_SLIDES <= values.slide_count <= MAX_SLIDES:
        conflicts.append(Conflict(field="slide_count",
                                  message=f"生成需要 {MIN_SLIDES}–{MAX_SLIDES} 页，当前是 {values.slide_count} 页"))
    policy = values.source_policy
    if policy in ("materials_only", "materials_and_web") and not readable:
        conflicts.append(Conflict(field="source_policy",
                                  message=f"选择了「{POLICY_LABELS[policy]}」，但没有可读取的资料"))
    if policy == "materials_only" and readable and values.slide_count:
        pages = sum(max(s.pages, ceil(s.characters / CHARS_PER_SOURCE_PAGE)) for s in readable)
        supported = pages * SLIDES_PER_SOURCE_PAGE + STRUCTURAL_SLIDES
        if values.slide_count > supported:
            conflicts.append(Conflict(
                field="slide_count",
                message=(f"资料约 {pages} 页，只用资料撑不起 {values.slide_count} 页；"
                         f"可以减到 {supported} 页以内、补充资料，或开启联网补充")))
    return conflicts


def evaluate(brief: ClarifyBrief, source_summary: list[SourceSummary] | None = None) -> BriefEvaluation:
    """Ready only when every required field is settled and nothing contradicts."""
    values = brief.values
    missing = [name for name in REQUIRED_FIELDS if brief.origin(name) == "missing" or _is_empty(getattr(values, name))]
    sources = list(source_summary or [])
    conflicts = _conflicts(values, sources)
    defaults = []
    if brief.origin("language") == "default":
        defaults.append(DefaultUsed(field="language", value=values.language, note="未指定语言，默认中文"))
    if values.style_profile is None:
        defaults.append(DefaultUsed(field="style_profile", value="auto", note="未指定风格，按演示类型自动选择"))
    if missing:
        next_question = NextQuestion(field=missing[0], kind="missing", reason=QUESTION_REASONS[missing[0]])
    elif conflicts:
        next_question = NextQuestion(field=conflicts[0].field, kind="conflict", reason=conflicts[0].message)
    else:
        next_question = None
    return BriefEvaluation(
        status="clarifying" if missing or conflicts else "ready", missing=missing,
        conflicts=conflicts, defaults_used=defaults, next_question=next_question,
        brief_hash=brief_hash(brief, sources))


def _requirements_text(brief: ClarifyBrief) -> str:
    values = brief.values
    parts = [f"目的：{values.purpose}。"]
    if values.content_focus:
        parts.append(f"重点内容：{'、'.join(values.content_focus)}。")
    if values.constraints:
        parts.append(f"限制条件：{'；'.join(values.constraints)}。")
    if values.tone:
        parts.append(f"语气：{values.tone}。")
    if values.visual_direction:
        parts.append(f"视觉方向：{values.visual_direction}。")
    parts.append(f"资料使用：{POLICY_LABELS[values.source_policy]}。")
    if values.user_requirements:
        parts.append(f"补充说明：{values.user_requirements}")
    delegated = []
    for name in FIELDS:
        if brief.origin(name) != "delegated":
            continue
        value = getattr(values, name)
        shown = "、".join(value) if isinstance(value, list) else POLICY_LABELS.get(value, value)
        delegated.append(f"{FIELD_LABELS.get(name, name)}={shown}")
    if delegated:
        parts.append(f"以下各项由 Agent 代为决定：{'；'.join(delegated)}。")
    return "".join(parts)


def to_job_request(brief: ClarifyBrief, *, attachment_ids: Iterable[str] = ()) -> CreateLongDeckJobRequest:
    """The generation request a settled brief stands for."""
    missing = [name for name in REQUIRED_FIELDS if _is_empty(getattr(brief.values, name))]
    if missing:
        raise ValueError("需求单还缺：" + "、".join(FIELD_LABELS[name] for name in missing))
    values = brief.values
    return CreateLongDeckJobRequest(
        topic=values.topic, audience=values.audience, slide_count=values.slide_count,
        language=values.language, deck_type="visual_design_v2", layout_engine="typeset",
        style_profile=values.style_profile, user_requirements=_requirements_text(brief),
        enable_search=values.source_policy in ("materials_and_web", "web_only"),
        attachment_ids=list(attachment_ids))


def fields_view(brief: ClarifyBrief) -> dict[str, dict[str, Any]]:
    """Every field with its value and where it came from."""
    return {name: {"value": getattr(brief.values, name), "origin": brief.origin(name)} for name in FIELDS}


_ORIGIN_NOTES = {"source": "资料", "delegated": "Agent 代定", "default": "默认"}


def brief_summary(brief: ClarifyBrief, sources: Iterable[SourceSummary] = ()) -> str:
    """Short Chinese summary to show the user before generating."""
    values = brief.values

    def show(name: str, text: Any) -> str | None:
        if _is_empty(text):
            return None
        note = _ORIGIN_NOTES.get(brief.origin(name))
        return f"{FIELD_LABELS[name]}：{text}" + (f"（{note}）" if note else "")

    sources = list(sources)
    documents = [s.name for s in sources if s.kind == "document" and s.readable]
    images = [s.name for s in sources if s.kind == "image" and s.readable]
    parts = [
        show("topic", values.topic),
        show("audience", values.audience),
        show("purpose", values.purpose),
        show("slide_count", f"{values.slide_count} 页" if values.slide_count else None),
        show("source_policy", POLICY_LABELS.get(values.source_policy) if values.source_policy else None),
        f"资料：{'、'.join(documents)}" if documents else None,
        f"图片素材：{'、'.join(images)}" if images else None,
        show("content_focus", "、".join(values.content_focus)),
        show("constraints", "；".join(values.constraints)),
        show("style_profile", values.style_profile),
    ]
    return "；".join(part for part in parts if part) + "。" if any(parts) else "还没有确定任何需求。"
