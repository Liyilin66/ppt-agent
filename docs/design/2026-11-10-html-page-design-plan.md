# HTML 页面设计重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 模型用受限的 HTML 设计每一页，浏览器量尺寸，代码转成原生可编辑的 PPT 元素；三套主题；长稿不重复；出处与数字核对不变。

**Architecture:** 新增 `src/ppt_agent/v2/htmldeck/` 包。每页先写已核对的内容，再写 HTML；Playwright 排版并量出每个元素；`convert` 把量到的元素变成 `PageDesign`（新增两种元素 `rich_text`、`box`），现有 `render.py` 输出 PPTX。旧排版引擎不动，作为兜底。

**Tech Stack:** Python 3.11、python-pptx、Playwright（用本机 Chrome，`channel="chrome"`）、pydantic、pytest。

**Spec:** `docs/design/2026-11-10-html-page-design.md`

## Global Constraints

- 全流程只用 `gpt-5.6-terra`；单次调用输出控制在约 3500 token 内，一次只设计一页，页与页并发。
- 字体只用 Office 自带的：微软雅黑（Microsoft YaHei）、等线（DengXian）、宋体（SimSun）、黑体（SimHei）。浏览器量尺寸用同一份字体文件。
- 文字必须是文本框，图表必须是原生图表，表格必须是原生表格；不把整页或大块内容做成图片。
- 页面上的数字必须来自已核对的内容；出处页码不能丢。
- 旧引擎（`visual/`、`typeset_pipeline.py`）行为不变，全部现有测试通过。
- 没有浏览器时自动使用旧引擎并提示。
- 提交不加 Claude 署名；不 push。
- 画布 1280×720；1 px = 9525 EMU = 0.75 pt。
- 自动重写最多 2 次；重试后仍不合格的页退回旧引擎并记录。

## Review Focus

1. 模型写出不支持的 HTML（`<script>`、外链、任意字体、滤镜）：应在转换前拒绝并作为问题发回，不能进到 PPT。
2. 文字比量到的多一行（PowerPoint 与浏览器换行不同）：文本框不能压到下面的元素。
3. 设计步多写了内容步没有的数字：应被发现并退回。
4. 机器上没有 Chrome 或没有 Office 字体：应退回旧引擎或系统字体，并在结果里说明，不能崩。
5. 图表数据不合法（系列长度不一致、少于 2 个值）：应作为问题发回，最终退回为文字或旧引擎，不能产出空图表。

---

## 阶段一：转换器

### Task 1：页面结构新增两种元素

**Files:** Modify `src/ppt_agent/v2/ir.py`、`src/ppt_agent/v2/render.py`、`src/ppt_agent/v2/preview.py`、`src/ppt_agent/v2/qa.py`（只需不因新类型出错）；Test `tests/test_v2_htmldeck_ir.py`

**Produces:**
- `ColorStop(hex: str, alpha: float = 1.0, pos: float)`，`pos` 为 0–100。
- `Paint(hex: str | None, alpha: float = 1.0, stops: list[ColorStop] | None, kind: Literal["solid","linear","radial"] = "solid", angle_deg: float = 0)`。
- `TextRun(text: str, size_pt: float, hex: str, alpha: float = 1.0, bold: bool = False, italic: bool = False, font: str, letter_spacing_pt: float = 0, paint: Paint | None = None, line_break: bool = False)`。
- `RichTextItem(type="rich_text", id, frame, runs: list[TextRun], align, valign, wrap: bool, line_pt: float | None)`。
- `BoxItem(type="box", id, frame, shape: Literal["rect","round","round_top","ellipse"], radius: float = 0, fill: Paint | None, stroke_hex: str | None, stroke_alpha: float = 1, stroke_width: float = 0, glow_hex: str | None, glow_alpha: float = 1, glow_radius: float = 0)`。
- `PageDesign.background_hex: str | None = None`（优先于 `background`）。
- `Frame` 的下限对这两种元素放宽：允许出血（`x`、`y` 可到负一个画布，`w`、`h` 可到两个画布），用单独的 `BleedFrame`。

