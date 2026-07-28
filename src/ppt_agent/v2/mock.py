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
        images: list[tuple[str, str]] | None = None,
    ) -> Any:
        del system, user, max_output_tokens, images
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
        if task == "anchor_design":
            return self._anchor_design(context)
        if task == "page_repair":
            return context.get("page_payload", {})
        if task == "image_digest":
            name = str((context or {}).get("name", "图片"))
            return {
                "description": f"「{name}」的示意图：展示了与主题相关的结构与数据要点。",
                "extracted_text": "",
            }
        if task == "revision_plan":
            return self._revision_plan(context)
        if task == "theme_revise":
            return self._theme_revise(context)
        if task == "image_classify":
            return self._image_classify(context)
        if task == "theme_from_images":
            theme = self._theme(context)
            theme["name"] = "extracted-from-images"
            return theme
        if task == "image_page":
            return self._image_page(context)
        raise ValueError(f"MockLLMClient does not know task '{task}'")

    def _image_classify(self, context: dict[str, Any]) -> dict[str, Any]:
        name = str((context or {}).get("name", "image.png")).lower()
        if any(word in name for word in ("slide", "ppt", "deck", "poster")):
            category, reasoning = "slide", "这张图本身就是一页完整的演示页面。"
        elif any(word in name for word in ("selfie", "pet", "cat", "dog", "scenery", "photo")):
            category, reasoning = "unrelated", "这张图片不像演示页面，也没有可直接使用的信息。"
        else:
            category, reasoning = "informative", "这张图不是完整的 PPT 页面，但包含可用于演示的信息。"
        return {
            "category": category,
            "confidence": 0.9,
            "reasoning": reasoning,
            "description": f"「{name}」的内容示意。",
            "extracted_text": "示例文字 123" if category != "unrelated" else "",
            "title_guess": "图片内容标题",
        }

    def _image_page(self, context: dict[str, Any]) -> dict[str, Any]:
        route = str((context or {}).get("route", "design_from_content"))
        name = str((context or {}).get("name", "image.png"))
        page_number = int((context or {}).get("page_number", 1))
        elements: list[dict[str, Any]] = [
            {"type": "text", "id": "rebuild_title", "frame": {"x": 64, "y": 48, "w": 900, "h": 60},
             "text": f"重建页 · {name}", "role": "title"},
            {"type": "shape", "id": "rebuild_card", "frame": {"x": 64, "y": 150, "w": 700, "h": 420},
             "shape": "rounded_rectangle", "fill": "surface"},
            {"type": "text", "id": "rebuild_body", "frame": {"x": 96, "y": 182, "w": 640, "h": 200},
             "text": "重建正文要点\n结构与文字均可编辑", "role": "body", "bullet": "dot"},
            {"type": "text", "id": f"route_tag_{route}", "frame": {"x": 96, "y": 600, "w": 500, "h": 28},
             "text": f"route:{route}", "role": "caption"},
        ]
        if route == "rebuild":
            elements.append(
                {"type": "image", "id": "rebuild_photo", "frame": {"x": 820, "y": 150, "w": 380, "h": 420},
                 "src": "crop:0.55,0.2,0.4,0.6", "label": "照片区域"}
            )
        elif route == "embed_with_notes":
            elements.append(
                {"type": "image", "id": "embedded_original", "frame": {"x": 820, "y": 150, "w": 380, "h": 420},
                 "src": name, "label": "原图"}
            )
        return {
            "role": "content",
            "title": f"重建页 {page_number}",
            "background": "background",
            "show_chrome": False,
            "elements": elements,
            "speaker_notes": f"这一页来自图片 {name} 的 {route} 路线。",
        }

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
            "style": {
                "composition": "clean modular grid, generous top whitespace",
                "decor": "soft rounded cards with one accent corner arc",
                "shape_language": "rounded rectangles and pills",
                "cover_concept": "deep gradient with a topic-specific focal shape cluster",
            },
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
                    "speaker_notes": f"这一页我们重点讲「{anchor}」，先说现状，再讲动作和结果。",
                }
            )
        return {"pages": pages}

    def _revision_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        import re as _re

        message = str(context.get("message", ""))
        selected = context.get("selected_pages") or []
        numbers = [int(match) for match in _re.findall(r"第\s*(\d+)\s*页", message)]
        numbers = list(dict.fromkeys(numbers + [int(n) for n in selected]))
        wants_restyle = any(
            word in message for word in ("颜色", "色调", "配色", "风格", "页码", "页脚")
        )
        wants_all_pages = not numbers and any(
            word in message for word in ("每页", "每一页", "所有页", "全部页")
        )
        pages = [
            {"page_number": number, "instruction": message, "new_brief": None}
            for number in numbers
        ]
        return {
            "reply": "好的，我按你的要求调整对应页面。",
            "theme_instruction": message if wants_restyle else None,
            "all_pages_instruction": message if wants_all_pages else None,
            "pages": pages,
        }

    def _theme_revise(self, context: dict[str, Any]) -> dict[str, Any]:
        theme = dict(context.get("theme") or {})
        instruction = str(context.get("instruction", ""))
        if any(word in instruction for word in ("页码", "page number", "页脚", "footer")):
            chrome = dict(theme.get("chrome") or {})
            if "页码" in instruction or "page number" in instruction:
                chrome["show_page_number"] = False
            if "页脚" in instruction or "footer" in instruction:
                chrome["show_footer"] = False
            theme["chrome"] = chrome
        if any(word in instruction for word in ("颜色", "色调", "配色", "color")):
            palette = dict(theme.get("palette") or {})
            palette.update({"primary": "#23415E", "secondary": "#4A7BA6", "accent": "#C0574F"})
            theme["palette"] = palette
            theme["name"] = f"{theme.get('name', 'theme')}-revised"
        return theme

    def _anchor_design(self, context: dict[str, Any]) -> dict[str, Any]:
        kind = str(context.get("kind", "cover"))
        deck_title = str(context.get("deck_title", "未命名演示"))
        page_number = int(context.get("page_number", 1))
        if kind == "cover":
            return {
                "role": "cover",
                "title": deck_title,
                "background": "primary",
                "background_gradient": {"start": "primary", "end": "secondary", "angle_deg": 120},
                "elements": [
                    {"type": "shape", "id": "mock_cover_diag", "frame": {"x": 820, "y": 0, "w": 460, "h": 720},
                     "shape": "parallelogram", "fill": "accent", "fill_alpha": 0.14},
                    {"type": "text", "id": "mock_cover_kicker", "frame": {"x": 96, "y": 220, "w": 500, "h": 30},
                     "text": "专题演示", "role": "kicker", "color": "on_primary"},
                    {"type": "text", "id": "mock_cover_title", "frame": {"x": 96, "y": 264, "w": 860, "h": 180},
                     "text": deck_title, "role": "display", "color": "on_primary"},
                ],
            }
        if kind == "section_divider":
            section_index = int(context.get("section_index") or 1)
            section_count = int(context.get("section_count") or 1)
            section_title = str(context.get("section_title") or "章节")
            return {
                "role": "section_divider",
                "title": section_title,
                "background": "primary",
                "background_gradient": {"start": "secondary", "end": "primary", "angle_deg": 45},
                "elements": [
                    {"type": "text", "id": "mock_div_num", "frame": {"x": 96, "y": 150, "w": 360, "h": 140},
                     "text": f"{section_index:02d}", "role": "display", "color": "accent", "size_pt": 84},
                    {"type": "text", "id": "mock_div_title", "frame": {"x": 96, "y": 310, "w": 900, "h": 110},
                     "text": section_title, "role": "section", "color": "on_primary"},
                    {"type": "text", "id": "mock_div_progress", "frame": {"x": 96, "y": 600, "w": 400, "h": 30},
                     "text": f"{section_index:02d} / {section_count:02d}", "role": "kicker", "color": "on_primary"},
                ],
            }
        return {
            "role": "closing",
            "title": "谢谢观看",
            "background": "primary",
            "background_gradient": {"start": "primary", "end": "secondary", "angle_deg": 245},
            "elements": [
                {"type": "text", "id": "mock_close_title", "frame": {"x": 190, "y": 300, "w": 900, "h": 110},
                 "text": "谢谢观看", "role": "display", "color": "on_primary", "align": "center"},
                {"type": "text", "id": "mock_close_note", "frame": {"x": 240, "y": 430, "w": 800, "h": 60},
                 "text": deck_title, "role": "subtitle", "color": "on_primary", "align": "center"},
            ],
        }

    def _page_design(self, context: dict[str, Any]) -> dict[str, Any]:
        page_brief = PageBrief.model_validate(context.get("page_brief", {}))
        page_number = int(context.get("page_number", 1))
        page = design_fallback_page(
            page_brief,
            page_number=page_number,
            section_title=context.get("section_title"),
            language=str(context.get("language", "zh-CN")),
        )
        payload = page.model_dump(mode="json")
        if context.get("revision_instruction"):
            payload["elements"].append(
                {
                    "type": "text",
                    "id": "mock_revision_tag",
                    "frame": {"x": 64, "y": 660, "w": 400, "h": 24},
                    "text": "已按修改要求重新设计",
                    "role": "caption",
                }
            )
        return payload
