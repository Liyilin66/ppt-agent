# Patch Edit Demo

这个目录展示 `ppt-agent` 的结构化局部修改能力。Patch Edit 使用 JSON patch 操作修改 Deck IR，然后重新渲染 PPTX；它不是自然语言直接改 PPT，也不会重新生成整份 deck。

## sample_patch.json 做了什么

`sample_patch.json` 使用现有 Patch schema 的 `update_text` 操作：

- 目标 slide：`slide_001`
- 目标 element：`s1_subtitle`
- 修改内容：把封面副标题改成更适合技术产品分享的表达

修改前：

```text
核心不是包装能力，而是定义可交付的技术边界与责任边界
```

修改后：

```text
从需求到工作流：用边界、指标和风险控制设计可落地 Agent
```

## CLI 示例

```bash
uv run ppt-agent patch examples/demo_ai_agent_pm/generated_deck_ir.json \
  --patch examples/demo_ai_agent_pm/patches/sample_patch.json \
  --output examples/demo_ai_agent_pm/patched_deck_ir.json \
  --result-output examples/demo_ai_agent_pm/patch_report.json

uv run ppt-agent render examples/demo_ai_agent_pm/patched_deck_ir.json \
  --theme examples/theme.json \
  --output examples/demo_ai_agent_pm/patched_deck.pptx
```

也可以在 `build` 命令中直接传入 patch：

```bash
uv run ppt-agent build \
  --topic "AI 产品经理如何设计 Agent 产品" \
  --audience "准备进入 AI 产品岗位的 IT 硕士学生" \
  --slides 8 \
  --language zh-CN \
  --theme examples/theme.json \
  --output-dir examples/demo_ai_agent_pm/output \
  --requirements "中文技术产品分享，重点讲技术边界、用户需求分析、工作流设计、评估指标和落地风险。PPT 必须可编辑。" \
  --patch examples/demo_ai_agent_pm/patches/sample_patch.json
```