- [ ] 测试：旧的 PageDesign JSON 仍能加载；含 `rich_text`、`box` 的页面能往返序列化；`render_deck` 输出的 PPTX 里 `rich_text` 是文本框且每个 run 的字号、颜色、字体正确，`box` 的渐变停靠点和透明度写进了 XML，`spPr` 子元素顺序为 `xfrm, prstGeom, fill, ln, effectLst`；`page_to_html` 对新元素不报错。
- [ ] 实现并通过；全部测试通过；提交。

### Task 2：字体

**Files:** Create `src/ppt_agent/v2/htmldeck/__init__.py`、`src/ppt_agent/v2/htmldeck/fonts.py`；Test `tests/test_v2_htmldeck_fonts.py`

**Produces:** `office_font_css() -> tuple[str, bool]`：返回 `@font-face` 样式文本，以及是否找到了 Office 字体。字体族名固定为 `Microsoft YaHei`、`DengXian`、`SimSun`、`SimHei`。macOS 从 `/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/` 读取；Chrome 不能直接加载 `.ttc` 时用 fontTools 拆到 `~/.cache/ppt-agent/fonts/`；Windows 用 `local()`；都没有时返回系统字体回退并标记 `False`。

- [ ] 测试：路径不存在时返回回退样式和 `False`；存在时样式里含四个族名。
- [ ] 实现；在本机确认 Chrome 实际用上了这些字体（`document.fonts.check`）；提交。

### Task 3：浏览器量尺寸

**Files:** Create `src/ppt_agent/v2/htmldeck/browser.py`、`src/ppt_agent/v2/htmldeck/extract.js`、`src/ppt_agent/v2/htmldeck/document.py`；Test `tests/test_v2_htmldeck_browser.py`（没有 Chrome 时 skip）

**Produces:**
- `document.wrap(body_html: str, theme_css: str, font_css: str) -> str`：完整 HTML 文档，页面根元素是 `<div class="slide">`，1280×720。
- `browser.available() -> bool`。
- `browser.Session`（上下文管理器，复用一个浏览器进程，线程安全地排队）：`measure(html_document: str) -> Measured`。
- `Measured(background: str, items: list[dict], defects: list[dict], screenshot_png: bytes)`。`items` 的种类：`box`、`text`、`chart`、`table`。
- `extract.js` 在试验脚本基础上增加：`<div class="chart" data-chart='JSON'>` 输出 `chart` 项且不再下钻；`<table>` 输出 `table` 项（表头、行、列宽、字号）且不再下钻；带 `data-icon` 的元素输出文字项。

- [ ] 测试：一段含色块、两行文字、渐变文字的 HTML，量到的位置与预期一致（±1 px），行数正确。
- [ ] 实现并通过；提交。

### Task 4：自动检查

**Files:** Create `src/ppt_agent/v2/htmldeck/checks.py`、`src/ppt_agent/v2/htmldeck/sanitize.py`；Test `tests/test_v2_htmldeck_checks.py`

**Produces:**
- `sanitize.clean(html: str) -> tuple[str, list[str]]`：去掉 `<script>`、`<style>`、`<link>`、`<iframe>`、事件属性、外部 URL；返回清理后的 HTML 和被拒绝的内容说明。内联 `style` 只保留白名单属性（布局类：`display, flex*, grid*, gap, width, height, min/max-*, margin*, padding*, position, left, top, right, bottom, text-align, align-*, justify-*`）。
- `checks.find_defects(measured: Measured, allowed_numbers: set[str]) -> list[Defect]`，`Defect(kind, message, element_text)`。种类：`overflow`（文字超出所在容器）、`off_canvas`（文字或图表超出页面）、`overlap`（两段文字相交）、`small_text`（正文低于 11 px 对应的下限）、`sparse`（内容占页面面积不足 35%）、`extra_number`（页面上出现未核对的数字）、`bad_chart`、`unsupported`。
- `checks.numbers_in(text: str) -> set[str]`：与 `evidence_check` 的数字归一化一致。

