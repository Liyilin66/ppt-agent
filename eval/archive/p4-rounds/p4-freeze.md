# P4 最终验证冻结（2026-11-19）

- 代码：标签 `p4-final-freeze`（本文件所在提交）。冻结后的任何修改都属于"修复后回归"，单独记录。
- 资料：CNNIC 第 57 次《中国互联网络发展状况统计报告》，cnnic.cn 官方 PDF（6,494,268 字节），
  存放在 `data/p4-source/cnnic_internet_57.pdf`，未参与任何规则调试。
- 运行配置：`data/html-pages/run_new.py` — layout_engine typeset，style_profile consulting，html_theme studio，
  html_review 关闭（设计审阅不参与），concurrency 4，qa_gate strict；新运行规则 bars_from_zero、type_roles 开启。
- 设计模型 gpt-5.6-terra（`.env`）；盲评模型 gpt-6.2-sol（`create_v2_review_client`）。
- 步骤：先只跑内容（不设计），看页数预算报告资料能支撑多少页；再生成连续的整套。
