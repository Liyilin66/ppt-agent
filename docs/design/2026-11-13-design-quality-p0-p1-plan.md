# 设计质量 P0 / P1 实施计划（studio 主题，信通院 20 页）

> **给执行者：** 按任务顺序逐个执行，每个任务先写失败的测试、再实现、再跑测试、再提交。步骤用 `- [ ]` 勾选。
> 建议配合 superpowers:subagent-driven-development 或 superpowers:executing-plans。
> 提交信息不加 Claude 署名；不要 push。

**目标：** 不加页数、不加版式，把现有 20 页从"每页不一样"提到"每页都站得住"，并让内容步能交出可画原生图表的数据。

**做法：** P0 不改视觉风格，只做四件事：给检查补上审美规则、让失败的页先换构图再兜底、收紧大字的用法、只重做有问题的页。P1 在内容生成前加一步"数据序列提取"，每个数值带原文和出处，由代码核对后才允许画图。

**涉及技术：** Python 3、pydantic、Playwright（headless Chromium）、python-pptx、pytest；真实渲染用 `scripts/pptx_snapshot.sh`（macOS PowerPoint）。

**需求来源：** Jay 2026-11-13 的阶段指示（P0–P3）。本计划只覆盖 P0、P1。基线提交 `577c890`，基线成品 `data/html-pages/run20b/out2/`。

## 全局约束

- 不扩展到 100 页，不增加 `STUDIO_FORMS` 里的版式数量，不重写项目。
- 保留 studio 主题的配色、字体、网格；CSS 只做微调。
- 现有溢出、重叠、数字核对检查全部保留，只增不减。
- 没有数据就不画图；页面上任何数字、任何图形的长短高低都必须能对到已核对的内容。
- 每个改动之后用真实模型重做受影响的页，并用 PowerPoint 导出逐页看图。不以"检查通过"代替看图。
- 优先级：设计质量、可读性、内容表达；不为速度和费用让步。

## 现状诊断（决定了下面每个任务）

| 页 | 看到的问题 | 根因（已在检查点里核实） |
|---|---|---|
| 4 | 兜底页；左下大片空；"2023年起""2024年"被当成数字放大 | 模型把 21.5% **增速** 画成了"占 100% 的比例条"（`candidates()` 只要有百分比就放行 strip）；三次重写后还差 7 像素，直接整页兜底；兜底模板把 `date` 当 `value` 用 |
| 9 | 兜底页，和第 4 页同一个模板 | 模型凭空画了两根柱子（"回报高"100%、"待探索"），没有任何数据；嵌套了带 `grid-column` 的 `.r`；一处小溢出导致整页兜底；兜底只有一个模板 |
| 11 | 左侧 4 行 54px 结论压过标题和数据 | 把 70 字以内的 `takeaway` 放进了 `q1`（观点页专用）；`t1` 标题和 `q1` 两个焦点；同样嵌套了 `.r` |
| 17 | 标题和正文之间空了 200 像素，右边栏的"超10亿美元"反而最大 | 内容没有 `highlight`，却给了 `t1` 大标题加 `d4` 数字；没有"同一栏里的空洞"检查 |
| 20 | 底部结论顶到上一行文字 | 2 行 `q1`（153px）放进 1 行高（75px）且 `bot` 的区域，向上溢出；重叠检查用的是收紧后的行框，差一点没判为重叠；审稿后重做时形式改成了 flow，但 `job.form` 还是 table，示范页不对 |
| 全套 | 没有原生图表 | 第 5 页正文里明写"由2022年的4.5%升至2024年上半年12.1%"，`categories` 仍是空：一次调用里既写文案又抽序列，模型顾不上 |
| 全套 | 说明小字 14px 偏小；朱红偏多 | CSS 与缺少计数检查 |

## 评审重点（规格没写、但最可能出事的输入）

