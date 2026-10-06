"""Real sample contents for the visual system (used by scripts and tests).

Every fact is taken from the evaluation sources: T1 from the CAC interim
measures (articles 4 and 18), T3 from CNNIC's 2025 report (printed pages 7-8).
T2 deliberately contains no numbers: the product proposal has no data source.
"""

from __future__ import annotations

from ppt_agent.v2.visual.archetypes import (
    ChartContent,
    Insight,
    PointItem,
    PointsContent,
    ProcessContent,
    StepItem,
)

# T1 — training: 《生成式人工智能服务管理暂行办法》第四条、第十八条
T1 = PointsContent(
    kicker="第 2 课 · 员工使用守则",
    title="使用 AI 办公工具时，守住三条线",
    lead="办法第四条对“提供和使用”生成式 AI 服务提出的要求，同样适用于员工日常使用。",
    items=[
        PointItem(
            heading="保守商业秘密",
            body="客户资料、报价和未公开的项目文件，不要粘贴进外部 AI 工具。",
            ref="依据：第四条第（三）项",
        ),
        PointItem(
            heading="尊重他人权益",
            body="处理含他人照片、姓名或联系方式的内容前，先确认已获授权。",
            ref="依据：第四条第（四）项",
        ),
        PointItem(
            heading="核实后再使用",
            body="AI 给出的条款、数字和结论，引用前先对照原始资料核实。",
            ref="依据：第四条第（五）项",
        ),
    ],
    takeaway="发现 AI 服务不合规，使用者有权向有关主管部门投诉、举报（第十八条）。",
    source="来源：《生成式人工智能服务管理暂行办法》（2023 年 8 月 15 日起施行）",
)

# T2 — product proposal: 项目现场知识问答助手（无外部数据，不写数字）
T2 = ProcessContent(
    kicker="核心流程",
    title="一次提问，四步给出可追溯的答案",
    lead="助手不替代专业判断：它负责快速找到依据，并把依据和答案一起交给现场人员。",
    steps=[
        StepItem(label="现场提问", body="语音或文字描述问题，自动带上项目和工序信息。"),
        StepItem(label="检索依据", body="在规范条文、公司制度和历史项目经验中查找相关段落。"),
        StepItem(label="生成回答", body="回答附带条文出处和版本；查不到依据时明确说明。"),
        StepItem(label="人工确认", body="涉及安全和质量的高风险问题，转交专业负责人确认。"),
    ],
    takeaway="设计原则：每个答案都能追溯到原文，查不到就说查不到。",
)

# T3 — long report: CNNIC《生成式人工智能应用发展报告（2025）》第 7–8 页
T3 = ChartContent(
    kicker="用户普及",
    title="生成式 AI 普及率半年翻倍，豆包和 DeepSeek 领跑",
    chart="bar",
    chart_title="各主要产品在生成式 AI 用户中的使用率",
    categories=["豆包", "DeepSeek", "腾讯元宝", "Kimi", "文心一言"],
    values=[72.2, 62.0, 16.5, 16.4, 12.2],
    unit_format='0.0"%"',
    insights=[
        Insight(value="5.15 亿", text="用户规模（2025 年 6 月），较 2024 年 12 月增长 2.66 亿"),
        Insight(value="36.5%", text="普及率，半年提升 18.8 个百分点"),
        Insight(value="47.1%", text="的用户首选豆包，34.0% 首选 DeepSeek"),
    ],
    source="来源：CNNIC《生成式人工智能应用发展报告（2025）》第 7–8 页",
)

DECK_TITLES = {
    "consulting": "中国生成式 AI 应用发展：管理层简报",
    "launch": "项目现场知识问答助手",
    "training": "生成式 AI 合规使用培训",
    "corporate": "AI 办公助手推广季度汇报",
}

SAMPLES = [T1, T2, T3]


# --------------------------------------------------------------------------
# An 8-page management briefing from CNNIC《生成式人工智能应用发展报告（2025）》.
# Printed page numbers are cited on every slide; nothing is invented.
# --------------------------------------------------------------------------

_SRC = "来源：CNNIC《生成式人工智能应用发展报告（2025）》"

from ppt_agent.v2.visual.archetypes import (  # noqa: E402
    CompareContent,
    CompareSide,
    MetricItem,
    MetricsContent,
    Milestone,
    StatementContent,
    TimelineContent,
)