- [ ] 测试：每种问题各一个最小 HTML 触发；干净页面返回空列表。
- [ ] 实现并通过；提交。

### Task 5：转换

**Files:** Create `src/ppt_agent/v2/htmldeck/convert.py`；Test `tests/test_v2_htmldeck_convert.py`

**Produces:** `convert.to_page(measured: Measured, *, page_number: int, role: PageRole, section: str | None, title: str | None, notes: str | None) -> PageDesign`。规则沿用试验脚本：单行文字不换行并按对齐方式定位；多行文字用量到的行距（磅值）并开启换行；单边边框变成细条；`border-radius` 达到短边一半且宽高相等为椭圆；只有上边圆角为 `round_top`；居中阴影变成光晕。图表项变成 `ChartItem`，表格项变成 `TableItem`。`show_chrome=False`。

- [ ] 测试：用固定的 `Measured` 数据（不需要浏览器）断言输出的元素类型、位置、渐变角度换算（PPT 角度 = CSS 角度 − 90）。
- [ ] 端到端：把试验样张（改用 Office 字体）转换，PowerPoint 导出，与浏览器截图并排人工对比。结果记入 `eval/results/html-converter-2026-11-10.md`。
- [ ] 提交。

## 阶段二：主题

### Task 6：三套主题规范

**Files:** Create `src/ppt_agent/v2/htmldeck/themes/__init__.py`，以及 `themes/{editorial,dark,bright}/theme.css`、`guide.md`、`examples/*.html`；Modify `pyproject.toml`（package-data）；Test `tests/test_v2_htmldeck_themes.py`

**Produces:** `themes.get(name: Literal["editorial","dark","bright"]) -> HtmlTheme`，`HtmlTheme(name, label, css: str, guide: str, examples: list[str], spec: ThemeSpec)`。`spec` 给原生图表、表格和兜底引擎用。三套共用同一组类名（`kicker`、`title`、`lead`、`stat`、`card`、`panel`、`pill`、`rule`、`note`、`source`、`pageno`、`cols-2/3/4`、`chart`、`cover`、`section` 等），外观各不相同。

- [ ] 测试：三套主题都能加载；每个示范页经 `sanitize`、`measure`、`find_defects` 后没有问题。
- [ ] 三套主题各出 6 页样张（封面、章节页、大数字、图表、表格、流程或对比），转成 PPT 后 PowerPoint 导出，发给 Jay 看。提交。

## 阶段三：单页生成流程

### Task 7：内容步与设计步

**Files:** Create `src/ppt_agent/v2/htmldeck/content.py`、`prompts.py`、`pipeline.py`；Modify `src/ppt_agent/v2/mock.py`（离线用的假模型）；Test `tests/test_v2_htmldeck_pipeline.py`

**Produces:**
- `content.PageContent`：`title`、`takeaway`、`blocks: list[Block]`、`chart: ChartData | None`、`table: TableData | None`、`source`、`notes`。`Block(heading, text, figure: str | None, figure_label: str | None, source)`。
- `content.to_legacy(content) -> AnyContent`：转成旧引擎的内容，用于核对和兜底。
- `pipeline.design_page(client, session, theme, content, context) -> PageResult`，`PageResult(page: PageDesign, html: str, attempts: int, fell_back: bool, defects: list[Defect])`。流程：设计 → `sanitize` → `measure` → `find_defects` → 有问题带着问题重写（最多 2 次）→ `convert`；仍不合格退回旧引擎。
- 核对沿用 `typeset_pipeline` 里的 `repair_citation`、`source_is_valid`、`check_content_numbers`、`sanitize_content_numbers`。

- [ ] 测试（假模型）：正常页一次通过；第一次溢出、第二次修好；一直不合格时 `fell_back=True` 且页面来自旧引擎；设计步多写的数字被发现。
- [ ] 提交。