1. 资料里完全没有成序列的数字（纯定性资料）→ 整套 0 张图表也必须正常产出，不报错、不凑数。测试在任务 6。
2. 同一组数据被相邻两页各抽一次 → 只画一次。测试在任务 6。
3. 序列里混了预测值和实际值（6233 亿是 IDC 预测）→ 图注必须带"预测"。测试在任务 5、7。
4. 审美检查过严，把本来好的页（第 5、12、18 页）判坏 → 用基线 20 页的 HTML 做回归夹具，好页必须 0 条新缺陷。测试在任务 1。
5. 审美缺陷永远不能让一页被丢掉：改不好就保留缺陷最少的那版。测试在任务 2。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/ppt_agent/v2/htmldeck/aesthetics.py` | 新建 | 基于测量结果的审美缺陷（间距、焦点、空洞、朱红、大字长度） |
| `src/ppt_agent/v2/htmldeck/graphic_checks.py` | 新建 | 图解元件是否有数据依据；嵌套区域 |
| `src/ppt_agent/v2/htmldeck/checks.py` | 修改 | `find_defects` 末尾并入上面两类；`Defect.kind` 注释补全 |
| `src/ppt_agent/v2/htmldeck/pipeline.py` | 修改 | 审美缺陷的重写策略；`seed_html` |
| `src/ppt_agent/v2/htmldeck/deck.py` | 修改 | 失败先换形式；缓存页复查；审稿重做带形式 |
| `src/ppt_agent/v2/htmldeck/structural.py` | 修改 | 三种兜底页，按内容和邻页选择 |
| `src/ppt_agent/v2/htmldeck/layout_plan.py` | 修改 | strip/units 只给占比；有序列时的图表选择 |
| `src/ppt_agent/v2/htmldeck/review.py` | 修改 | 缩略图审查可返回建议形式 |
| `src/ppt_agent/v2/htmldeck/themes/studio.css`、`studio_guide.md` | 修改 | 小字号、结论条 `call`、大字使用规则 |
| `src/ppt_agent/v2/visual/content.py` | 修改 | `DataPoint`、`DataSeries`，`RichContent.series` |
| `src/ppt_agent/v2/data_series.py` | 新建 | 提取、核对、去重数据序列 |
| `src/ppt_agent/v2/typeset_pipeline.py` | 修改 | 内容生成前调用提取；把序列交给内容步 |
| `src/ppt_agent/v2/render.py` | 修改 | studio 图表样式 |
| `tests/fixtures/studio20/` | 新建 | 基线 20 页的 HTML（从检查点复制） |
| `tests/test_v2_htmldeck_aesthetics.py`、`test_v2_htmldeck_graphic_checks.py`、`test_v2_data_series.py` | 新建 | 对应测试 |

---

# P0：精修当前 20 页

### 任务 1：审美检查

**文件：** 新建 `htmldeck/aesthetics.py`、`tests/test_v2_htmldeck_aesthetics.py`、`tests/fixtures/studio20/page_NNN.html`；修改 `htmldeck/checks.py`。

**接口**
- 产出：`aesthetics.find(measured: Measured, *, tier: str = "standard", has_title: bool = True) -> list[Defect]`
- 产出：`checks.AESTHETIC = frozenset({"tight", "focal_conflict", "long_display", "hole", "red_overuse", "small_note"})`
- `find_defects(...)` 新增关键字参数 `tier: str = "standard"`，末尾追加 `aesthetics.find(...)` 的结果。

**规则（数值按 1280×720 像素）**

| kind | 判定 | 给模型的话（要点） |
|---|---|---|
| `tight` | 两段文字水平有交叠、上下相邻，行框间距 < 较大字号的 0.35 倍 | "「A」和「B」贴得太近，至少留出 N 像素，或把大字放进单独的区域并给够行数" |
| `long_display` | 字号 ≥ 54px 的非纯数字文字超过 2 行或 24 个字；`t1` 超过 2 行 | "这句话太长，不能用大字；改用 call 或 t3" |
| `focal_conflict` | 页面有字号 ≥ 54px 的文字时，标题还用了 `t1`（44px）；或 ≥ 54px 的文字有 3 处以上；或第二大的字号超过最大字号的 0.6 倍且两者都 ≥ 54px | "这一页有两个焦点，只保留「X」做最大，其余降到 t3 以下" |
| `hole` | 同一栏（水平交叠 > 50%）里上下相邻的两段文字之间空出 > 150px，且两段都在页面上 80% 内；或内容最低点低于：visual 0.62、standard 0.72、deep 0.80 × 页高 | "标题和下面的内容之间空了 N 像素，把内容上移或让主要元素占满这块" |
| `red_overuse` | 朱红文字段 + 朱红色块 > 3 处（kicker、pageno 不计） | "朱红用了 N 处，只留焦点和一个标记" |
| `small_note` | 页脚以外的文字 < 15px | 沿用 small_text 的话术 |

原有 `sparse`（0.62）由 `hole` 的分档阈值取代；`sparse` 这个 kind 保留给结构页。

- [ ] **步骤 1：建夹具。** 把 `data/html-pages/run20b/editorial/checkpoints/html/page_*.json` 的 `html` 字段和 `failed_004/009.json` 的 `html` 各存成 `tests/fixtures/studio20/page_NNN.html`（失败的存 `failed_NNN.html`）。
- [ ] **步骤 2：写失败的测试**（浏览器测试沿用 `test_v2_htmldeck_browser.py` 的 session 夹具和跳过条件）：

```python
BAD = {"011": {"long_display", "focal_conflict"}, "017": {"hole", "focal_conflict"}, "020": {"tight"}}
GOOD = ["005", "012", "018", "013", "006"]

