"""Deterministic mock client: the full pipeline with zero API calls.

Used by the offline demo and the test suite. It implements the same
``complete_json`` interface as real providers but answers every task from
the structured ``context`` the orchestrator passes along, routing page
design to the archetype library.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ppt_agent.v2.fallback import design_fallback_page
from ppt_agent.v2.planning import PageBrief
from ppt_agent.v2.providers import UsageMeter


_SECTION_TEMPLATES = [
    ("背景与挑战", "解释为什么现在必须行动", ["行业拐点已经出现", "现有流程的三个断点", "竞品动作与时间窗口", "用户痛点的数据佐证", "不行动的机会成本"]),
    ("核心理念", "建立解决问题的思维框架", ["以终为始的目标拆解", "最小闭环优先", "数据驱动迭代", "人机协作分工", "价值验证先于规模化"]),
    ("方案设计", "拆解方案的关键组成部分", ["整体架构一页看懂", "核心模块与职责", "关键流程走查", "边界与依赖", "与现有系统的衔接"]),
    ("落地路径", "给出可执行的推进节奏", ["三阶段推进节奏", "第一阶段:验证闭环", "第二阶段:扩大试点", "第三阶段:全面推广", "里程碑与检查点"]),
    ("数据与验证", "用数据证明方案有效", ["核心指标定义", "试点数据表现", "对照组差异分析", "用户反馈摘要", "成本收益测算"]),
    ("风险与对策", "预判风险并给出应对", ["技术风险与降级方案", "组织阻力与共识机制", "合规与安全底线", "供应商依赖对策", "预算超支预警线"]),
    ("资源与协作", "明确所需资源与分工", ["团队分工地图", "关键角色与职责", "外部资源清单", "协作节奏与例会", "决策升级机制"]),
    ("展望与行动", "收束到下一步行动", ["三个月后的样子", "本周就能开始的三件事", "需要拍板的两个决定", "长期演进方向", "行动清单与责任人"]),
]

_LAYOUT_CYCLE = ["cards", "two_column", "stats", "timeline", "chart", "list", "table", "quote"]


class MockLLMClient:
    """Answers every v2 task deterministically; no network, no key."""

    def __init__(self, *, latency_seconds: float = 0.0) -> None:
        self.usage = UsageMeter()
        self.latency_seconds = latency_seconds

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        context: Any = None,
    ) -> Any:
        del system, user, max_output_tokens
        if self.latency_seconds:
            await asyncio.sleep(self.latency_seconds)
        context = context or {}
        if task == "brief":
            return self._brief(context)
        if task == "theme":
            return self._theme(context)
        if task == "outline":
            return self._outline(context)
        if task == "section_pages":
            return self._section_pages(context)
        if task == "page_design":
            return self._page_design(context)
        if task == "page_repair":
            return context.get("page_payload", {})
        raise ValueError(f"MockLLMClient does not know task '{task}'")

    def _brief(self, context: dict[str, Any]) -> dict[str, Any]:
        prompt = str(context.get("user_prompt", "未命名主题")).strip()
        topic = prompt.splitlines()[0][:60] or "未命名主题"
        return {
            "topic": topic,
            "deck_title": topic[:40],
            "subtitle": "从问题到行动的完整推演",
            "audience": "业务与技术决策者",
            "purpose": "说服并推动决策",
            "tone": "专业、克制、有推动力",
            "language": "zh-CN",
            "key_points": [
                f"{topic}的现状与痛点",
                "机会窗口与时间压力",
                "解决方案的核心设计",
                "分阶段落地路径",
                "关键数据与验证结果",
                "风险识别与应对策略",
                "资源需求与协作机制",
                "下一步行动清单",
            ],
            "must_include": [],
            "must_avoid": [],
        }

    def _theme(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": "aurora-mock",
            "mood": "modern tech, confident",
            "motif": "corner_arc",
            "palette": {
                "background": "#F7F8FC",
                "surface": "#FFFFFF",
                "surface_alt": "#EEF1FA",
                "primary": "#4B5AE4",
                "primary_soft": "#DDE2FB",
                "secondary": "#22B8A6",
                "accent": "#F2A93B",
                "text": "#1E2233",
                "muted": "#6B7186",
                "on_primary": "#FFFFFF",
            },
        }

    def _outline(self, context: dict[str, Any]) -> dict[str, Any]:
        brief = context.get("brief", {})
        budget = int(context.get("content_budget", 24))
        topic = brief.get("topic", "主题")
        section_count = max(3, min(len(_SECTION_TEMPLATES), round(budget / 10) + 2))
        sections = []
        for index in range(section_count):
            title, goal, points = _SECTION_TEMPLATES[index % len(_SECTION_TEMPLATES)]
            sections.append(
                {
                    "title": title,
                    "goal": goal,
                    "content_pages": max(1, budget // section_count),
                    "talking_points": list(points),
                }
            )
        return {
            "deck_title": brief.get("deck_title", topic),
            "subtitle": brief.get("subtitle", ""),
            "sections": sections,
        }

    def _section_pages(self, context: dict[str, Any]) -> dict[str, Any]:
        section = context.get("section", {})
        page_count = int(context.get("page_count", 1))
        title = section.get("title", "章节")
        points = section.get("talking_points") or [f"{title}要点"]
        pages = []
        for index in range(page_count):
            anchor = points[index % len(points)]
            suffix = f" · {index // len(points) + 1}" if index >= len(points) else ""
            pages.append(
                {
                    "title": f"{anchor}{suffix}",
                    "summary": f"{title}:围绕「{anchor}」展开的论证与说明。",
                    "points": [
                        "现状:关键事实与判断",
                        "动作:本阶段要做的事",
                        "结果:可验证的产出",
                        "度量:跟踪指标与阈值",
                    ],
                    "layout_hint": _LAYOUT_CYCLE[index % len(_LAYOUT_CYCLE)],
                    "data_idea": None,
                }
            )
        return {"pages": pages}

    def _page_design(self, context: dict[str, Any]) -> dict[str, Any]:
        page_brief = PageBrief.model_validate(context.get("page_brief", {}))
        page_number = int(context.get("page_number", 1))
        page = design_fallback_page(
            page_brief,
            page_number=page_number,
            section_title=context.get("section_title"),
            language=str(context.get("language", "zh-CN")),
        )
        return page.model_dump(mode="json")