CNNIC_DECK = [
    StatementContent(
        title="核心判断",
        statement="生成式 AI 已进入大众市场，竞争重心正转向应用与成本",
        support="截至 2025 年 6 月，用户规模 5.15 亿、普及率 36.5%；"
        "GPT-3.5 水平的推理成本在两年内下降超过 280 倍。",
        source=f"{_SRC}第 1、4 页",
    ),
    ProcessContent(
        kicker="报告框架",
        title="从四个层面看生成式 AI 的发展",
        steps=[
            StepItem(label="用户普及", body="用户规模、属性结构，以及未成年人的认知和使用"),
            StepItem(label="产业发展", body="产业概况与生成式 AI 服务备案情况"),
            StepItem(label="典型应用", body="农业生产、工业制造、生活服务、科学研究"),
            StepItem(label="发展环境", body="政策、技术、融资与国际竞争环境"),
        ],
        source=f"{_SRC}前言、目录",
    ),
    MetricsContent(
        kicker="用户普及",
        title="半年新增 2.66 亿用户，普及率翻倍",
        metrics=[
            MetricItem(value="5.15 亿", label="用户规模", note="截至 2025 年 6 月"),
            MetricItem(value="36.5%", label="普及率", note="较 2024 年 12 月提升 18.8 个百分点"),
            MetricItem(value="538 款", label="完成备案的服务", note="截至 2025 年 8 月底"),
            MetricItem(value="263 款", label="完成登记的应用或功能", note="截至 2025 年 8 月底"),
        ],
        source=f"{_SRC}第 1、7 页",
    ),
    T3,
    ChartContent(
        kicker="使用场景",
        title="回答问题最普遍，近三成用户用 AI 做会议纪要和 PPT",
        chart="bar",
        chart_title="用户使用生成式 AI 产品的目的",
        categories=["回答问题", "生成、处理文本", "生成图片、视频", "作为生活助手",
                    "生成会议纪要、PPT", "休闲娱乐", "帮助写代码"],
        values=[80.9, 36.0, 33.0, 30.0, 29.7, 23.6, 10.3],
        unit_format='0.0"%"',
        insights=[
            Insight(value="80.9%", text="的用户用生成式 AI 回答问题"),
            Insight(value="29.7%", text="用于生成会议纪要、PPT，办公场景已成规模"),
            Insight(value="10.3%", text="用于帮助写代码"),
        ],
        source=f"{_SRC}第 9 页",
    ),
    PointsContent(
        kicker="技术趋势",
        title="四个技术变化正在降低应用门槛",
        items=[
            PointItem(heading="逻辑推理显著提升", body="模型从生成内容走向处理复杂任务，多轮对话、代码和推理表现更好。",
                      ref="第 2 页"),
            PointItem(heading="多模态跨越式发展", body="文本、图像、音频、视频可以任意组合输入和输出，交互更自然。",
                      ref="第 3 页"),
            PointItem(heading="推理成本显著降低", body="GPT-3.5 水平的系统推理成本在两年内下降超过 280 倍。",
                      ref="第 4 页"),
            PointItem(heading="轻量模型走向终端", body="剪枝、量化、蒸馏把推理时间压缩到毫秒级，手机等设备也能运行。",
                      ref="第 5–6 页"),
        ],
        source=f"{_SRC}第 2–6 页",
    ),
    CompareContent(
        kicker="技术路线",
        title="轻量模型补上主力模型的成本和部署短板",
        left=CompareSide(heading="主力模型", points=[
            "性能强，但计算成本高昂",
            "千亿参数单次推理耗时长，难满足客服等即时场景",
            "参数规模大，难在手机、物联网设备上实时运行",
        ]),
        right=CompareSide(heading="轻量模型", points=[
            "低成本、易部署",
            "剪枝、量化、蒸馏把推理时间压缩到毫秒级",
            "推动生成式 AI 从云端走向终端",
        ]),
        takeaway="例：子曰 3 数学模型只需单块消费级 GPU 就能运行。",
        source=f"{_SRC}第 5–6 页",
    ),
    TimelineContent(
        kicker="产品里程碑",
        title="一年内，视频、推理和搜索相继迎来标志性产品",
        milestones=[
            Milestone(date="2024.12", label="Sora 开放", body="OpenAI 视频生成模型正式向用户开放"),
            Milestone(date="2025.01", label="DeepSeek-R1", body="成本不到同类模型的十分之一"),
            Milestone(date="2025.05", label="谷歌 AI 模式", body="基于 Gemini 2.5 的搜索模式正式发布"),
            Milestone(date="2025.08", label="GPT-5 发布", body="统一推理能力与快速响应"),
        ],
        source=f"{_SRC}第 2、25、27、51 页",
    ),
    ChartContent(
        kicker="融资环境",
        title="机器人领域最受资本青睐，前四个月发生 362 起投融资",
        chart="bar",
        chart_title="2025 年 1–4 月 AI 投融资事件数量占比（按领域）",
        categories=["机器人", "传统行业应用", "硬件与技术", "人工智能自身应用", "自动驾驶"],
        values=[38.7, 23.5, 18.2, 15.5, 4.1],
        unit_format='0.0"%"',
        insights=[
            Insight(value="362 起", text="2025 年前四个月 AI 相关投融资事件"),
            Insight(value="403.9 亿元", text="合计金额"),
            Insight(value="38.2%", text="的投资金额集中在北京"),
        ],
        source=f"{_SRC}第 40–41 页",
    ),
    PointsContent(
        kicker="国际环境",
        title="主要经济体都在加码 AI 投入",
        items=[
            PointItem(heading="欧盟", body="“投资人工智能”倡议拟调动 2000 亿欧元，并计划建设人工智能超级工厂。",
                      ref="第 45–46 页"),
            PointItem(heading="英国", body="公布“人工智能机遇行动计划”，未来 10 年将公共算力提高 20 倍。",
                      ref="第 47 页"),
            PointItem(heading="印度", body="2024 年 3 月批准 1037.1 亿卢比（约 12.5 亿美元）AI 专项拨款。",
                      ref="第 47 页"),
            PointItem(heading="韩国", body="2025 年 5 月通过 1.1 万亿韩元 AI 追加预算，用于采购 1 万块先进 GPU 等。",
                      ref="第 50 页"),
        ],
        source=f"{_SRC}第 45–50 页",
    ),
]

CNNIC_DECK_TITLE = "中国生成式 AI 应用发展：管理层简报"