@pytest.mark.parametrize("page,kinds", BAD.items())
async def test_known_bad_pages_are_flagged(session, page, kinds):
    found = {d.kind for d in aesthetics.find(await measure_fixture(session, page))}
    assert kinds <= found

@pytest.mark.parametrize("page", GOOD)
async def test_known_good_pages_stay_clean(session, page):
    assert aesthetics.find(await measure_fixture(session, page), tier=TIER[page]) == []
```
  另加纯数据测试（手工构造 `Measured.items`，不需要浏览器）：间距 10px 的 19px 与 54px 两段 → `tight`；4 处红色 → `red_overuse`；14px 正文 → `small_note`。
- [ ] **步骤 3：** 跑测试确认失败（`uv run pytest tests/test_v2_htmldeck_aesthetics.py -v`，预期 `ModuleNotFoundError`）。
- [ ] **步骤 4：** 实现 `aesthetics.find`，复用 `checks._line_rect`、`_item_text`、`_short`。颜色取测量结果里 run 的颜色字段，与主题朱红 `#c5341c` 比较。
- [ ] **步骤 5：** 好页若被误报，调阈值而不是删规则；阈值改动写进本表。跑全部 htmldeck 测试通过。
- [ ] **步骤 6：** 提交：`Check spacing, focal conflict, long display type, holes and red overuse`。

### 任务 2：图解必须有数据依据；审美缺陷的重写策略

**文件：** 新建 `htmldeck/graphic_checks.py`、`tests/test_v2_htmldeck_graphic_checks.py`；修改 `htmldeck/pipeline.py`、`htmldeck/layout_plan.py`。

**接口**
- 产出：`graphic_checks.find(html: str, content: dict) -> list[Defect]`，用标准库 `html.parser` 解析已清洗的 HTML。
- 产出：`design_page(..., seed_html: str | None = None, seed_problems: list[str] | None = None)`：给了就把它当"上一次写的 HTML"，第一次调用即为修改。

**规则（都是硬缺陷，kind 用 `unbound_graphic` / `unsupported`）**
- `.cols`：每根 `.col` 的文字里必须有内容里的数字，且各柱高度之比与数值之比相差 ≤ 5 个百分点；否则"柱子的高度没有数据依据，请换成并列文字"。
- `.strip .fill` 的宽度必须等于内容里某个百分比，并且该百分比是占比（任务 5 之前：`relation == "proportion"`；任务 5 之后：属于 `kind == "share"` 的序列）。增速、提升幅度不能画成比例条。
- `.units` 点亮个数 = 该占比 ÷ 10 四舍五入。
- 带 `grid-column`/`grid-row` 的 `.r` 不能出现在另一个 `.r` 里："区域不能嵌套，请把它写成并列的区域"。
- `layout_plan.candidates()`：`strip`、`units` 的 `usable` 增加条件 `relation == "proportion"`。

