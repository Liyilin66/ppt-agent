"""Prompts for the design step: verified content in, one slide of HTML out."""

from __future__ import annotations

import json
from typing import Any

from ppt_agent.v2.figures import item_text
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
    guide = themes.studio_guide() if theme.vocabulary == 2 else themes.guide()
    return guide + SYSTEM_SUFFIX.format(label=theme.label, mood=theme.mood)


def studio_idea(form: str, focus: str, ratio: str, *, core_visual: str = "", reading_path: str = "") -> str:
    """The composition this page was assigned by the deck-wide plan, in words."""

    side = "左" if focus == "left" else "右"
    width = {"even": "主要视觉和其余内容大致各占一半", "wide": "主要视觉占大约三分之二的宽度",
             "narrow": "主要视觉只占大约三分之一的宽度，其余留给文字"}[ratio]
    parts = [f"{form}——{themes.STUDIO_FORMS[form]}。", f"主要视觉放在{side}侧；{width}。"]
    if core_visual:
        parts.append(f"读者第一眼应该看到：{core_visual}。")
    if reading_path:
        parts.append(f"视线顺序：{reading_path}。")
    parts.append("示范页是同一种形式，但左右和比例可能不同：学它的写法，按上面的要求重新安排网格。")
    return "".join(parts)


def layout_idea(archetype: str, occurrence: int) -> str:
    ideas = LAYOUT_IDEAS.get(archetype) or LAYOUT_IDEAS["points"]
    return ideas[occurrence % len(ideas)]


def _chart_brief(content: dict[str, Any], idea: str) -> list[str]:
    """The chart this page draws, written out so the model only has to place it."""

    from ppt_agent.v2.htmldeck import charts

    series = charts.chartable(content)
    if series is None:
        return []
    if not idea.startswith("chart"):
        # Evidence for a claim about another measure: present, and second to the page's own figure.
        return [
            "这一页还要放一张小图表作为论据（数据已逐个核对过原文）。它不是主角：页面第一眼仍然是上面说的焦点，"
            "图表放在次要位置，占 4 到 5 列、3 到 4 行。把下面两行原样放进同一个区域，data-chart 里一个字符都不要改，"
            "图注用类名 c，写在图表的正上方：\n"
            f'<div class="c">{charts.caption(series)}</div>\n'
            f"<div class=\"chart\" data-chart='{charts.attr(series)}'></div>\n"
            "图表已经画出了每个数值，文字不要再逐个重复。"
        ]
    return [
        "这一页要画的图表（数据已逐个核对过原文）。把下面两行原样放进页面，data-chart 里一个字符都不要改，"
        "图注用类名 c，写在图表的正上方；图表区域至少占 6 列、4 行：\n"
        f'<div class="c">{charts.caption(series)}</div>\n'
        f"<div class=\"chart\" data-chart='{charts.attr(series)}'></div>\n"
        "图表已经画出了每个数值，旁边的文字不要再逐个重复这些数，只写它说明了什么。"
    ]


def _visual_brief(content: dict[str, Any]) -> list[str]:
    visual = content.get("visual")
    if not visual:
        return []
    role = ("它就是这一页的主视觉，其余内容围绕它。" if visual.get("role") == "primary"
            else "它只是论据，不是主角：页面的第一眼必须是下面说的焦点。")
    return [f"这一页的论点：{visual.get('claim')}\n核心指标：{visual.get('core_metric')}\n"
            f"读者第一眼应该看到：{visual.get('focus') or visual.get('core_metric')}\n这一页的图：{role}"]


def diagram_words(diagram: dict[str, Any]) -> list[str]:
    """Every word of a redrawn figure that the page must carry."""

    words = [level["name"] for level in diagram.get("levels") or []]
    for level in diagram.get("levels") or []:
        for item in level["items"]:
            # A titled box is carried whole, with what it holds inside its brackets.
            words.append(item_text(item))
    words += [group["name"] for group in diagram.get("groups") or []]
    return words + list(diagram.get("steps") or [])


def _diagram_brief(content: dict[str, Any]) -> list[str]:
    diagram = content.get("diagram")
    if not diagram or diagram.get("kind") != "levels":
        return []
    lines = [f"第 {index} 层（自上而下）：{level['name']}"
             + (f"——{'、'.join(item_text(item) for item in level['items'])}" if level["items"] else "")
             for index, level in enumerate(diagram["levels"], 1)]
    groups = [f"「{group['name']}」指第 {'、'.join(str(number) for number in group['covers'])} 层"
              for group in diagram.get("groups") or []]
    return [
        "这一页的主视觉是按资料原图重画的层级图。用 layers 元件画，层的顺序、层名和每层的条目都必须和下面完全一样，"
        "一个词都不要改、不要增删、不要合并两层；每层的条目用顿号隔开写在这一层里，带括号的条目连同括号里的内容原样照抄"
        "（括号表示原图里这几项框在这个标题下面）：\n" + "\n".join(lines)
        + ("\n原图还标了分组，要在对应的层里用小字（类名 c 或 k）标出来：" + "；".join(groups) if groups else "")
        + f"\n层级图下面要有一行图注（类名 c），原样写：{diagram['caption']}"
    ]


def _figure_brief(content: dict[str, Any], idea: str) -> list[str]:
    figure = content.get("figure")
    if not figure or not idea.startswith("figure"):
        return []
    return [
        "这一页要放资料里的一张原图。把下面两行原样放进同一个区域（先图后图注），属性一个字符都不要改；"
        "这个区域至少占 6 列、5 行，里面不要再放别的东西：\n"
        f'<div class="plate" data-figure="{figure["id"]}" data-ratio="{figure["ratio"]}"></div>\n'
        f'<div class="c">{figure["caption"]}</div>\n'
        "图里已经有的数据不要再抄一遍；旁边的文字说这张图说明了什么。"
    ]


def user_prompt(
    *,
    content: dict[str, Any],
    archetype: str,
    page_number: int,
    kicker: str | None,
    source: str | None,
    idea: str,
    example_html: str | None = None,
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
        *_visual_brief(content),
        *_chart_brief(content, idea),
        *_diagram_brief(content),
        *_figure_brief(content, idea),
        f"建议的排布：{idea}",
        "同一套类名写出的示范页（只学写法，内容不要照抄）：\n"
        + (example_html or themes.example(EXAMPLE_FOR.get(archetype, "columns"))),
    ]
    if previous_html and problems:
        parts.append("你上一次写的 HTML：\n" + previous_html)
        parts.append(
            "它有这些问题，请改掉后重新输出完整的 HTML：\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )
    return "\n\n".join(parts)
