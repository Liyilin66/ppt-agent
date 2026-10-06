# 失败与问题记录

每条：现象 → 复现步骤 → 证据（截图/文件路径） → 初步原因 → 处理（修复 / 降级 / 记录不修）。
至少挑一条写完整，作为面试用的失败案例。

## 评测前已知

### K1 图标风格不统一（视觉）
- 现象：同一页中彩色 emoji 图标（📚、📊）与单色符号（✓）混用；跨平台渲染不一致（Windows 字体不同）。
- 证据：第 0 步 PowerPoint 导出截图（data/step0/，未入库）。
- 处理：记录不修，本轮评测中统计出现频率。

### K2 资料截断（预注册，待 T3 验证）
- `src/ppt_agent/v2/intake.py` `MAX_DIGEST_CHARS = 24_000`：T3 报告截断在 PDF 第 34 页，后 45% 正文不进入生成。
- 见 README「预注册预测」第 1 条。

### K3 访谈阶段只看到附件前 2,400 字符（预注册，待验证）
- `src/ppt_agent/api.py` `_attachment_digest_text` 返回 `digest[:2400]`；正式生成会重新读取文件（受 K2 限制）。

## 评测中发现

（按 E1、E2… 编号追加）

### E1 规则 QA 通过，但 PowerPoint 中流程框遮挡正文（记录不修）
- 版本：`feat/evidence-grounding`，commit `a85fb21664cf6eb36f49844d668608b5a235b949`。
- 任务与现象：T2 新分支成品第 9 页，左栏下方的流程框覆盖第三条正文。导出成功和规则 QA 通过不能证明真实渲染无重叠。
- 复现：在 PowerPoint 打开 `data/evaluation/evidence-grounding/gpt-5.6-luna/candidate/T2.pptx`，查看第 9 页；或查看 PowerPoint 本地导出的同目录 `T2.pdf` 第 9 页。
- 证据：`data/evaluation/evidence-grounding/gpt-5.6-luna/candidate/slide-09.png`；对应设计 IR 和 QA 报告在同目录，实验汇总见 `data/evaluation/evidence-grounding/REPORT.md`。
- 初步原因：规则 QA 对文本与非文本流程框的遮挡未覆盖充分。尚未完成根因定位，不将此判断当作已证实原因。
- 处理：按用户要求仅记录，现在不修改排版或 QA。

### E2 T2 正文页面退回确定性版式（统一评测待统计）
- 版本：基线 `89e0efb9f823d84dd38f9a40d785c350b1a667a8`；新分支 `a85fb21664cf6eb36f49844d668608b5a235b949`。
- 现象：基线正文第 5、7 页因未解决的文本溢出进入 fallback；新分支正文第 5 页因未解决的文本重叠进入 fallback。另有结构页回到预置版式，须与正文 fallback 分开计数。
- 证据：两份 `data/evaluation/evidence-grounding/gpt-5.6-luna/{baseline,candidate}/T2_run_report.json`、同目录 checkpoint 和生成日志。
- 说明：上述正文 fallback 的直接触发是版式 QA，不应全部归因于模型调用失败；新分支第 9 页另外发生过请求重试失败，之后继续生成。
- 处理：记录不修；统一评测分别记录回退页码、触发原因、可见质量和内容完整性。

### E3 未支持的 layout_hint 使整章页面脚本回退（记录不修）
- A 组章节 5 返回 `three_column`，校验不接受该值，整章使用大纲要点回退。证据：`data/evaluation/long-input/runs/run1/A/calls.json`、`plan.json` 与 `checkpoints/skeleton_with_briefs.json`。
- 本问题发生在页面脚本规划阶段；E2 的 T2 回退由页面设计 QA 触发，不能当作同一个原因。
- 处理：保留本次实验输出，不修改生产逻辑；实验结束后再评估未知提示降级为 auto。

### E4 管理层汇报的结构页占比高（记录不修）
- 小资料四组均规划 20 页，其中封面、目录、6 张章节分隔页、结尾共 9 页（45%），内容页 11 页。
- 证据：`data/evaluation/long-input/runs/run1/{A,B,C,D}/plan.json`。此为产品现象；不能据此证明金标准事实缺失的原因，B 同样的内容页数仍包含 39 个去重数字指标。

### E5 放大全文大纲被当前网关在约 125 秒切断（部署约束）
- B_large 非流式与后续 SSE 尝试均返回 HTTP 524；第一回 125.085 秒，客户端超时设为 300 秒。
- 证据：`data/evaluation/long-input/runs/run1/B_large/calls.json`、`B_large_stream/calls.json`、`recovery.json`。
- 使用量未知，保留预算预留上限；不计事实召回 0 分，不再重试。此结果证明当前 CCCX 接口的部署限制，不能推广为所有模型或所有代理都无法处理全文。

### E6 数字正确但统计指标配错（B 组）
- 第 17 页 points[1] 正确区分机器人融资事件占比 38.7% 与金额占比 35.5%；data_idea 却把 38.7% 配为金额占比。同一指标跨字段冲突按一个错误计。
- 证据：`data/evaluation/long-input/audit/B_manual.json`。39 个去重指标中 38 个正确，没有新增无出处数值；无新数字不能证明所有数字陈述正确。

### E7 按章分配命中错误源章，后段事实由大纲传递（C 组）
- 应用章和环境章都匹配到报告第三章；第 17 页专利与融资数据由 Talking points 支持，当前章节原文不包含这些事实。
- 证据：`data/evaluation/long-input/runs/run1/C/calls/05-request.json` 与 `scripts.json`。
- 处理：如实记录；不能把后段召回归因于按章分配，也不能直接把当前匹配算法接进主流程。

### E8 放大检索流程能跑通，但出现回退和结构页增加（记录不修）
- 补跑证据：D_large_production_prefix 第 5、6 章分别因 `four_column` 校验失败回退，成品脚本第 12、13、15 页只保留大纲标题/摘要，正文 points 为空。见 `data/evaluation/long-input/supplement.log`、对应 `scripts.json`。这进一步说明整章回退是独立于网络超时的内容损失路径。
- 补跑页数：D_large 8 个章节使结构页达到 11/20（55%），内容页只剩 9 页。比较小资料 D 与 D_large 时须同时披露前缀流程和页数分配变化。

## 第2a步后续修复（2026-10-06，不修改原实验记录）
- commit `dacdeab`：修复E3/E8中未知layout_hint导致整章脚本退化，未知栏布局归一化，其他校验错误按单页回退并记录run report。
- 修复E4/E8结构页比例：20页6章变为3结构+17内容，100页8章保留11结构+89内容；<12页固定3结构是明确数学例外。
- 595测试通过，20页mock已在PowerPoint检查；证据见 `data/evaluation/step2a/REPORT.md` 和截图。无付费调用；E1/E2设计几何问题、E7章节匹配问题不在本次修复范围。