**重写策略（`pipeline.design_page`）**
- 硬缺陷：`MAX_REWRITES` 仍为 2。
- 只剩审美缺陷时：再给最多 2 次修改机会（`MAX_POLISH = 2`），每次把上一版 HTML 和缺陷原话交回去。
- 始终保留"无硬缺陷里审美缺陷最少"的一版；审美缺陷永远不使 `result.page` 为 `None`。
- `review.look` 之后的那次修改，只在它的审美缺陷数不多于修改前时采用。
- `PageResult` 增加 `aesthetic: list[str]`（最终版遗留的审美缺陷），写进检查点和运行摘要 `aesthetic_left`。

- [ ] **步骤 1：** 写失败的测试：`failed_009.html` → 含 `unbound_graphic` 和嵌套区域；`failed_004.html`（内容用 `content_004.json`，relation=trend）→ strip 的 `unbound_graphic`；示范页 `themes/studio/*.html` 配各自示范内容 → 全部 0 条（示范页不合规就改示范页）；`candidates({"archetype":"rich","relation":"trend","items":[{"value":"21.5%"}], ...})` 不含 `strip`。
- [ ] **步骤 2：** 写失败的管线测试（用 `test_v2_htmldeck_pipeline.py` 里的假 client）：第一次回复有 `long_display`、第二次干净 → `attempts == 2`、`result.aesthetic == []`；三次都只有审美缺陷 → `result.ok is True` 且采用缺陷最少的一版。
- [ ] **步骤 3：** 跑测试确认失败。
- [ ] **步骤 4：** 实现。
- [ ] **步骤 5：** 跑 `uv run pytest tests/test_v2_htmldeck_*.py -v` 全过。
- [ ] **步骤 6：** 提交：`Graphics must be backed by figures; polish pages on aesthetic defects without dropping them`。

### 任务 3：失败先换构图，兜底页不再只有一个样子（第 4、9 页）

**文件：** 修改 `htmldeck/deck.py`、`htmldeck/structural.py`、`htmldeck/review.py`；测试加在 `tests/test_v2_htmldeck_longdeck.py`、`test_v2_htmldeck_themes.py`。

**接口**
- `structural.plain(..., variant: Literal["ledger", "poster", "columns"] = "ledger", highlight: tuple[str, str] | None = None)`
  - `ledger`：现有样子，但标题区用 `spread`，`takeaway` 贴底并加 `lt`，不再左下留空。
  - `poster`：左 7 列 `d3`/`d2` 放 `highlight.value` + 说明，右 5 列最多 3 条事实，用 `lt` 分隔。需要 `highlight`。
  - `columns`：标题通栏，下面 2–4 条事实等宽并列，每条 `lt` 顶线，底部 `call` 结论。
- `deck._plain_html(job, variant)`：数字列只用 `item["value"]`；没有 `value` 时用序号 `01`…；`date` 写进说明文字，不放大。
- `deck._plain_variant(job, neighbours: set[str]) -> str`：有 `highlight` 且 `poster` 不在前后 3 页的兜底变体/形式里 → `poster`；条目 ≤ 4 → `columns`；否则 `ledger`；与邻页已用的兜底变体不同。
- `review.matrix` 的返回项增加可选 `"form"`；提示词要求"如果建议换形式，form 必须从该页 allowed 里选"，调用方把每页的 allowed 传进用户消息。

**流程（`deck.one`）**
1. `design_page` 失败 → 依次换 `layout_plan.candidates()` 里前后 3 页没用过的另外最多 2 种形式重新设计（更新 `plan` 和 `job.form`、`job.idea`）。
2. 仍失败 → `_plain_variant` 选兜底页。
3. `redo(new_form=False)`：若审稿意见带合法 `form`，先 `_apply` 新指纹再设计，保证示范页与形式一致；`redo` 用 `look=True`。

