# 数字核对与联网证据

数字检查不调用模型：所有模型可见字段的数值要求出现在所引页/URL原文中，指标卡和图表还要求局部40字指标关键词和对应数值关联。原文尾零/千分位归一，亿万不能混，不能以31认证1。错配30.0%会议纪要/PPT回归拦截，29.7%通过。此为保守局部规则，不是完整语义蕴含证明；同义词可能导致拒绝。

失败传具体path/数字/label/reason重试一次，再失败删数字或整项；基数不足退化到定性statement，继续检查确保无遗留。缓存、排版回退也检查，run report记录blocked_attempts、passed_after_retry_pages、removed_items和removed_numbers。

本地出处显示文件名 第 X 页；网页显示domain · title。原始source URL和证据packet保留，不用显示格式替代校验。无来源显示空。

Web两处确认均有联网补充资料开关，无key置灰并提示.env TAVILY_API_KEY；capabilities只暴露boolean。搜索结果经EvidenceStore与本地文件共同索引，保留URL/title，未配置或无结果不假装完成联网。

真实验收T2只跑一次，10页corporate无文件、Tavily联网。总预算$1：模型$0.98上限，单basic搜索预留$0.02。保存每调用actualmodel/usage，搜索1次；搜索实际钱包扣费若API未返回不能伪称已核实。

## 实测结果

1670测试通过；一次真实联网T2企业10页成片、PowerPoint PDF完成。业务数字0，拦截/重试通过/删除均0，不能报告数字纠错提升。错配拦截由明确回归验证。模型官方费$0.240628，搜索预留$0.02，记账上限$0.260628/$1。key只写ignored.env，不进git；报告和截图在data/evaluation/evidence-check。
