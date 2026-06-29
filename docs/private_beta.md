# ppt-agent Private Beta Guide

本文档面向本地 private beta 测试者，说明如何启动 Web UI、填写字段、下载 artifacts，以及如何理解常见错误。

## 本地启动

安装依赖：

```bash
uv sync
```

设置服务端 OpenAI API key：

```bash
export OPENAI_API_KEY="..."
```

启动 FastAPI Web UI：

```bash
uv run uvicorn ppt_agent.api:app --reload
```

打开：

```text
http://127.0.0.1:8000
```

默认任务数据写入 `data/jobs/`，SQLite 元数据写入 `data/jobs.sqlite3`。

## Web UI 字段说明

- `主题`：PPT 的主标题或核心主题，例如 `AI 产品经理如何设计 Agent 产品`。
- `目标观众`：面向谁讲，例如 `准备进入 AI 产品岗位的 IT 硕士学生`。
- `页数`：当前支持 1-10 页。private beta 推荐 6-10 页。
- `最低 QA 分数`：生成结果达到该分数时视为 accepted。默认 80。
- `最大尝试次数`：QA 不达标时最多重试几轮。private beta 推荐先用 1，减少等待时间。
- `Patch 文件路径`：可选，必须是本地 `.json` patch 文件路径。为空时不执行 patch。
- `详细要求`：写清楚语言、风格、必须讲什么、不要讲什么、背景色或可编辑要求。

Patch path 默认应为空。只有要测试结构化局部修改时才填写，例如：

```text
examples/demo_ai_agent_pm/patches/sample_patch.json
```

## 产物在哪里

Web UI 任务完成后会在页面下方显示可下载 artifacts。

本地文件默认位于：

```text
data/jobs/<job_id>/
```

常见产物：

- `generated_deck_brief.json`：需求解析结果和 brief 来源。
- `generated_deck_plan.json`：DeckPlan、slide role、layout 建议和叙事顺序。
- `generated_deck_ir.json`：通过 Pydantic 校验的 Deck IR。
- `generated_qa_report.json`：QA 分数和 issue 列表。
- `generated_attempts.json`：生成尝试、QA 和 gate 结果。
- `generated_deck.pptx`：可编辑 PowerPoint。
- `patched_deck_ir.json`：应用 patch 后的 Deck IR。
- `patch_result.json`：patch 应用数量和问题。
- `patched_deck.pptx`：patch 后的可编辑 PowerPoint。

## QA failed 但 PPTX 已生成是什么意思

private beta 区分运行失败和 QA 未过：

- `succeeded / accepted=true`：PPTX 已生成，并达到最低 QA 分数。
- `succeeded / accepted=false`：PPTX 和 artifacts 已生成，但 QA 分数低于门槛。Web UI 会显示“已生成，但未通过 QA”。
- `failed`：运行时失败，例如 LLM timeout、schema validation failed、renderer error、patch 文件错误或 artifact 写入失败。

`accepted=false` 不代表没有 PPTX。它表示结果可下载、可检查，但系统认为内容或布局仍需要改进。

## patch_path 怎么用

Patch Edit 使用结构化 JSON，不是自然语言指令。当前支持：

- `update_text`
- `move_element`
- `resize_element`
- `update_shape_style`

示例：

```json
{
  "patch_id": "demo_update_subtitle",
  "operations": [
    {
      "op": "update_text",
      "slide_id": "slide_001",
      "element_id": "s1_subtitle",
      "text": "从需求到工作流：用边界、指标和风险控制设计可落地 Agent"
    }
  ]
}
```

Web UI 中填写 patch 路径后，pipeline 会先生成 deck，再把 patch 应用到 Deck IR，最后重新渲染 patch 后的 PPTX。

## 常见错误

### OPENAI_API_KEY is not set on the server

服务端环境变量没有设置 `OPENAI_API_KEY`。Web UI 不提供用户输入 API key 的能力。

### LLM timeout

某个 LLM 阶段超时。错误通常会说明阶段，例如：

```text
LLM call timed out in stage 'generate_deck' chunk 2/4 after 120 seconds.
```

可以减少页数、降低 `max_attempts`，或查看后端 stage 日志定位慢点。

### schema validation failed

LLM 输出不符合 `Deck` / `Slide` / `Element` Pydantic schema，例如多输出了 schema 不允许的字段，或 bbox 越界。

这属于生成失败，不应通过放宽 schema 解决。应修 prompt、normalization 或对应测试。

### QA gate not accepted

PPTX 已生成，但 QA 分数低于 `min_qa_score`。常见原因：

- 页面文字过多。
- 内容像报告摘要。
- layout 容量不匹配。
- 视觉模式重复。
- 风险矩阵或行动项结构不完整。

可以下载 `generated_qa_report.json` 查看具体 issue。

### patch file not found

`patch_path` 指向的文件不存在。确认路径相对于当前运行目录，或改用绝对路径。

### invalid patch json

Patch 文件不是合法 JSON，或不符合 `SlidePatch` schema。确认：

- 文件后缀是 `.json`。
- 顶层包含 `operations` 数组。
- 每个 operation 的 `op` 是当前支持的类型。
- `slide_id` 和 `element_id` 存在于目标 Deck IR。

## 当前 private beta 边界

- 当前不是商业 SaaS。
- 不支持登录、多租户、权限系统或云端托管。
- 不支持 RAG。
- 不支持 image-to-PPT。
- 不支持 30/50/100 页 batch generation。
- 不支持复杂品牌模板系统。
- 当前目标是 local private beta / portfolio demo。