- [ ] **步骤 1：** 写失败的测试：假 client 对 `strip` 永远返回溢出的 HTML、对 `margin` 返回合格 HTML → 该页 `templated is False` 且 `job.form == "margin"`；全部形式都失败的两页（一页有 highlight）→ 两页兜底变体不同；`_plain_html` 对 `content_004.json` 的结果里 `class="fig` 的文字不含"年"；三种变体在浏览器里测量后 `find_defects` 为空（条目数 2、4、6 各测一次）。
- [ ] **步骤 2：** 跑测试确认失败。
- [ ] **步骤 3：** 实现。
- [ ] **步骤 4：** 跑测试通过；提交：`Try other forms before the plain page; three plain variants; reviews can name a form`。

### 任务 4：大字规则与字号微调（第 11、17、20 页及全套）

**文件：** 修改 `themes/studio.css`、`themes/studio_guide.md`、受影响的 `themes/studio/*.html` 示范页；测试 `test_v2_htmldeck_themes.py`。

**决定**
- `.c` 14px → 15px；`.s` 16px → 17px；`.compact .s` 15px → 16px。`.source`、`.pageno` 不变。
- 新增结论条 `.call`：衬线 24px、行高 1.5、上方 2px 朱红线、上内边距 14px、`max-width:30em`，最多 2 行。它不是新版式，只是一个文字样式。
- `.q1` 增加 `margin-top:18px`；`.d1`–`.d4` 增加 `margin-top:8px`（防止大字贴住上一段）。
- `studio_guide.md` 字号一节补四条：
  - `q1` 只用在 statement 形式的页；其他页的结论（takeaway）用 `call`。
  - 页面有 `d1`–`d4` 时标题用 `t2`，不用 `t1`。`t1` 只用在没有大数字的页，且不超过 2 行。
  - 没有焦点数字（内容没有 highlight）的页，焦点是图解本身或标题，不要把条目里的数字放大成 `d4` 以上。
  - 区域不能嵌套；`bot` 区域的行数必须装得下内容（2 行 `call` 要 2 行）。
- 朱红一条改为"kicker 之外不超过三处"，与 `red_overuse` 的计数一致。

- [ ] **步骤 1：** 写失败的测试：guide 文本含 `call` 且声明 `q1` 仅用于 statement；所有示范页测量后 `find_defects`（含审美）为空；`statement.html` 以外的示范页不含 `class="q1`。
- [ ] **步骤 2：** 改 CSS、guide、示范页；跑测试通过。
- [ ] **步骤 3：** 重新导出字体校准图，确认 15px 说明文字在 PowerPoint 里与浏览器一致（沿用 `docs/design/html-font-calibration.png` 的做法）。
- [ ] **步骤 4：** 提交：`A conclusion bar instead of display type for takeaways; larger notes; rules for when big type is allowed`。

### 任务 5：只重做有问题的页，并用 PowerPoint 验证

**文件：** 修改 `htmldeck/deck.py`（缓存复查）；新增 `eval/results/studio-20-refine-2026-11-XX.md`、`docs/design/studio-20-refine-*.png`。

**接口**
- `deck._replay` 之后：缓存页若无硬缺陷但有审美缺陷或 `graphic_checks` 缺陷 → 调 `design_page(seed_html=缓存HTML, seed_problems=缺陷原话)` 做局部修改；修改失败则保留缓存版。无缺陷的缓存页不调用模型。

