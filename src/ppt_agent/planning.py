"""Deck-level planning primitives for generation prompts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Self

from pydantic import Field, model_validator

from ppt_agent.design import DesignSpec, SlideRole, get_layout_contract, list_layout_contracts
from ppt_agent.models import StrictModel
from ppt_agent.runtime import StageObserver, invoke_with_timeout, observed_stage


SLIDE_ROLES: tuple[str, ...] = (
    "cover",
    "context",
    "comparison",
    "framework",
    "process",
    "metrics",
    "risk",
    "summary",
)
PlanSource = Literal["llm", "deterministic", "fallback", "provided", "none"]
EARLY_STAGE_MARKERS = (
    "背景",
    "价值",
    "为什么",
    "问题定义",
    "问题意识",
    "场景",
    "痛点",
    "why",
    "context",
    "background",
    "value",
    "problem framing",
)
CONCLUSION_MARKERS = (
    "核心结论",
    "结论",
    "总结",
    "下一步",
    "行动",
    "建议",
    "收束",
    "落地",
    "闭环",
    "conclusion",
    "closing",
    "next step",
    "action",
)
NARRATIVE_ROLE_PRIORITY: dict[SlideRole, int] = {
    "cover": 0,
    "context": 10,
    "comparison": 20,
    "framework": 30,
    "process": 50,
    "metrics": 60,
    "risk": 70,
    "summary": 80,
}


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(marker.lower() in normalized for marker in markers)


def _slide_plan_text(slide: "SlidePlan") -> str:
    return f"{slide.key_message} {slide.content_goal} {slide.recommended_layout}"


def _is_early_stage_slide(slide: "SlidePlan") -> bool:
    return slide.slide_role == "context" or _contains_any_marker(_slide_plan_text(slide), EARLY_STAGE_MARKERS)


def _is_conclusion_slide(slide: "SlidePlan") -> bool:
    if slide.recommended_layout == "closing_slide":
        return True
    return slide.slide_role == "summary" and _contains_any_marker(_slide_plan_text(slide), CONCLUSION_MARKERS)


class SlidePlan(StrictModel):
    slide_index: int = Field(..., ge=1)
    slide_role: SlideRole
    key_message: str = Field(..., min_length=1)
    content_goal: str = Field(..., min_length=1)
    recommended_layout: str = Field(..., min_length=1)
    content_items: int | None = Field(default=None, ge=0)
    must_not_repeat: list[str] = Field(default_factory=list)


class DeckPlan(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=1, le=10)
    slides: list[SlidePlan] = Field(..., min_length=1)
    plan_source: PlanSource = "llm"

    @model_validator(mode="after")
    def validate_slide_plan_relationships(self) -> Self:
        actual_count = len(self.slides)
        if actual_count != self.slide_count:
            raise ValueError(
                f"DeckPlan has {actual_count} slides, but slide_count is {self.slide_count}."
            )

        expected_indexes = list(range(1, self.slide_count + 1))
        actual_indexes = [slide.slide_index for slide in self.slides]
        if actual_indexes != expected_indexes:
            raise ValueError(
                "DeckPlan slide_index values must be consecutive from 1 to slide_count; "
                f"got {actual_indexes}."
            )

        for slide in self.slides:
            try:
                contract = get_layout_contract(slide.recommended_layout)
            except ValueError as exc:
                raise ValueError(
                    f"Slide {slide.slide_index} uses unsupported recommended_layout "
                    f"'{slide.recommended_layout}'. {exc}"
                ) from exc

            if slide.content_items is None:
                continue

            if not contract.min_items <= slide.content_items <= contract.max_items:
                raise ValueError(
                    f"Slide {slide.slide_index} content_items={slide.content_items} "
                    f"does not fit layout '{contract.layout_name}' capacity "
                    f"{contract.min_items}-{contract.max_items}."
                )

        closing_positions = [
            index
            for index, slide in enumerate(self.slides)
            if slide.recommended_layout == "closing_slide"
        ]
        if closing_positions and closing_positions != [self.slide_count - 1]:
            raise ValueError("DeckPlan closing_slide must be the final slide when present.")

        conclusion_positions = [
            index
            for index, slide in enumerate(self.slides)
            if _is_conclusion_slide(slide)
        ]
        if conclusion_positions:
            first_conclusion = conclusion_positions[0]
            late_early_slides = [
                slide
                for index, slide in enumerate(self.slides)
                if index > first_conclusion and _is_early_stage_slide(slide)
            ]
            if late_early_slides:
                slide_indexes = [slide.slide_index for slide in late_early_slides]
                raise ValueError(
                    "DeckPlan places background/context/value slides after conclusion or closing; "
                    f"late slide_index values: {slide_indexes}."
                )

        if self.slide_count >= 6:
            for index, slide in enumerate(self.slides):
                if not _is_conclusion_slide(slide):
                    continue
                late_metrics_or_risk = [
                    later.slide_index
                    for later in self.slides[index + 1 :]
                    if later.slide_role in {"metrics", "risk"}
                ]
                if late_metrics_or_risk:
                    raise ValueError(
                        "DeckPlan places conclusion before metrics/risk; "
                        f"late metrics/risk slide_index values: {late_metrics_or_risk}."
                    )

        return self


class SectionPlan(StrictModel):
    section_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    purpose: str = Field(..., min_length=1)
    start_slide: int = Field(..., ge=1)
    end_slide: int = Field(..., ge=1)
    target_slide_count: int = Field(..., ge=1)
    key_questions: list[str] = Field(..., min_length=1)
    key_messages: list[str] = Field(..., min_length=1)
    preferred_layouts: list[str] = Field(..., min_length=1)
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_section_range(self) -> Self:
        if self.end_slide < self.start_slide:
            raise ValueError(
                f"Section '{self.section_id}' end_slide must be >= start_slide; "
                f"got {self.start_slide}-{self.end_slide}."
            )
        for layout_name in self.preferred_layouts:
            try:
                get_layout_contract(layout_name)
            except ValueError as exc:
                raise ValueError(
                    f"Section '{self.section_id}' uses unsupported preferred_layout "
                    f"'{layout_name}'. {exc}"
                ) from exc
        actual_count = self.end_slide - self.start_slide + 1
        if actual_count != self.target_slide_count:
            raise ValueError(
                f"Section '{self.section_id}' target_slide_count={self.target_slide_count} "
                f"does not match range size {actual_count}."
            )
        return self


class BatchPlan(StrictModel):
    batch_id: str = Field(..., min_length=1)
    start_slide: int = Field(..., ge=1)
    end_slide: int = Field(..., ge=1)
    section_ids: list[str] = Field(..., min_length=1)
    batch_goal: str = Field(..., min_length=1)
    context_summary: str = Field(..., min_length=1)
    must_include: list[str] = Field(default_factory=list)
    must_not_repeat: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_batch_range(self) -> Self:
        if self.end_slide < self.start_slide:
            raise ValueError(
                f"Batch '{self.batch_id}' end_slide must be >= start_slide; "
                f"got {self.start_slide}-{self.end_slide}."
            )
        return self


class LongDeckPlan(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=21, le=100)
    language: str = Field(..., min_length=1)
    deck_type: str = Field(..., min_length=1)
    sections: list[SectionPlan] = Field(..., min_length=1)
    batches: list[BatchPlan] = Field(..., min_length=1)
    narrative_summary: str = Field(..., min_length=1)
    global_style_notes: list[str] = Field(default_factory=list)
    content_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_long_deck_relationships(self) -> Self:
        errors: list[str] = []

        duplicate_section_ids = _duplicate_values(section.section_id for section in self.sections)
        if duplicate_section_ids:
            errors.append(
                "LongDeckPlan section_id values must be unique; "
                f"duplicates: {', '.join(sorted(duplicate_section_ids))}."
            )

        duplicate_batch_ids = _duplicate_values(batch.batch_id for batch in self.batches)
        if duplicate_batch_ids:
            errors.append(
                "LongDeckPlan batch_id values must be unique; "
                f"duplicates: {', '.join(sorted(duplicate_batch_ids))}."
            )

        errors.extend(_validate_range_coverage(self.sections, self.slide_count, label="SectionPlan"))
        errors.extend(_validate_range_coverage(self.batches, self.slide_count, label="BatchPlan"))

        known_section_ids = {section.section_id for section in self.sections}
        for batch in self.batches:
            unknown_ids = [section_id for section_id in batch.section_ids if section_id not in known_section_ids]
            if unknown_ids:
                errors.append(
                    f"Batch '{batch.batch_id}' references unknown section_ids: {unknown_ids}."
                )
                continue

            overlapping_section_ids = [
                section.section_id
                for section in self.sections
                if section.start_slide <= batch.end_slide and batch.start_slide <= section.end_slide
            ]
            if batch.section_ids != overlapping_section_ids:
                errors.append(
                    f"Batch '{batch.batch_id}' section_ids must match the overlapping sections "
                    f"{overlapping_section_ids}; got {batch.section_ids}."
                )

        conclusion_positions = [
            index for index, section in enumerate(self.sections) if _is_conclusion_section(section)
        ]
        if conclusion_positions and conclusion_positions[-1] != len(self.sections) - 1:
            errors.append("LongDeckPlan conclusion / action section must be the final section.")

        if conclusion_positions:
            first_conclusion = conclusion_positions[0]
            late_context_sections = [
                section.section_id
                for section in self.sections[first_conclusion + 1 :]
                if _is_context_section(section)
            ]
            if late_context_sections:
                errors.append(
                    "LongDeckPlan places context/background sections after conclusion; "
                    f"late section_ids: {late_context_sections}."
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self


class LongDeckPlanningRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=21, le=100)
    language: str = Field(default="zh-CN", min_length=1)
    deck_type: str | None = Field(default=None, min_length=1)
    purpose: str = ""
    tone: str = ""
    visual_style: str = ""
    content_focus: str = ""
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    user_requirements_raw: str | None = Field(default=None, min_length=1)


class BatchContext(StrictModel):
    batch_id: str = Field(..., min_length=1)
    start_slide: int = Field(..., ge=1)
    end_slide: int = Field(..., ge=1)
    section_ids: list[str] = Field(..., min_length=1)
    batch_goal: str = Field(..., min_length=1)
    context_summary: str = Field(..., min_length=1)
    must_include: list[str] = Field(default_factory=list)
    must_not_repeat: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(..., min_length=1)
    sections: list[SectionPlan] = Field(..., min_length=1)
    previous_section_summary: str | None = None
    next_section_summary: str | None = None


DECK_PLAN_STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "title": "DeckPlan",
    "description": "Deck-level plan generated before Slide IR. Extra provider fields are normalized locally.",
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "audience": {"type": "string"},
        "slide_count": {"type": "integer"},
        "plan_source": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slide_index": {"type": "integer"},
                    "slide_number": {"type": "integer"},
                    "slide_role": {"type": "string"},
                    "key_message": {"type": "string"},
                    "content_goal": {"type": "string"},
                    "recommended_layout": {"type": "string"},
                    "content_items": {"type": "integer"},
                    "must_not_repeat": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "deck_plan": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": True,
}


def _normalize_deck_plan_payload(response: Any, brief: Any) -> Any:
    if isinstance(response, DeckPlan):
        return response
    if not isinstance(response, dict):
        return response

    allowed_plan_fields = set(DeckPlan.model_fields)
    payload = {
        field_name: field_value
        for field_name, field_value in response.items()
        if field_name in allowed_plan_fields
    }
    payload.setdefault("topic", _brief_value(brief, "topic"))
    payload.setdefault("audience", _brief_value(brief, "audience"))
    payload.setdefault("slide_count", _brief_value(brief, "slide_count"))

    slides = payload.get("slides")
    if isinstance(slides, list):
        allowed_slide_fields = set(SlidePlan.model_fields)
        normalized_slides: list[Any] = []
        for index, slide in enumerate(slides, start=1):
            if not isinstance(slide, dict):
                normalized_slides.append(slide)
                continue

            normalized_slide = {
                field_name: field_value
                for field_name, field_value in slide.items()
                if field_name in allowed_slide_fields
            }
            if "slide_index" not in normalized_slide:
                normalized_slide["slide_index"] = slide.get("slide_number", index)
            normalized_slides.append(normalized_slide)

        payload["slides"] = normalized_slides

    return payload


def _brief_value(brief: Any, field_name: str, fallback: str = "") -> Any:
    return getattr(brief, field_name, fallback)


def _duplicate_values(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _validate_range_coverage(items: list[Any], slide_count: int, *, label: str) -> list[str]:
    errors: list[str] = []
    if not items:
        return [f"{label} must not be empty."]

    sorted_items = sorted(items, key=lambda item: item.start_slide)
    expected_start = 1
    for item in sorted_items:
        if item.start_slide < 1 or item.end_slide > slide_count:
            errors.append(
                f"{label} range {item.start_slide}-{item.end_slide} must stay within 1-{slide_count}."
            )
        if item.start_slide != expected_start:
            errors.append(
                f"{label} ranges must be continuous without gaps or overlap; expected slide {expected_start}, "
                f"got {item.start_slide}."
            )
        expected_start = item.end_slide + 1

    if expected_start != slide_count + 1:
        errors.append(
            f"{label} ranges must cover through slide {slide_count}; coverage stopped at {expected_start - 1}."
        )

    return errors


def _section_text(section: SectionPlan) -> str:
    return f"{section.title} {section.purpose}".lower()


def _is_conclusion_section(section: SectionPlan) -> bool:
    return _contains_any_marker(_section_text(section), CONCLUSION_MARKERS)


def _is_context_section(section: SectionPlan) -> bool:
    return _contains_any_marker(_section_text(section), EARLY_STAGE_MARKERS)


def _format_brief_list(value: Any) -> str:
    if not value:
        return "- None provided"
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return f"- {value}"


def _format_design_spec(spec: DesignSpec) -> str:
    accent_color = spec.accent_color or "None"
    return "\n".join(
        [
            f"- theme_name: {spec.theme_name}",
            f"- visual_tone: {spec.visual_tone}",
            f"- density_level: {spec.density_level}",
            f"- font_scale: {spec.font_scale}",
            f"- accent_color: {accent_color}",
            f"- background_style: {spec.background_style}",
        ]
    )


def _format_layout_contracts() -> str:
    lines: list[str] = []
    for contract in list_layout_contracts():
        best_for = ", ".join(contract.best_for)
        required = ", ".join(contract.required_slots)
        optional = ", ".join(contract.optional_slots) or "none"
        avoid = ", ".join(contract.avoid_when) or "none"
        lines.append(
            "- "
            f"{contract.layout_name}: best_for={best_for}; "
            f"min_items={contract.min_items}; max_items={contract.max_items}; "
            f"required_slots={required}; optional_slots={optional}; avoid_when={avoid}"
        )
    return "\n".join(lines)


def _format_role_layout_guidance() -> str:
    return "\n".join(
        [
            "- comparison: prefer comparison_matrix for two-option, before/after, or normal AI vs Agent comparisons.",
            "- process: prefer process_flow for workflows, pipelines, or step-by-step sequences with 3-5 steps.",
            "- risk: prefer risk_matrix for risk / impact / mitigation or governance content with 3-4 risks.",
            "- summary: prefer key_takeaway for strong conclusions, action checklists, or pre-closing summary pages.",
            "- cover still uses title_slide; final thank-you pages can still use closing_slide.",
        ]
    )


@dataclass(frozen=True)
class _ArcStep:
    key_message: str
    slide_role: SlideRole
    content_goal: str
    layout_hint: str | None = None


@dataclass(frozen=True)
class _LongDeckSectionBlueprint:
    slug: str
    title: str
    purpose: str
    key_questions: tuple[str, ...]
    key_messages: tuple[str, ...]
    preferred_layouts: tuple[str, ...]
    slide_roles: tuple[SlideRole, ...]
    message_markers: tuple[str, ...] = ()


_LONG_DECK_SECTION_BLUEPRINTS: tuple[_LongDeckSectionBlueprint, ...] = (
    _LongDeckSectionBlueprint(
        slug="opening_context",
        title="Cover and Context",
        purpose="Open the narrative, anchor the audience problem, and explain why the topic matters now.",
        key_questions=(
            "Why does this topic matter for this audience now?",
            "What decision or framing does the deck need before details?",
        ),
        key_messages=(
            "Open with the core judgment before expanding the rest of the deck.",
            "Use early slides to define the context the audience will carry through later sections.",
        ),
        preferred_layouts=("title_slide", "two_column", "key_takeaway"),
        slide_roles=("cover", "context"),
        message_markers=("背景", "context", "why", "价值", "场景", "问题"),
    ),
    _LongDeckSectionBlueprint(
        slug="responsibility_boundary",
        title="Problem and Responsibility Boundary",
        purpose="Explain what changes when the topic becomes a product responsibility instead of a pure capability demo.",
        key_questions=(
            "What product problem is being solved?",
            "Why is the responsibility boundary different here?",
        ),
        key_messages=(
            "Responsibility boundaries should be explicit before solution detail expands.",
            "Comparison slides should clarify what the product must own, not just what the model can do.",
        ),
        preferred_layouts=("comparison_matrix", "two_column", "metric_cards"),
        slide_roles=("comparison",),
        message_markers=("责任", "边界", "对比", "comparison", "取舍"),
    ),
    _LongDeckSectionBlueprint(
        slug="technical_boundary",
        title="Framework and Technical Boundary",
        purpose="Define the technical boundary, system constraints, and capability framing that shape product promises.",
        key_questions=(
            "What can the system safely promise?",
            "Which constraints define the technical boundary?",
        ),
        key_messages=(
            "Technical boundary should shape the product promise before workflow detail grows.",
            "Framework slides should separate capability, constraint, and escalation paths.",
        ),
        preferred_layouts=("four_cards", "three_column", "key_takeaway"),
        slide_roles=("framework",),
        message_markers=("技术", "能力", "约束", "系统", "权限", "边界"),
    ),
    _LongDeckSectionBlueprint(
        slug="user_needs_tasks",
        title="User Needs and Task Decomposition",
        purpose="Translate user goals into task units, decision points, and success conditions that can be designed.",
        key_questions=(
            "Which user tasks should be decomposed first?",
            "What inputs, states, and success conditions need definition?",
        ),
        key_messages=(
            "User needs become design material only after they are decomposed into executable tasks.",
            "Task decomposition should expose what the product must define before automation expands.",
        ),
        preferred_layouts=("three_column", "four_cards", "two_column"),
        slide_roles=("framework",),
        message_markers=("用户", "需求", "任务", "拆解", "状态", "success", "need", "task"),
    ),
    _LongDeckSectionBlueprint(
        slug="workflow_process",
        title="Workflow and Process Design",
        purpose="Show the execution sequence, control points, and handoff logic that make the workflow operable.",
        key_questions=(
            "What is the execution order?",
            "Where do confirmation, validation, and rollback happen?",
        ),
        key_messages=(
            "Workflow design should show control points, not only a nominal happy path.",
            "Process detail should make handoff and fallback legible before launch decisions.",
        ),
        preferred_layouts=("process_flow", "four_cards", "three_column"),
        slide_roles=("process",),
        message_markers=("流程", "步骤", "确认", "回退", "workflow", "process"),
    ),
    _LongDeckSectionBlueprint(
        slug="metrics_evaluation",
        title="Metrics and Evaluation",
        purpose="Define how success will be measured and what evidence is needed to justify rollout.",
        key_questions=(
            "How will the team measure whether the workflow is working?",
            "Which metrics decide whether rollout should continue?",
        ),
        key_messages=(
            "Metrics should explain how they are measured before they are used as launch gates.",
            "Evaluation slides should help decide whether the workflow is worth scaling.",
        ),
        preferred_layouts=("metric_cards", "comparison_matrix", "three_column"),
        slide_roles=("metrics",),
        message_markers=("指标", "评估", "衡量", "metric", "measure", "evaluation"),
    ),
    _LongDeckSectionBlueprint(
        slug="risk_governance",
        title="Risks and Governance",
        purpose="Make the major risks, impacts, and mitigation actions concrete before the deck closes.",
        key_questions=(
            "Which risks block rollout if unaddressed?",
            "What mitigation actions reduce those risks?",
        ),
        key_messages=(
            "Risk sections should name concrete failure modes and the actions that constrain them.",
            "Governance slides should explain what the team will actually do when risk appears.",
        ),
        preferred_layouts=("risk_matrix", "two_column", "three_column"),
        slide_roles=("risk",),
        message_markers=("风险", "治理", "缓解", "risk", "governance", "mitigation"),
    ),
    _LongDeckSectionBlueprint(
        slug="conclusion_action",
        title="Conclusion and Action",
        purpose="Close with the key product judgment and the next executable actions for the target audience.",
        key_questions=(
            "What is the strongest final product judgment?",
            "What should the audience do next?",
        ),
        key_messages=(
            "The conclusion should make the final tradeoff explicit instead of reopening background.",
            "Closing actions should be concrete enough for the audience to execute next.",
        ),
        preferred_layouts=("key_takeaway", "closing_slide"),
        slide_roles=("summary",),
        message_markers=("结论", "下一步", "行动", "summary", "closing", "action"),
    ),
)


_PLAN_RECIPES: dict[str, dict[str, tuple[_ArcStep, ...]]] = {
    "technical_product_share": {
        "problem_to_method": (
            _ArcStep("责任边界先被重新定义", "comparison", "对比普通 AI 与 Agent 产品在责任边界上的差异。"),
            _ArcStep("技术边界决定产品承诺", "framework", "拆解模型能力、工具能力与系统约束的边界。"),
            _ArcStep("需求分析要落到可执行任务", "framework", "把用户目标、任务状态和成功条件拆成可设计对象。"),
            _ArcStep("工作流设计决定 Agent 可控性", "process", "说明从需求推导到工具调用与人工确认的流程。"),
            _ArcStep("评估指标要同时看效果与风险", "metrics", "定义效率、准确性、可控性和用户信任等指标。"),
            _ArcStep("落地风险需要前置治理", "risk", "列出权限、失败处理和责任归属的主要风险与缓解方式。"),
            _ArcStep("小场景验证比大而全更可靠", "summary", "给出从低风险场景启动的落地建议。"),
        ),
        "workflow_first": (
            _ArcStep("真实场景先于能力清单", "context", "从用户任务场景解释为什么需要 Agent 产品。"),
            _ArcStep("任务拆解决定 Agent 边界", "framework", "把复杂需求拆成目标、输入、状态和输出。"),
            _ArcStep("工作流是产品经理的核心设计物", "process", "呈现需求到步骤、工具、确认点的流程。"),
            _ArcStep("权限设计保护用户与系统", "risk", "说明工具权限、数据权限和人工接管边界。"),
            _ArcStep("评估必须覆盖过程质量", "metrics", "说明任务完成率、回退率、误触发和满意度。"),
            _ArcStep("失败处理决定真实可用性", "risk", "列出失败场景、影响和可恢复策略。"),
            _ArcStep("落地从可观测闭环开始", "summary", "总结如何用日志、QA 和反馈形成迭代闭环。"),
        ),
        "risk_first": (
            _ArcStep("先定义不能做什么", "comparison", "对比能力展示与产品责任之间的差异。"),
            _ArcStep("风险约束不是上线后的补丁", "risk", "说明高风险场景、影响和治理动作。"),
            _ArcStep("系统约束让 Agent 可被信任", "framework", "拆解规则、权限、状态和审计四类约束。"),
            _ArcStep("工作流要暴露关键确认点", "process", "展示用户确认、工具调用和回滚路径。"),
            _ArcStep("指标要衡量可控而不只衡量效率", "metrics", "用效果、稳定性和安全性指标约束产品判断。"),
            _ArcStep("取舍来自场景优先级", "comparison", "对比自动化收益与风险成本的产品取舍。"),
            _ArcStep("落地路径需要渐进放权", "summary", "总结从建议型到执行型 Agent 的推进节奏。"),
        ),
    },
    "classroom_teaching": {
        "concept_to_practice": (
            _ArcStep("先建立共同问题", "context", "用学习或课堂场景引入主题。"),
            _ArcStep("核心概念需要边界感", "framework", "解释关键概念、适用范围和常见误解。"),
            _ArcStep("方法要能被学生复用", "process", "把理解主题的方法拆成可操作步骤。"),
            _ArcStep("例子帮助连接真实任务", "comparison", "通过对比或案例展示好做法与坏做法。"),
            _ArcStep("风险提醒比口号更重要", "risk", "说明学术诚信、依赖风险或安全边界。"),
            _ArcStep("练习建议让知识落地", "summary", "给出课后行动或检查清单。"),
        ),
        "problem_example_action": (
            _ArcStep("问题意识决定学习方向", "context", "说明为什么这个主题值得学生理解。"),
            _ArcStep("具体例子降低理解门槛", "comparison", "用两个场景或前后变化说明主题价值。"),
            _ArcStep("方法框架提供操作路径", "framework", "归纳学生可以复用的关键方法。"),
            _ArcStep("注意事项保护学习质量", "risk", "列出误用、过度依赖和诚信风险。"),
            _ArcStep("行动建议要具体可执行", "summary", "给出下一步学习和实践建议。"),
        ),
    },
    "business_pitch": {
        "pain_solution_market": (
            _ArcStep("用户痛点需要被量化", "context", "说明目标客户面临的高频问题。"),
            _ArcStep("市场机会来自明确人群", "metrics", "用机会规模、频次或价值指标说明吸引力。"),
            _ArcStep("方案要直接回应痛点", "comparison", "对比现状与方案后的体验变化。"),
            _ArcStep("产品能力构成差异化", "framework", "拆解核心能力、壁垒和交付方式。"),
            _ArcStep("商业模式要可验证", "metrics", "说明收入、成本或增长指标。"),
            _ArcStep("竞争优势来自执行路径", "comparison", "对比竞品或替代方案的差异。"),
            _ArcStep("Roadmap 聚焦近期验证", "process", "展示从试点到扩张的阶段路径。"),
        ),
        "customer_value": (
            _ArcStep("客户问题定义价值边界", "context", "说明目标客户、任务和未满足需求。"),
            _ArcStep("价值主张必须一句话说清", "summary", "提炼核心价值与用户收益。"),
            _ArcStep("使用场景证明需求真实", "process", "展示典型客户使用流程。"),
            _ArcStep("方案能力服务关键场景", "framework", "拆解产品能力与场景的对应关系。"),
            _ArcStep("指标验证商业可行性", "metrics", "说明采用率、留存、效率或转化指标。"),
            _ArcStep("商业化节奏需要取舍", "comparison", "对比不同客户或渠道路径。"),
            _ArcStep("下一步聚焦可交付承诺", "summary", "给出试点、合作或行动请求。"),
        ),
    },
    "project_report": {
        "goal_process_result": (
            _ArcStep("项目目标定义交付标准", "context", "说明项目背景、目标和评价标准。"),
            _ArcStep("方法选择回应约束条件", "framework", "解释技术、资源或时间约束下的方法选择。"),
            _ArcStep("实现过程需要可追踪", "process", "展示关键模块或工作流程。"),
            _ArcStep("结果要和目标对应", "metrics", "呈现成果、指标或完成情况。"),
            _ArcStep("问题暴露下一步改进空间", "risk", "列出遇到的问题、影响和处理方式。"),
            _ArcStep("改进方向形成后续计划", "summary", "总结复盘结论和下一步。"),
        ),
        "before_after_learning": (
            _ArcStep("背景说明为什么要做", "context", "介绍项目起点与核心挑战。"),
            _ArcStep("方案设计体现关键判断", "framework", "说明方案结构与核心设计取舍。"),
            _ArcStep("关键实现支撑最终效果", "process", "展示实现步骤或模块协作。"),
            _ArcStep("前后对比说明变化", "comparison", "对比项目实施前后的状态。"),
            _ArcStep("收获来自真实问题", "summary", "总结能力提升和经验教训。"),
            _ArcStep("风险和不足需要诚实呈现", "risk", "说明限制、风险和未来修正方式。"),
        ),
    },
    "risk_analysis": {
        "risk_map": (
            _ArcStep("背景决定风险边界", "context", "说明风险分析对象和适用范围。"),
            _ArcStep("风险分类帮助排序", "framework", "把风险拆成可管理类别。"),
            _ArcStep("高风险场景需要优先处理", "risk", "列出最关键风险、影响和缓解措施。"),
            _ArcStep("影响路径决定治理优先级", "comparison", "对比不同风险的影响范围和严重度。"),
            _ArcStep("缓解措施要能执行", "process", "展示预防、监控和响应流程。"),
            _ArcStep("监控指标让风险可见", "metrics", "定义触发阈值、质量指标和复盘指标。"),
        ),
        "control_framework": (
            _ArcStep("问题定义控制目标", "context", "说明控制目标和失败后果。"),
            _ArcStep("权限是第一道边界", "risk", "列出权限风险、影响和缓解措施。"),
            _ArcStep("审计让过程可追责", "process", "展示记录、检查和复盘流程。"),
            _ArcStep("回滚能力降低失败成本", "risk", "说明失败处理和恢复策略。"),
            _ArcStep("指标监测治理效果", "metrics", "定义风险暴露、响应速度和误报指标。"),
            _ArcStep("落地建议需要分阶段", "summary", "总结控制框架的实施顺序。"),
        ),
    },
    "general_knowledge_share": {
        "background_concept_application": (
            _ArcStep("背景说明主题价值", "context", "说明主题与听众的关系。"),
            _ArcStep("核心概念建立共同语言", "framework", "解释关键概念和边界。"),
            _ArcStep("重点一提供主要判断", "framework", "展开第一个核心观点。"),
            _ArcStep("重点二补足实践视角", "comparison", "对比不同做法或场景。"),
            _ArcStep("应用场景帮助迁移理解", "process", "说明如何把概念应用到实际任务。"),
            _ArcStep("风险边界避免误用", "risk", "说明限制、风险和注意事项。"),
        ),
    },
}


def _brief_text(brief: Any) -> str:
    values = [
        getattr(brief, "topic", ""),
        getattr(brief, "audience", ""),
        getattr(brief, "purpose", ""),
        getattr(brief, "content_focus", ""),
        getattr(brief, "user_requirements_raw", "") or "",
        " ".join(getattr(brief, "must_include", []) or []),
    ]
    return " ".join(str(value) for value in values if value).lower()


def _classify_plan_purpose(brief: Any) -> str:
    text = _brief_text(brief)
    if any(marker in text for marker in ["风险", "安全", "合规", "失败处理", "risk", "security", "compliance"]):
        if not any(marker in text for marker in ["产品经理", "agent 产品", "技术产品", "product manager"]):
            return "risk_analysis"
    if any(marker in text for marker in ["pitch", "商业计划", "融资", "投资", "市场机会", "商业模式"]):
        return "business_pitch"
    if any(marker in text for marker in ["项目总结", "作业汇报", "实习", "开发项目", "project report"]):
        return "project_report"
    if any(marker in text for marker in ["课堂", "教学", "学生", "学习", "classroom", "teaching", "students"]):
        if not any(marker in text for marker in ["产品经理", "agent 产品", "技术产品", "产品方法论"]):
            return "classroom_teaching"
    if any(marker in text for marker in ["ai 产品经理", "agent", "技术产品", "产品方法论", "product manager"]):
        return "technical_product_share"
    return "general_knowledge_share"


def _stable_variant_name(purpose: str, brief: Any, seed: str | None) -> str:
    variants = sorted(_PLAN_RECIPES[purpose])
    seed_material = seed or "|".join(
        [
            str(getattr(brief, "topic", "")),
            str(getattr(brief, "audience", "")),
            str(getattr(brief, "slide_count", "")),
            str(getattr(brief, "user_requirements_raw", "") or ""),
            str(getattr(brief, "content_focus", "") or ""),
        ]
    )
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    return variants[int(digest[:8], 16) % len(variants)]


def _content_items_for_layout(layout_name: str) -> int:
    contract = get_layout_contract(layout_name)
    preferred = {
        "title_slide": 1,
        "closing_slide": 2,
        "comparison_matrix": 2,
        "process_flow": 4,
        "risk_matrix": 3,
        "metric_cards": 3,
        "key_takeaway": 3,
        "four_cards": 4,
        "three_column": 3,
        "two_column": 2,
        "section_divider": 1,
    }.get(layout_name, contract.min_items)
    return min(max(preferred, contract.min_items), contract.max_items)


def _layout_for_step(step: _ArcStep, slide_index: int, slide_count: int) -> str:
    if slide_index == 1:
        return "title_slide"
    if slide_index == slide_count:
        return "closing_slide"
    if step.layout_hint:
        return step.layout_hint
    if step.slide_role == "comparison":
        return "comparison_matrix"
    if step.slide_role == "process":
        return "process_flow"
    if step.slide_role == "risk":
        return "risk_matrix"
    if step.slide_role == "metrics":
        return "metric_cards"
    if step.slide_role == "summary":
        return "key_takeaway"
    if step.slide_role == "framework":
        return "four_cards" if any(marker in step.key_message for marker in ["能力", "分类", "框架"]) else "three_column"
    return "two_column"


def _alternate_layout(role: SlideRole, current_layout: str) -> str:
    alternatives = {
        "context": ["two_column", "three_column", "key_takeaway"],
        "comparison": ["comparison_matrix", "two_column", "metric_cards"],
        "framework": ["three_column", "four_cards", "key_takeaway"],
        "process": ["process_flow", "four_cards", "three_column"],
        "metrics": ["metric_cards", "comparison_matrix", "three_column"],
        "risk": ["risk_matrix", "two_column", "three_column"],
        "summary": ["key_takeaway", "closing_slide", "four_cards"],
    }.get(role, [current_layout])
    for layout in alternatives:
        if layout != current_layout:
            return layout
    return current_layout


def _fit_steps_to_count(steps: tuple[_ArcStep, ...], count: int) -> list[_ArcStep]:
    if count <= 0:
        return []
    if count <= len(steps):
        return list(steps[:count])

    result = list(steps)
    extension_pool = _PLAN_RECIPES["general_knowledge_share"]["background_concept_application"]
    cursor = 0
    while len(result) < count:
        result.append(extension_pool[cursor % len(extension_pool)])
        cursor += 1
    return result


def _arc_step_text(step: _ArcStep) -> str:
    return f"{step.key_message} {step.content_goal} {step.layout_hint or ''}"


def _narrative_priority(step: _ArcStep) -> int:
    step_text = _arc_step_text(step)
    if (
        step.slide_role == "summary"
        and _contains_any_marker(step_text, EARLY_STAGE_MARKERS)
        and not _contains_any_marker(step_text, CONCLUSION_MARKERS)
    ):
        return 12
    if step.slide_role == "framework" and _contains_any_marker(step_text, ("需求", "任务", "用户", "need", "task", "user")):
        return 40
    return NARRATIVE_ROLE_PRIORITY.get(step.slide_role, 45)


def _order_steps_for_narrative(steps: list[_ArcStep]) -> list[_ArcStep]:
    return [
        step
        for _original_index, step in sorted(
            enumerate(steps),
            key=lambda indexed_step: (_narrative_priority(indexed_step[1]), indexed_step[0]),
        )
    ]


def _infer_long_deck_type(brief: Any, deck_plan: DeckPlan | None) -> str:
    if isinstance(brief, LongDeckPlanningRequest) and brief.deck_type:
        return brief.deck_type
    purpose = _classify_plan_purpose(brief)
    return {
        "technical_product_share": "technical_product_share",
        "classroom_teaching": "classroom_teaching",
        "business_pitch": "business_pitch",
        "project_report": "project_report",
        "risk_analysis": "risk_analysis",
        "general_knowledge_share": "knowledge_share",
    }.get(purpose, "knowledge_share")


def _fallback_section_blueprint(slide: SlidePlan) -> _LongDeckSectionBlueprint:
    for blueprint in _LONG_DECK_SECTION_BLUEPRINTS:
        if slide.slide_role in blueprint.slide_roles:
            return blueprint
        if any(marker.lower() in f"{slide.key_message} {slide.content_goal}".lower() for marker in blueprint.message_markers):
            return blueprint
    return _LONG_DECK_SECTION_BLUEPRINTS[2]


def _section_blueprint_for_slide(slide: SlidePlan) -> _LongDeckSectionBlueprint:
    if slide.slide_index == 1 or slide.slide_role == "cover":
        return _LONG_DECK_SECTION_BLUEPRINTS[0]
    if slide.recommended_layout == "closing_slide" or _is_conclusion_slide(slide):
        return _LONG_DECK_SECTION_BLUEPRINTS[-1]

    normalized_text = f"{slide.key_message} {slide.content_goal}".lower()
    for blueprint in _LONG_DECK_SECTION_BLUEPRINTS:
        if slide.slide_role in blueprint.slide_roles:
            if blueprint.message_markers and any(marker.lower() in normalized_text for marker in blueprint.message_markers):
                return blueprint
    for blueprint in _LONG_DECK_SECTION_BLUEPRINTS:
        if blueprint.message_markers and any(marker.lower() in normalized_text for marker in blueprint.message_markers):
            return blueprint
    return _fallback_section_blueprint(slide)


def _ideal_long_section_count(slide_count: int) -> int:
    return len(_LONG_DECK_SECTION_BLUEPRINTS)


def _select_long_deck_blueprints(
    deck_plan: DeckPlan | None,
    slide_count: int,
) -> list[_LongDeckSectionBlueprint]:
    if deck_plan is None:
        return list(_LONG_DECK_SECTION_BLUEPRINTS[: _ideal_long_section_count(slide_count)])

    selected: list[_LongDeckSectionBlueprint] = []
    used_slugs: set[str] = set()
    for slide in deck_plan.slides:
        blueprint = _section_blueprint_for_slide(slide)
        if blueprint.slug in used_slugs:
            continue
        selected.append(blueprint)
        used_slugs.add(blueprint.slug)

    if not selected:
        selected = list(_LONG_DECK_SECTION_BLUEPRINTS[: _ideal_long_section_count(slide_count)])

    if selected[0].slug != _LONG_DECK_SECTION_BLUEPRINTS[0].slug:
        selected.insert(0, _LONG_DECK_SECTION_BLUEPRINTS[0])
        used_slugs.add(_LONG_DECK_SECTION_BLUEPRINTS[0].slug)
    if selected[-1].slug != _LONG_DECK_SECTION_BLUEPRINTS[-1].slug:
        selected.append(_LONG_DECK_SECTION_BLUEPRINTS[-1])
        used_slugs.add(_LONG_DECK_SECTION_BLUEPRINTS[-1].slug)

    ideal_count = _ideal_long_section_count(slide_count)
    for blueprint in _LONG_DECK_SECTION_BLUEPRINTS:
        if len(selected) >= ideal_count:
            break
        if blueprint.slug not in used_slugs:
            insert_at = max(1, len(selected) - 1)
            selected.insert(insert_at, blueprint)
            used_slugs.add(blueprint.slug)

    ordered = sorted(
        selected,
        key=lambda blueprint: next(
            index for index, candidate in enumerate(_LONG_DECK_SECTION_BLUEPRINTS) if candidate.slug == blueprint.slug
        ),
    )
    if ordered[-1].slug != _LONG_DECK_SECTION_BLUEPRINTS[-1].slug:
        ordered = [item for item in ordered if item.slug != _LONG_DECK_SECTION_BLUEPRINTS[-1].slug]
        ordered.append(_LONG_DECK_SECTION_BLUEPRINTS[-1])
    return ordered


def _allocate_section_counts(slide_count: int, section_count: int) -> list[int]:
    counts = [1] * section_count
    remaining = slide_count - section_count
    cursor = 0
    while remaining > 0:
        counts[cursor % section_count] += 1
        cursor += 1
        remaining -= 1
    return counts


def _section_messages_for_blueprint(
    blueprint: _LongDeckSectionBlueprint,
    deck_plan: DeckPlan | None,
    *,
    index: int,
    section_count: int,
) -> tuple[list[str], list[str], list[str], list[str]]:
    if deck_plan is None:
        return (
            list(blueprint.key_questions),
            list(blueprint.key_messages),
            [],
            [],
        )

    relevant_slides = [
        slide
        for slide in deck_plan.slides
        if _section_blueprint_for_slide(slide).slug == blueprint.slug
    ]
    key_messages = [slide.key_message for slide in relevant_slides] or list(blueprint.key_messages)
    key_questions = [
        slide.content_goal
        for slide in relevant_slides
        if slide.content_goal not in key_messages
    ] or list(blueprint.key_questions)
    preferred_layouts = list(dict.fromkeys(
        [slide.recommended_layout for slide in relevant_slides] + list(blueprint.preferred_layouts)
    ))
    must_not_repeat = [
        message
        for slide in relevant_slides
        for message in slide.must_not_repeat
    ]

    if index == 0:
        must_not_repeat.append("不要在开场之后重复背景页")
    if index == section_count - 1:
        must_not_repeat.extend(["不要重新引入背景铺垫", "不要在结尾后补背景"])

    return (
        list(dict.fromkeys(key_questions)),
        list(dict.fromkeys(key_messages)),
        preferred_layouts,
        list(dict.fromkeys(must_not_repeat)),
    )


def _build_long_deck_sections(
    brief: Any,
    deck_plan: DeckPlan | None,
    *,
    slide_count: int,
) -> list[SectionPlan]:
    blueprints = _select_long_deck_blueprints(deck_plan, slide_count)
    counts = _allocate_section_counts(slide_count, len(blueprints))

    sections: list[SectionPlan] = []
    current_start = 1
    brief_must_include = list(_brief_value(brief, "must_include", []) or [])
    brief_must_avoid = list(_brief_value(brief, "must_avoid", []) or [])
    for index, (blueprint, count) in enumerate(zip(blueprints, counts, strict=True), start=1):
        start_slide = current_start
        end_slide = current_start + count - 1
        key_questions, key_messages, preferred_layouts, local_must_avoid = _section_messages_for_blueprint(
            blueprint,
            deck_plan,
            index=index - 1,
            section_count=len(blueprints),
        )
        must_include = brief_must_include if index in {1, len(blueprints)} else []
        if blueprint.slug == "metrics_evaluation":
            must_include = must_include + ["说明指标如何衡量"]
        if blueprint.slug == "risk_governance":
            must_include = must_include + ["每个风险行都要有 risk / impact / mitigation"]
        if blueprint.slug == "conclusion_action":
            must_include = must_include + ["给目标受众可执行的下一步动作"]

        sections.append(
            SectionPlan(
                section_id=f"section_{index:02d}_{blueprint.slug}",
                title=blueprint.title,
                purpose=blueprint.purpose,
                start_slide=start_slide,
                end_slide=end_slide,
                target_slide_count=count,
                key_questions=key_questions,
                key_messages=key_messages,
                preferred_layouts=preferred_layouts or list(blueprint.preferred_layouts),
                must_include=list(dict.fromkeys(must_include)),
                must_avoid=list(dict.fromkeys(local_must_avoid + brief_must_avoid)),
            )
        )
        current_start = end_slide + 1
    return sections


def split_long_deck_into_batches(long_deck_plan: LongDeckPlan) -> list[BatchPlan]:
    return list(long_deck_plan.batches)


def get_batch_by_id(long_deck_plan: LongDeckPlan, batch_id: str) -> BatchPlan:
    for batch in long_deck_plan.batches:
        if batch.batch_id == batch_id:
            return batch
    raise ValueError(f"Unknown batch_id '{batch_id}'.")


def get_batch_context(long_deck_plan: LongDeckPlan, batch_id: str) -> BatchContext:
    batch = get_batch_by_id(long_deck_plan, batch_id)
    sections = [section for section in long_deck_plan.sections if section.section_id in batch.section_ids]
    first_section_index = next(
        index for index, section in enumerate(long_deck_plan.sections) if section.section_id == sections[0].section_id
    )
    last_section_index = next(
        index for index, section in enumerate(long_deck_plan.sections) if section.section_id == sections[-1].section_id
    )

    previous_section_summary = None
    if first_section_index > 0:
        previous = long_deck_plan.sections[first_section_index - 1]
        previous_section_summary = f"{previous.title}: {previous.purpose}"

    next_section_summary = None
    if last_section_index < len(long_deck_plan.sections) - 1:
        nxt = long_deck_plan.sections[last_section_index + 1]
        next_section_summary = f"{nxt.title}: {nxt.purpose}"

    return BatchContext(
        batch_id=batch.batch_id,
        start_slide=batch.start_slide,
        end_slide=batch.end_slide,
        section_ids=batch.section_ids,
        batch_goal=batch.batch_goal,
        context_summary=batch.context_summary,
        must_include=batch.must_include,
        must_not_repeat=batch.must_not_repeat,
        expected_outputs=batch.expected_outputs,
        sections=sections,
        previous_section_summary=previous_section_summary,
        next_section_summary=next_section_summary,
    )


def build_deterministic_long_deck_plan(
    brief: Any,
    deck_plan: DeckPlan | None = None,
    *,
    batch_size: int = 10,
) -> LongDeckPlan:
    """Build a deterministic LongDeckPlan without calling an LLM."""

    slide_count = int(_brief_value(brief, "slide_count", 0))
    if slide_count <= 20:
        raise ValueError("LongDeckPlan is only used for slide_count > 20.")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")

    topic = str(_brief_value(brief, "topic"))
    audience = str(_brief_value(brief, "audience"))
    language = str(_brief_value(brief, "language", "zh-CN") or "zh-CN")
    deck_type = _infer_long_deck_type(brief, deck_plan)
    sections = _build_long_deck_sections(brief, deck_plan, slide_count=slide_count)

    batches: list[BatchPlan] = []
    batch_index = 1
    for start_slide in range(1, slide_count + 1, batch_size):
        end_slide = min(start_slide + batch_size - 1, slide_count)
        overlapping_sections = [
            section
            for section in sections
            if section.start_slide <= end_slide and start_slide <= section.end_slide
        ]
        batch_goal = "Carry the next narrative segment forward without repeating earlier sections."
        if overlapping_sections:
            batch_goal = (
                f"Develop {overlapping_sections[0].title}"
                if len(overlapping_sections) == 1
                else f"Bridge {overlapping_sections[0].title} into {overlapping_sections[-1].title}"
            )
        context_summary = " ".join(section.purpose for section in overlapping_sections)
        must_include = list(
            dict.fromkeys(item for section in overlapping_sections for item in section.must_include)
        )
        must_not_repeat = list(
            dict.fromkeys(item for section in overlapping_sections for item in section.must_avoid)
        )
        expected_outputs = [
            f"Slides {start_slide}-{end_slide} stay inside the planned narrative stage.",
            "Layouts stay aligned with the section semantics they cover.",
            "No repeated conclusion or reopening background after the closing stage.",
        ]
        batches.append(
            BatchPlan(
                batch_id=f"batch_{batch_index:02d}",
                start_slide=start_slide,
                end_slide=end_slide,
                section_ids=[section.section_id for section in overlapping_sections],
                batch_goal=batch_goal,
                context_summary=context_summary or "Continue the planned narrative without breaking section order.",
                must_include=must_include,
                must_not_repeat=must_not_repeat,
                expected_outputs=expected_outputs,
            )
        )
        batch_index += 1

    narrative_summary = (
        "The long deck moves from opening context through boundary framing, system design, "
        "workflow, evaluation, risk control, and closes with an action-oriented conclusion."
    )
    global_style_notes = [
        "Keep section order stable across batches so context does not reappear after conclusion.",
        "Preserve layout diversity by mixing matrix, process, card, metric, and takeaway pages.",
        "Treat batches as generation windows only; the audience should still feel one continuous narrative.",
    ]
    content_constraints = [
        "Do not generate all slides in one LLM call.",
        "Do not reopen background/context after the conclusion section begins.",
        "Closing section must stay last and end with executable next steps.",
    ]
    return LongDeckPlan(
        topic=topic,
        audience=audience,
        slide_count=slide_count,
        language=language,
        deck_type=deck_type,
        sections=sections,
        batches=batches,
        narrative_summary=narrative_summary,
        global_style_notes=global_style_notes,
        content_constraints=content_constraints,
    )


def build_deterministic_deck_plan(brief: Any, seed: str | None = None) -> DeckPlan:
    """Build a deterministic DeckPlan without calling an LLM."""

    slide_count = int(_brief_value(brief, "slide_count", 1))
    topic = str(_brief_value(brief, "topic"))
    audience = str(_brief_value(brief, "audience"))
    purpose = _classify_plan_purpose(brief)
    variant_name = _stable_variant_name(purpose, brief, seed)
    body_steps = _order_steps_for_narrative(
        _fit_steps_to_count(_PLAN_RECIPES[purpose][variant_name], max(slide_count - 2, 0))
    )

    slides: list[SlidePlan] = []
    used_messages: set[str] = set()

    for index in range(1, slide_count + 1):
        if index == 1:
            step = _ArcStep(f"{topic} 的核心判断", "cover", f"用一句清晰主线打开面向 {audience} 的分享。")
        elif index == slide_count:
            step = _ArcStep("下一步从可执行判断开始", "summary", "收束关键观点，并给出可执行的行动方向。")
        else:
            step = body_steps[index - 2]

        key_message = step.key_message
        if key_message in used_messages:
            key_message = f"{key_message} {index}"
        used_messages.add(key_message)

        layout_name = _layout_for_step(step, index, slide_count)
        if len(slides) >= 2 and slides[-1].recommended_layout == slides[-2].recommended_layout == layout_name:
            layout_name = _alternate_layout(step.slide_role, layout_name)

        must_not_repeat = [
            prior.key_message
            for prior in slides[-3:]
        ] or ["泛泛介绍和空洞口号"]

        slides.append(
            SlidePlan(
                slide_index=index,
                slide_role=step.slide_role,
                key_message=key_message,
                content_goal=step.content_goal,
                recommended_layout=layout_name,
                content_items=_content_items_for_layout(layout_name),
                must_not_repeat=must_not_repeat,
            )
        )

    return DeckPlan(
        topic=topic,
        audience=audience,
        slide_count=slide_count,
        slides=slides,
        plan_source="deterministic",
    )


def build_deck_plan_prompt(brief: Any) -> str:
    """Build the planning prompt from a DeckBrief-like object."""

    slide_count = _brief_value(brief, "slide_count")
    design_spec = DesignSpec()
    layout_names = ", ".join(contract.layout_name for contract in list_layout_contracts())
    slide_roles = ", ".join(SLIDE_ROLES)
    return f"""Create a DeckPlan as structured data before generating Slide IR.

