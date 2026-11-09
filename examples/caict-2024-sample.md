# 样例：信通院《人工智能发展报告（2024年）》→ 20 页管理层汇报

[caict-2024-sample.pptx](caict-2024-sample.pptx) 由 ppt-agent 生成，未经人工修改。

- 资料：中国信息通信研究院《人工智能发展报告（2024年）》，64 页，[官方 PDF](https://www.caict.ac.cn/kxyj/qwfb/bps/202412/P020241210548865982463.pdf)（PDF 不随仓库分发）。
- 参数：20 页，typeset 引擎，consulting 风格，只用资料、不联网，模型 `gpt-5.6-terra`。
- 生成方式：一次完整生成（约 $1.87）后，用当前排版代码重新排版，并单独重写了第 5 页（约 $0.17），因为原来的第 5 页只剩一句和章节名相同的话。整份生成中有两页退化（第 5、18 页）；第 18 页保留原样。
- 仍有的弱点：第 15 页的图表把成本降幅、准确率和周期缩短放在一起，它们并不是同一个指标；部分页面内容偏概括。

这份报告是泛化检验用的「没见过的资料」：检验过程、发现的问题和修复见 [eval/results/generalization-2026-10-07.md](../eval/results/generalization-2026-10-07.md)。同一需求与 Claude Code、Codex 的对比见 [eval/results/compare-2026-11-10.md](../eval/results/compare-2026-11-10.md)。