- [ ] **步骤 1：** 写失败的测试：缓存里一页干净、一页有 `long_display` → 假 client 只被调用一次，且收到的用户消息包含缓存 HTML。实现，测试通过，提交：`Recheck cached pages against the new rules and repair them in place`。
- [ ] **步骤 2：** 复制检查点目录到 `data/html-pages/run20c/`（保留内容与版式规划，删除 `html/failed_*.json`），用与基线相同的命令加 `--html-theme studio` 和复用检查点的开关重跑。预期：第 4、9 页重新设计，第 11、17、20 页被局部修改，其余页按缓存复查。
- [ ] **步骤 3：** `scripts/pptx_snapshot.sh data/html-pages/run20c/out data/html-pages/run20c/out/*.pptx`，逐页看 20 张 PNG。
- [ ] **步骤 4：验收（全部满足才算 P0 完成）**
  - 兜底页 0；第 4、9 页轮廓距离 > 5，且各自有明确焦点。
  - 第 11、17、20 页：没有超过 2 行的 54px 以上文字；结论与相邻文字间距 ≥ 18px；每页只有一个最大元素。
  - 全套 `aesthetic_left` 为空，或逐条写明为什么接受。
  - 没有任何图形的长短高低缺少数据依据（人工逐页核对一遍比例条、柱子、方块阵）。
  - 人工逐页检查：字号层级、行距、左边线对齐、视觉重心、留白，把每页的结论记进结果文件（一页一行）。
- [ ] **步骤 5：** 没过的页：先看是规则漏了还是提示词不够，改规则/提示词后只重做那一页，回到步骤 3。不手改 HTML 冒充生成结果。
- [ ] **步骤 6：** 写结果文件并提交：`Record the refined 20-page run`。

---

# P1：可视化的数据结构

### 任务 6：数据序列模型

**文件：** 修改 `v2/visual/content.py`；测试 `tests/test_v2_htmldeck_ir.py` 或新建 `tests/test_v2_data_series.py`。

**接口（产出，后面任务都用这些名字）**

```python
class DataPoint(StrictModel):
    label: str        # ≤16，类别或时间点，如 "2022年"
    value: float
    display: str      # ≤14，原文写法，如 "4.5%"
    quote: str        # ≤120，包含这个数字的原文片段，逐字
    ref: str          # ≤40，"<文件名> 第N页"
    projected: bool = False   # 预测/计划值

class DataSeries(StrictModel):
    metric: str       # ≤30，指标定义，如 "AI融资占全行业融资比例"
    unit: str         # ≤10，"%"、"亿美元"
    kind: Literal["time", "category", "share", "distribution"]
    period: str | None   # ≤24，时间范围，如 "2022年—2024年上半年"
    scope: str | None    # ≤30，口径或主体，如 "全球"
    points: list[DataPoint]   # 2–8 个
```
- `RichContent.series: list[DataSeries] = []`（最多 2 条）。`series` 是唯一真源：校验器在有 `series` 时用第一条覆盖 `chart_title`（= metric）、`categories`、`values`、`unit_format`，下游（`candidates`、`evidence_check`）不用改。
- 校验：同一序列 `label` 不重复；`share` 的值都在 0–100 且合计 ≤ 100.5；`points` 少于 2 报错。

- [ ] **步骤 1：** 写失败的测试：合法序列通过并回填 `categories == ["2022年","2024年上半年"]`、`values == [4.5, 12.1]`；重复 label、share 合计 130、单点序列各自报错；没有 `series` 的旧内容 JSON（`content_005.json`）仍能解析。
- [ ] **步骤 2：** 实现，测试通过，提交：`A data series model: metric, unit, period, scope, and a quoted source per value`。

### 任务 7：提取、核对、去重

**文件：** 新建 `v2/data_series.py`、`tests/test_v2_data_series.py`；修改 `v2/typeset_pipeline.py`、`v2/mock.py`。

**接口**
- `async def extract(client, checkpoints, page_number: int, title: str, evidence_text: str) -> list[DataSeries]`：一次 `complete_json`（task=`data_series`），检查点 `typeset/series_NNN.json`。证据里数字少于 2 个时不调用模型，返回 `[]`。
- `def verify(series: list[DataSeries], evidence_text: str) -> tuple[list[DataSeries], list[dict]]`：返回通过的序列和被拒的点（含原因）。
- `def dedupe(by_page: dict[int, list[DataSeries]]) -> dict[int, list[DataSeries]]`：同一 `(metric, unit, 各点 label+value)` 只留在页码最小的那页。