Brief:
- Topic: {_brief_value(brief, "topic")}
- Audience: {_brief_value(brief, "audience")}
- Slide count: {slide_count}
- Language: {_brief_value(brief, "language")}
- Purpose: {_brief_value(brief, "purpose", "Not specified") or "Not specified"}
- Tone: {_brief_value(brief, "tone", "Not specified") or "Not specified"}
- Visual style: {_brief_value(brief, "visual_style", "Not specified") or "Not specified"}
- Content focus: {_brief_value(brief, "content_focus", "Not specified") or "Not specified"}
- Must include:
{_format_brief_list(_brief_value(brief, "must_include", []))}
- Must avoid:
{_format_brief_list(_brief_value(brief, "must_avoid", []))}
- Raw user requirements: {_brief_value(brief, "user_requirements_raw", None) or "None provided"}

Default DesignSpec guidance:
{_format_design_spec(design_spec)}

LayoutContract registry:
{_format_layout_contracts()}

SlideRole to layout guidance:
{_format_role_layout_guidance()}

Planning rules:
- Return only structured data that validates as DeckPlan.
- Plan exactly {slide_count} slides.
- Each slide must have one unique key_message.
- Avoid repeated key_message values across slides.
- Every slide must set slide_role to one of: {slide_roles}.
- Every slide needs a distinct slide_role, content_goal, and key_message.
- recommended_layout must be one of the LayoutContract registry layout_name values only: {layout_names}.
- Choose a recommended_layout that naturally matches the slide_role and content_goal.
- If slide_role is comparison, process, risk, or summary, prefer the matching professional layout when the content fits.
- Set content_items to the estimated number of major content blocks, excluding the slide title.
- Do not let content_items exceed the selected layout max_items.
- For 3-slide short decks, do not prioritize section_divider.
- Use section_divider only for true chapter transition or section break pages, not ordinary content explanation pages.
- For decks with 8 slides or fewer, do not recommend section_divider unless the user explicitly asks for divider, transition, or section break pages.
- If a slide has only one key_message plus one explanation sentence, recommend key_takeaway, two_column, or three_column instead of section_divider.
- For long decks, keep layout diversity; for 8-slide decks, mix at least three useful layouts when the content allows it.
- Avoid three consecutive card-grid-style slides; alternate framework/card pages with process_flow, risk_matrix, comparison_matrix, key_takeaway, or closing-style pages when the story allows it.
- Narrative order matters: cover first, then background/context/why-now/value/problem framing, then comparison/boundary, framework/concept model, user need/task decomposition, workflow/process, metrics/evaluation, risk/governance, conclusion/key_takeaway, and closing/next steps last.
- Background / context / value / why-now slides are early-stage slides; never introduce them after conclusion, key_takeaway, closing_slide, or next-action slides.
- 核心结论 / 下一步行动必须放在后半段，通常是最后两页；背景 / 价值 / 为什么重要必须放在前半段。
- If slide_count grows, insert new slides into the proper narrative stage instead of appending them near or after closing.
- Avoid repeating content listed in each slide's must_not_repeat.
- Keep the plan concise enough that the generator can follow it exactly.
"""


def generate_deck_plan_with_model(
    model: Any,
    brief: Any,
    *,
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
) -> DeckPlan:
    """Generate a DeckPlan using a LangChain-compatible structured-output model."""

    prompt = build_deck_plan_prompt(brief)
    structured_model = model.with_structured_output(DECK_PLAN_STRUCTURED_OUTPUT_SCHEMA)
    with observed_stage(
        stage_observer,
        "generate_deck_plan",
        slide_count=getattr(brief, "slide_count", None),
        use_deck_plan=True,
    ):
        response = invoke_with_timeout(
            lambda: structured_model.invoke(prompt),
            timeout_seconds=timeout_seconds,
            stage_name="generate_deck_plan",
        )
        if isinstance(response, dict) and "structured_response" in response:
            response = response["structured_response"]
        if isinstance(response, dict) and "deck_plan" in response:
            response = response["deck_plan"]
        response = _normalize_deck_plan_payload(response, brief)
        return DeckPlan.model_validate(response)
