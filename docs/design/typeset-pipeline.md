# 第3b步：视觉系统接线

本分支从c9e6f3b创建，只调用visual/content.py、PROFILES、typeset_page；不修改visual内部。等真实生成和PowerPoint验证完成再评估合并，不合并main。

## 合同

- BuildRequest.layout_engine默认typeset，free保留旧坐标/主题/QA路径。style_profile为用户覆写；Brief.deck_type/reason由模型依据受众、目的、语气选择。确认页可改档案及引擎。
- typeset主题直接profile.theme，结构页暂用已有确定性封面/目录/章节页/结尾；新版结构页由视觉负责人另行实现。
- schema从content.py TypeAdapter动态读取；模型只输出原型+内容，无坐标。当前未实现原型建议降级到可用原型。chart数组长度在外围补校验。
- 模型内容并发，具体校验错误反馈重试一次；失败points回退并记录。所有内容完成后按页序调用typeset_page(history)；记录notes、构图、缩字、换构图、丢条目、排版失败。
- 内容页只检查内容问题（重复标题、有数无source），不进行旧几何QA或坐标修复；结构页保留已有QA。source是模型归因字段，本轮尚不宣称逐数字证据核验。
- 引擎不得在同一输出目录断点续跑时切换；对比必须新目录。档案切换重新排版，不重新生成已有内容。
- 修订只支持内容页重生成及四档案切换，能力外明确报错。当前内容进入实际prompt；整个修改在临时目录完成，退化/丢内容/门禁/渲染失败不写原稿，成功后提交产物，文件替换失败恢复原文件。

## 冻结验证

1. 离线回归：默认typeset、旧free、校验重试/回退、预算、顺序history、编辑事务、Web/CLI参数与缓存。
2. 真实模型预检：返回模型必须gpt-5.6-terra，记录官方token用量；并发使用ContextVar隔离响应计费，异常使用量未知保留预留，不计零收费。
3. $1.5本轮官方价硬上限：T3 20页consulting，T2 10页corporate；使用原固定prompt，不改原文分配或检索。
4. 两份PowerPoint实际打开并导出PDF，报告每页原型/构图/notes、所有失败及成本。不以规则通过宣称真实视觉完美。
5. 本地服务器用当前worktree源码、原项目data目录，加载既有.env，不复制或打印密钥，绑定127.0.0.1；检查health及Web页面再给浏览链接。

## 第一次真实验证（2026-10-06）

生成冻结版本5739164：T3 consulting 20页、T2 corporate 10页，两份均PowerPoint本地打印质量导出PDF，按官方价含预检合计$0.9595104，实际返回gpt-5.6-terra。两份内容校验失败/退化/删条目均0；T3目录有元素偏多warning。

全页PDF检查发现T3第8页`+18.8pp`内部换行覆盖标签。该视觉模块问题已保留输入和截图交接，未修改visual实现；不能将QA通过当作无视觉错误。本分支暂不合并。后续进度消息补丁不改变真实成品布局或模型内容。
