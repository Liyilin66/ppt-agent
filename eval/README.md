# ppt-agent 基线评测（第 1 轮）

目的：用同一批输入比较 **当前版本 ppt-agent** 与 **Claude 网页版直接生成**，得到可复现的质量基线、评分表和失败案例。本轮只测量，不改代码。

## 冻结信息（运行前填写，填完不再改）

| 项 | 值 |
|---|---|
| ppt-agent commit | `________`（提交当前改动后填写） |
| ppt-agent 模型 / provider | `________` |
| Claude 网页版模型 | `________`（以网页显示为准） |
| 冻结日期 | 2026-10-__ |

## 任务

| 编号 | 文件 | 场景 | 输出 |
|---|---|---|---|
| T1 | [tasks/T1_training.md](tasks/T1_training.md) | 企业 AI 合规培训课件（短资料） | 10 页 |
| T2 | [tasks/T2_product.md](tasks/T2_product.md) | 产品方案（无资料） | 10 页 |
| T3 | [tasks/T3_long_report.md](tasks/T3_long_report.md) | 63 页行业报告 → 管理层汇报 | 20 页 |
| T4 | [tasks/T4_image_rebuild.md](tasks/T4_image_rebuild.md) | 5 张图片 → 可编辑页面 | 每张 1 页 |
| T5 | [tasks/T5_revision.md](tasks/T5_revision.md) | 在 T1 成品上做 3 次修改 | — |

T3 的标准答案：[facts/T3_facts.md](facts/T3_facts.md)（15 条，前/中/后各 5 条）。

## 公平规则

1. 两个系统拿到**完全相同**的资料和 prompt（各任务文件里的「固定 prompt」原样复制，不改字）。
2. ppt-agent：访谈中如被追问，只用任务文件「补充信息」一栏的内容回答；没有的就回答"按你的判断"。大纲**原样确认**，不手动修改。
3. Claude 网页版：允许回答一轮澄清，规则同上；要求输出可下载的 .pptx。新开对话，不带历史。
4. 两边**都不人工修改成品**（例外：第 4 天的"人工修改时间"测量，在副本上做）。
5. 运行次数：ppt-agent 的 T1–T4 各跑 2 次（run1/run2）；Claude 网页版每个任务 1 次。
6. 所有成品用 **PowerPoint 打开并导出 PDF**，评分以 PowerPoint 显示为准，不用 HTML 预览。

## 目录约定

```
eval/results/<system>/<task>/<run>/
    deck.pptx        成品
    deck.pdf         PowerPoint 导出
    slides/          逐页截图（可选）
    run.md           运行记录（模板见下）
```
`<system>` = `ppt-agent` 或 `claude-web`；`<run>` = `run1` / `run2`。

### run.md 模板

```
- 日期时间：
- 系统 / 模型：
- commit（ppt-agent）：
- 输入：任务编号 + 实际上传的文件
- 访谈/澄清问答原文：
- 费用（USD）：            首页出现耗时：        总耗时：
- 人工介入次数及内容：
- 异常/报错：
```

## 预注册预测（运行前写下，结果出来后对照，不改）

1. **T3 资料截断**：`src/ppt_agent/v2/intake.py` 把文档拼接后截断到 24,000 字符。用同一函数实测，本报告的截断点落在 **PDF 第 34 页**（全文约 55% 处）。预测：ppt-agent 的 T3 成品中，截断点之后的 7 条事实（M09、M10、B11–B15）**几乎不会正确出现**；若出现，应核查是否来自模型记忆或编造。
2. **T3 访谈阶段**：Web 对话附件摘要只保留前 2,400 字符（`api.py` `_attachment_digest_text`），访谈 Agent 只看到封面、目录和前言。预测：访谈追问与 Brief 不会体现报告正文中的具体数据。
3. ppt-agent 在可编辑性（原生文本/表格/图表占比）和长篇稳定性上优于对照组；在 10 页短篇的视觉评分上**不一定**占优。——这是待检验假设，不是结论。

## 回报节点

- **第 1 天结束**：任务文件、T3 事实清单（含抽查记录）、T4 五张图、T5 指令 → 交审核。审核通过再运行。
- **第 5 天结束**：`REPORT.md`、`scorecard.csv`、`failures.md`、双方最好/最差各 2–3 页截图 → 交审核。
