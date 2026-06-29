# Release Checklist

在提交或发布 `ppt-agent` 的 demo / private beta 版本前，至少完成下面的检查。

## 1. Dependency and test checks

```bash
uv sync
uv lock --check
uv run pytest
git diff --check
```

## 2. Sensitive info and local-path scan

确认 README、docs、examples 里没有本机绝对路径或真实敏感信息：

```bash
rg -n "/Users/[^/]+|[A-Za-z]:\\\\Users\\\\" README.md docs examples
rg -n --glob '!tests/**' "sk-[A-Za-z0-9_-]{16,}|BEGIN [A-Z ]*PRIVATE KEY" .
```

`OPENAI_API_KEY` 占位符示例可以保留，但不要提交真实 key。

## 3. Demo artifact checks

- verify demo screenshots exist
- verify example PPTX opens
- verify patch demo report exists
- 确认 `examples/demo_ai_agent_pm/screenshots/` 下的截图存在
- 确认 `examples/demo_ai_agent_pm/patches/screenshots/patch_before_after.png` 存在
- 确认 `examples/demo_ai_agent_pm/generated_deck.pptx` 可以正常打开
- 确认 `examples/demo_ai_agent_pm/patched_deck.pptx` 可以正常打开
- 确认 `examples/demo_ai_agent_pm/patch_report.json` 存在

可选地重新生成 README 预览图：

```bash
uv run python scripts/generate_demo_screenshots.py --include-patch-demo
```

## 4. Quickstart smoke checks

- `uv run uvicorn ppt_agent.api:app --reload`
- 打开 `http://127.0.0.1:8000`
- 确认 Web UI 首页能加载
- 确认 `uv run ppt-agent --help` 列出 `generate`、`build`、`render`、`qa`、`patch`

## 5. Local runtime cleanup

- `git status --short`
- 确认没有把 `data/jobs.sqlite3`、`data/jobs/` 或其他本地 job artifacts staged 进 commit
- 确认 `examples/demo_ai_agent_pm/output/` 这类本地重跑产物没有进入 commit
