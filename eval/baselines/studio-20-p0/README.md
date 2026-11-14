# studio 20 页稳定基准（P0，2026-11-14 签收）

这是 P0 精修后被认可的 20 页。后续所有改动都拿它做对比，不再为了版式数量反复重做。

- 代码：标签 `studio-20-p0`（提交 `adbbd01`）。
- 成品：`run/generated_long_deck_v2.pptx`。PowerPoint 导出的总览：`docs/design/studio-20-refine-a.png`、`-b.png`。
- 逐页结论和已知不足：`eval/results/studio-20-refine-2026-11-14.md`。
- `run/` 是完整的运行目录（大纲、证据、内容、每页 HTML、版式规划），资料是 `eval/sources/caict_ai_2024.pdf`。

## 已签收的页面怎么被保护

`run/checkpoints/html/accepted.json` 列出了 16 个内容页。带这个文件续跑时，这些页面：

- 原样重放，不再按新规则复查，也不做局部修改；
- 不会被整套审查或"轮廓相近"规则重做；
- 版式规划保持不变。

只有当某页的**内容**变了（例如 P1 给它挂上了核对过的数据序列），它才会被重新设计——那已经是另一页了。

## 用它续跑

```bash
cp -R eval/baselines/studio-20-p0/run data/html-pages/<新目录>
```

```bash
uv run python data/html-pages/run_html.py data/html-pages/<新目录> studio 6 --no-review
```

要解除某页的签收，从 `accepted.json` 的 `pages` 里删掉它的页码。

## 留待单独处理的页面（审美精修，不阻塞 P1）

第 5 页（数据海报变成了账本）、第 7 和 15 页（表格没有视觉焦点）、第 14 页（偏空）、第 19 页（朱红 5 处）。
