你在设计一页 16:9 的幻灯片（1280×720 像素）。只输出这一页的 HTML，不要解释，不要 Markdown 代码块。

## 规则
- 只用下面列出的类名。不要写 <style>、<script>、图片、SVG；不要写颜色、字号、字体。
- 内联 style 只能写布局：display、flex、gap、width、height、margin、padding、text-align。
- 页面上的每个数字都必须来自给你的内容，不要新增、推算或改写数字。
- 内容装不下时删减文字，不要缩小字号。一页只突出一个重点。
- 内容区要撑满：主要的块用 f1 / f2 / f3 分配宽度，让它们占满高度。

## 内容页骨架
<div class="page">
  <div class="head"><div class="kicker">章节</div><h1 class="title">结论式标题</h1><p class="lead">可选的一句话</p></div>
  <div class="body">……这里自由排布……</div>
  <div class="foot"><div class="source">资料来源：……</div><div class="pageno">页码</div></div>
</div>
body 默认横向排列；加 col 变成纵向。

## 排布
row（横排）、col（竖排）、f1 f2 f3（按 1:2:3 分配空间）、fit（按内容大小）
center between end（主轴对齐）、middle baseline stretch（交叉轴对齐）、gap-s gap-l

## 色块
panel（最重的深色块，一页最多两个）、card（浅色块）、outline（只有边框）、accent（强调色块，少用）
topline（顶部一条线的分栏；加 dim 变成灰线）、sideline（左侧竖线）、note（页面底部的一句结论）、rule（分隔线）

## 文字
stat（大数字；xl 特大、l 大、s 小）、h（小标题；l 大一号）、p（正文）、small（小字）、label（说明性小字）
muted（灰色）、em（强调色）、bold、pill（标签；ghost 为描边）、num（序号，如 01）

## 成组的事实
<div class="list"><div class="item"><div class="key">80%+</div><div class="p muted">说明</div></div>……</div>
key 加 w 可以放更宽的数字（如「316 亿美元」）。

## 数据
- 图表（会变成可编辑的原生图表）：
  <div class="chart" data-chart='{"kind":"column","categories":["A","B"],"series":[{"name":"名称","values":[1,2]}],"highlight":1,"format":"0.0\"%\""}'></div>
  kind 可选 column、bar、line、pie、doughnut。只有同一指标的多个取值才能画在一张图里；不同指标不要放在一起比。图表放在 card 或 f2/f3 的块里。
- 进度条：<div class="bar"><div class="fill" style="width:70%"></div></div>，宽度必须等于给定的百分比。
- 表格：标准 <table>，第一行用 <th>，最多 8 列 12 行。

## 整页类型
封面：<div class="page cover"><div class="deco"></div><div class="deco2"></div><div class="deco3"></div><div class="kicker">…</div><div class="cover-main"><h1 class="cover-title">…</h1><p class="cover-sub">…</p></div><div class="cover-aside">…目录…</div></div>
章节页：<div class="page section"><div class="deco"></div><div class="deco2"></div><div class="section-main"><div class="section-no">02</div><h1 class="section-title">…</h1><p class="section-sub">…</p></div></div>
观点页：<div class="page statement"><div class="deco"></div><div class="statement-main"><div class="kicker">…</div><div class="quote">一句话结论</div><p class="lead">…</p></div></div>

## 怎么选排布
- 有一个最重要的数字：左边 panel 里放 stat xl，右边 list 放 2 到 3 个支撑事实。
- 同一指标的几个取值：左边图表，右边解读。
- 三四个并列的点：等宽的 card / panel 横排，把结论不同的那个做成 panel。
- 先后步骤：横排的块加序号。
- 多行多列的对照：表格，下面加一句 note。
- 两种情况对比：两个块，一个 panel 一个 card。
不要每页都用等宽卡片。图形要表达标题里的结论。
