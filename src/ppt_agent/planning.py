"""Deck-level planning primitives for generation prompts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, Self

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