**提示词要点（`SERIES_SYSTEM`）：** 只从给出的原文里找"同一指标、同一单位、两个及以上取值"的组合：时间序列、跨类别对比、占比构成、分布。每个值必须给出逐字原文片段和页码。不同指标不能拼成一条；不要计算、不要补全缺失年份；预测值标 `projected`。找不到就返回 `{"series": []}`——空结果是正确答案。

**`verify` 规则（任何一条不满足，丢掉这个点；剩不足 2 个点，丢掉整条）**
- `quote` 去空白后是证据文本的子串。
- `display` 里的数字出现在 `quote` 里，且用 `evidence_check._key` 解析后等于 `value`。
- `kind == "time"` 时，`label` 里的年份出现在 `quote` 里。
- 同一条里各点 `display` 的单位一致，且与 `unit` 一致；百分比与百分点不混。
- `quote` 含"预计、预测、将、有望、计划"而 `projected` 为假 → 置为真（不丢点）。

**接入（`typeset_pipeline` 的 `produce`）**
- 仅 `rich` 路径：先对所有内容页并发 `extract` → `verify` → `dedupe`，结果存 `typeset/series_plan.json`（含被拒原因）。
- 该页有通过核对的序列时：`diversity` 增加 `available_series`；`RICH_SYSTEM` 增加一句"available_series 里的序列已经核对过，页面论点与它相关时原样放进 series，不要改数、不要新增序列"；并删掉原来让模型自己填 `categories + values` 的两句。
- `generate_content` 返回后，代码把 `series` 里不在 `available_series` 中的条目剔除（模型不能自造序列）。
- 运行摘要增加：`series_found`、`series_rejected`、`pages_with_series`。

- [ ] **步骤 1：** 写失败的测试（证据文本用信通院第 12 页那段真实原文作夹具）：
  - 模型返回 4.5%/12.1% 两点且 quote 逐字 → `verify` 保留，`kind == "time"`。
  - 把 12.1 改成 12.7 → 该点被拒，整条被丢。
  - quote 不在原文 → 被拒。
  - "6233亿美元"的 quote 含"预计" → `projected is True`。
  - 纯定性原文（无数字）→ `extract` 不调用 client，返回 `[]`。
  - 第 4、5 页抽到同一条 → `dedupe` 后只在第 4 页。
  - 模型在 content 里加了一条不在 `available_series` 的序列 → 最终 `series` 不含它。
- [ ] **步骤 2：** 跑测试确认失败；实现；`mock.py` 增加 `data_series` 任务的假回复；测试通过。
- [ ] **步骤 3：** 提交：`Extract data series before writing pages, verify every value against its quoted source`。

### 任务 8：把序列画成原生图表

**文件：** 修改 `htmldeck/layout_plan.py`、`htmldeck/prompts.py`、`htmldeck/pipeline.py`、`htmldeck/graphic_checks.py`、`v2/render.py`、`themes/studio_guide.md`、`themes/studio/chart.html`。

