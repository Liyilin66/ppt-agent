"""Prompts for the design step: verified content in, one slide of HTML out."""

from __future__ import annotations

import json
from typing import Any

from ppt_agent.v2.htmldeck import themes

# Which hand-written sample to show for each kind of content.
EXAMPLE_FOR = {
    "chart": "chart", "metrics": "hero", "table": "table", "process": "steps", "timeline": "steps",
    "roadmap": "steps", "points": "columns", "compare": "columns", "summary": "columns",
    "statement": "statement",
}

# Arrangements to rotate through, so pages written at the same time still differ.
LAYOUT_IDEAS: dict[str, list[str]] = {
    "points": [
        "等宽的块横排，把最重要的一个做成 panel，其余用 card",
        "左边一个 f2 的 panel 写核心结论和关键数字，右边 f3 用 list 逐条列出要点",
        "上下两行：上面一行两个大块，下面一行放其余要点，用 outline 或 topline",
        "左窄右宽：左侧 f1 放 stat 或关键词，右侧 f3 分条说明，条目之间用 rule 隔开",
        "2×2 的四宫格，对角的两个用 panel，另外两个用 card",
    ],
    "metrics": [
        "左边 f3 的 panel 放最重要的数字（stat xl），右边 f2 用 list 放其余指标",
        "一行等宽的块，每块一个 stat，最重要的那个用 panel",
        "上面一条通栏 panel 放最重要的数字和说明，下面一行 card 放其余指标",
    ],
    "chart": [
        "左边 f3 的 card 放图表，右边 f2 放解读（list）和一句 note",
        "右边 f3 放图表，左边 f2 的 panel 放最重要的数字（stat l）和解读",
        "上面通栏图表（card），下面一行两三个 outline 写解读",
    ],
    "table": [
        "通栏表格，下面一句 note",
        "左边 f3 表格，右边 f1 的 panel 写一句结论和关键数字",
    ],
    "process": [
        "步骤横排成等宽的块，每块带序号（stat s），最后一步或最关键的一步用 panel",
        "步骤竖排成 list，左边 key 写序号或步骤名，右边写说明；旁边一个 panel 写这套步骤的要点",
        "步骤横排用 topline 分栏，下面一句 note",
    ],
    "timeline": [
        "时间点横排成等宽的块，每块上方是日期（stat s），最近的一个用 panel",
        "时间点竖排成 list，key 写日期，右边写事件",
    ],
    "compare": [
        "左右两个等宽的大块，一个 panel 一个 card，各自列出要点",
        "左边 f2 的 card，右边 f3 的 panel（推荐的一方更大），中间不留分隔",
    ],
    "summary": [
        "每个结论一行：左边一个带底色的小块写标签，右边写结论（h）和依据（p muted）；最下面一句 note",
        "等宽的块横排，每块顶部是标签（pill），下面是结论和依据；建议那一块用 panel",
    ],
    "roadmap": [
        "阶段横排成等宽的块，每块里列出这个阶段要做的事，第一阶段用 panel",
        "一张表格：行是工作项，列是阶段，对应的阶段写上内容；下面一句 note",
    ],
    "statement": ["观点页：整页只有一句话结论和一行依据"],
}

SYSTEM_SUFFIX = "\n\n## 这套主题\n{label}：{mood}。\n"

REVIEW_SYSTEM = (
    "你是严格的幻灯片设计审稿人。看这一页的截图，只指出会让读者觉得不专业的明显问题："
    "文字被截断或重叠、某个块里大片空白、元素没有对齐、页面没有视觉重心、"
    "图形和标题说的不是一回事、内容挤在一角。不要挑措辞，不要提颜色和字体。"
    "没有明显问题就返回空列表。"
    '只输出 JSON：{"problems": ["每条不超过 40 个字，说清楚是哪里、该怎么改"]}，最多 3 条。'
)


def system_prompt(theme: themes.HtmlTheme) -> str:
    return themes.guide() + SYSTEM_SUFFIX.format(label=theme.label, mood=theme.mood)


def layout_idea(archetype: str, occurrence: int) -> str:
    ideas = LAYOUT_IDEAS.get(archetype) or LAYOUT_IDEAS["points"]
    return ideas[occurrence % len(ideas)]


def user_prompt(
    *,
    content: dict[str, Any],
    archetype: str,
    page_number: int,
    kicker: str | None,
    source: str | None,
    idea: str,
    previous_html: str | None = None,
    problems: list[str] | None = None,
) -> str:
    parts = [
        f"页码：{page_number:02d}",
        f"章节标签（放在 kicker）：{kicker or '（无，省略 kicker）'}",
        "资料来源（原样放进 foot 里的 source，一个字都不要改）："
        + (source or "（无，source 留空）"),
        "这一页的内容（已核对，数字和事实不能改，可以删减措辞，不能新增）：\n"
        + json.dumps(content, ensure_ascii=False, indent=1),
        f"建议的排布：{idea}",
        "同一套类名写出的示范页（只学写法，内容不要照抄）：\n"
        + themes.example(EXAMPLE_FOR.get(archetype, "columns")),
    ]
    if previous_html and problems:
        parts.append("你上一次写的 HTML：\n" + previous_html)
        parts.append(
            "它有这些问题，请改掉后重新输出完整的 HTML：\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )
    return "\n\n".join(parts)