### Task 8：看图检查

**Files:** Create `src/ppt_agent/v2/htmldeck/review.py`；Test `tests/test_v2_htmldeck_review.py`

**Produces:** `review.look(client, screenshot_png, content, theme) -> list[str]`（问题列表，空表示通过）；`pipeline.design_page` 在自动检查通过后调用一次，有问题再改一轮。可用参数关闭。

- [ ] 测试（假模型）；提交。

### Task 9：接进生成主流程并做 20 页实测

**Files:** Modify `src/ppt_agent/v2/orchestrator.py`、`src/ppt_agent/v2/typeset_pipeline.py`（最小改动：`layout_engine="html"` 分支）；Test `tests/test_v2_htmldeck_integration.py`

- [ ] `BuildRequest.layout_engine` 增加 `"html"`，`html_theme` 字段；封面、执行摘要、章节页、行动路线也走 HTML 设计。
- [ ] 离线（假模型）20 页跑通。
- [ ] 真实 20 页（信通院报告，原提示词），PowerPoint 导出逐页看；记录到 `eval/results/html-20-2026-11-10.md`；成品发给 Jay 做小范围盲评。

## 阶段四：长稿

### Task 10：论证线、事实台账、页数建议、版面特征

**Files:** Create `src/ppt_agent/v2/htmldeck/ledger.py`、`signature.py`、`capacity.py`；Modify `planning.py`、`prompts.py`、`mcp_brief.py`；Test `tests/test_v2_htmldeck_longdeck.py`

**Produces:**
- `ledger.FactLedger`：`register(page, fact_key, role: "lead" | "support") -> bool`；同一事实最多 1 次 lead、2 次 support。
- `signature.of(page: PageDesign) -> str`；`signature.conflicts(signatures: list[str], window: int = 5, max_share: float = 0.08) -> list[int]`。
- `capacity.estimate(evidence) -> int`：资料能撑起的页数。
- 大纲阶段给每页分配事实和版面类型；生成完统计，只重做冲突页。

- [ ] 测试；离线 100 页；真实 100 页，PowerPoint 导出看；记录到 `eval/results/html-100-2026-11-10.md`。提交。

## 阶段五：接入

### Task 11：CLI、Web、MCP、修订

- [ ] CLI `--engine html --theme editorial|dark|bright`；Web 表单增加主题选择；MCP `create_deck` 接受主题，`prepare_deck` 在页数超出估算时给出建议。
- [ ] `revise_deck` 在 HTML 流程下带修改意见重走该页设计步；每页 HTML 存为检查点。
- [ ] Web 预览显示新页面。
- [ ] 测试；提交。

## 阶段六：评测与收尾

### Task 12

- [ ] 第 8 节验收表逐项实测并记录（盲评部分由 Jay 找人完成，这里准备好随机改名的三份 PDF 和记录模板）。
- [ ] README、样例、演示脚本更新；简历要点草稿。

## 实施中的调整（2026-11-10）

- **内容步沿用现有的内容生成。** 设计稿里写内容结构要比现在宽松；实际先复用了已有的按构图类型生成、核对的内容（`ArchetypeContent`），设计步在它之上自由排布。好处是出处校验、数字核对、执行摘要和兜底引擎都不用重写；代价是每页的内容量仍受旧结构的字数上限限制。放宽内容结构留到 20 页实测之后，看内容密度是否仍是短板再决定。
- **三套主题共用一套类名。** 同一份 HTML 可以在三套主题下显示，换主题不需要重新调用模型。
- **封面、目录、章节页、结束页不调用模型**，用模板填充。
- **检查点带内容指纹。** 续跑和修订时，内容没变的页直接用保存的 HTML 重建。
- **事实台账目前只做统计**（写进运行报告的 `fact_ledger`），还没有在生成时强制「一次主角、两次引用」。
- **页数估算**用一份报告的两次运行校准，见 `htmldeck/capacity.py` 的说明。