**接口**
- `layout_plan.chart_kind(series: dict) -> str | None`：`time` 且 ≥ 3 点 → `line`；`time` 2 点 → `column`；`category` → `bar`（按值降序）；`distribution` → `column`；`share`：合计在 99.5–100.5 → `doughnut`，否则返回 `None`（交给 strip/units）。
- `prompts.chart_attr(series: dict, highlight: int | None) -> str`：由代码生成完整的 `data-chart` JSON；用户提示里写"图表请原样使用这个属性，不要改里面的任何数字"。
- `prompts.chart_caption(series: dict) -> str`：`"{metric}（{unit}）· {scope} · {period} · {ref}"`，有预测值时加"含预测值"；`pipeline._required` 要求它原样出现在页面上（放在图表上方的 `c` 行）。
- `graphic_checks.find`：页面上每个 `.chart` 的 categories 和 values 必须与该页某条已核对序列逐项相等，否则硬缺陷 `bad_chart`；有序列且形式是 chart 的页没有 `.chart` → 硬缺陷。
- `candidates()`：有序列且 `chart_kind` 不为空时 `chart` 排第一；`MAX_FORM_SHARE` 对 `chart` 不适用（数据多就该多画），但相邻两页仍不同形式，连续的图表页用不同 `kind` 或不同的左右位置。
- `render._render_chart`：studio 主题下数据标签 12pt 等线、类别轴文字 `#6f6a5e`、无网格线、无边框；非高亮柱 `#b9b2a3`、高亮柱 `#c5341c`；折线 2.25pt 朱红、末点标签加粗；doughnut 主项朱红、其余浅褐、不显示图例而用数据标签。预测值的点用空心/浅色（`ChartItem` 增加 `projected: list[int] = []`，由 `data-chart` 的 `projected` 字段带入）。

- [ ] **步骤 1：** 写失败的测试：`chart_kind` 五种输入的结果；`chart_attr` 的 JSON 能通过 `convert.chart_spec`；页面上图表的值被改动 → `bad_chart`；缺少图注 → `missing`；`test_v2_qa_render.py` 风格的渲染测试：生成的 PPTX 里该图形 `has_chart` 为真、类别与数值与序列一致、高亮点颜色为 `C5341C`。
- [ ] **步骤 2：** 实现，测试通过。
- [ ] **步骤 3：** 用 `scripts/visual_samples.py` 的做法做一页手写图表样张（line、column、bar、doughnut 各一），PowerPoint 导出看图：标签不压柱、不被裁、字号与正文协调。调 `render.py` 直到四种都过。
- [ ] **步骤 4：** 提交：`Draw verified series as native charts with a sourced caption`。

### 任务 9：真实运行与记录

- [ ] **步骤 1：** 在 `data/html-pages/run20d/` 从内容步重新生成 20 页（内容会变，这是预期的），PowerPoint 导出逐页看。
- [ ] **步骤 2：验收**
  - 每张图表：在 PowerPoint 里点开能编辑数据；数值逐个对回 PDF 原文页（人工核对，全部列进结果文件：指标、单位、时间范围、口径、出处、是否预测）。
  - 被拒的点和原因列表人工过一遍：没有"原文其实有、被规则误杀"的情况超过 2 例，否则回任务 7 调规则。
  - 信通院这份资料里至少"融资占比 4.5%→12.1%"这一组被画成图；如果整套图表页为 0，查提取步的原始回复，不放宽核对规则。
  - 任务 5 的 P0 验收标准在这一轮同样成立。
- [ ] **步骤 3：** 写 `eval/results/studio-20-series-2026-11-XX.md`：图表页数、序列数、被拒数与原因、遗留问题、未验证项（Windows PowerPoint、WPS）。提交。

---

## 不在本计划内

- P2 独立视觉评审、P3 两份新资料与 30–40 页：等 P0/P1 验收后单独写计划。P1 完成时把"审美规则漏掉、靠人眼才发现"的问题逐条记下，作为 P2 评审提示词的输入。
- 图片、100 页、新增版式、速度与费用优化。

## 自查

- 规格覆盖：P0 第 1 条（保留风格）→ 全局约束；第 2–4 条 → 任务 2、3、4；第 5 条 → 任务 1、4、5 的人工逐页检查；第 6 条 → 任务 1、2；第 7 条 → 任务 5、9。P1 五条 → 任务 6（字段）、7（主动识别、不编造、无数据不画）、8（原生图表优先）。
- 名字一致：`aesthetics.find`、`graphic_checks.find`、`DataSeries`/`DataPoint`、`data_series.extract/verify/dedupe`、`chart_kind`、`chart_attr`、`chart_caption`、`seed_html` 在各任务中写法相同。
- 待执行者现场确认的两处：测量结果里 run 的颜色字段名（任务 1）；复用检查点的命令行开关名（任务 5，见 `v2/cli.py` 第 64 行附近）。
