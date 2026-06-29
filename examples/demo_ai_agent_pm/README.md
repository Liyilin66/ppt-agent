# AI Agent 产品经理官方 Demo

这个目录是 `ppt-agent` 的固定 private-beta demo，用来展示一个可复现的中文技术产品分享 PPT 生成流程。

## 输入

固定输入保存在 `input.json`：

- topic：`AI 产品经理如何设计 Agent 产品`
- audience：`准备进入 AI 产品岗位的 IT 硕士学生`
- slide_count：`8`
- language：`zh-CN`
- requirements：中文技术产品分享，强调技术边界、用户需求分析、工作流设计、评估指标、落地风险和可编辑 PPTX

## 已包含的真实产物

本目录包含一组来自本地 private-beta run 的真实 artifacts，没有手写或伪造生成结果：

- `generated_deck_brief.json`
- `generated_deck_plan.json`
- `generated_deck_ir.json`
- `patchable_elements.json`
- `generated_qa_report.json`
- `generated_attempts.json`
- `generated_deck.pptx`
- `patch_report.json`
- `patched_deck.pptx`

这些产物用于 GitHub / 简历 / private beta 演示。由于 LLM 输出可能随模型版本变化，重新运行同一输入不保证字词完全一致；但本项目的 DeckBrief 快速路径、DeckPlan、QA、renderer 和 patch 逻辑都保持可复现、可检查。

## 重新生成

需要服务端环境变量中有 `OPENAI_API_KEY`：

```bash
export OPENAI_API_KEY="..."

uv run ppt-agent build \
  --topic "AI 产品经理如何设计 Agent 产品" \
  --audience "准备进入 AI 产品岗位的 IT 硕士学生" \
  --slides 8 \
  --language zh-CN \
  --theme examples/theme.json \
  --output-dir examples/demo_ai_agent_pm/output \
  --requirements "中文技术产品分享，面向准备进入 AI 产品岗位的 IT 硕士学生，重点讲 AI Agent 产品经理需要理解的技术边界、用户需求分析、工作流设计、评估指标和落地风险。风格像技术产品分享，不像营销材料。每页有明确观点，少用空泛口号。背景色极淡蓝绿色。PPT 必须可编辑。" \
  --min-qa-score 80 \
  --max-attempts 1
```

如果 QA 分数未达到门槛，`build` 仍会输出可检查的 JSON 和 PPTX artifacts，并返回非零状态码。Web UI 会显示“已生成，但未通过 QA”，这和 runtime failed 不同。

## Patch demo

`patches/sample_patch.json` 演示如何只修改封面副标题，而不是重新生成整份 PPT。

如果需要自己写 patch，先看 `patchable_elements.json`。它列出了每个 slide 的可 patch 元素、文本预览和支持的操作。

详见 `patches/README.md`。
