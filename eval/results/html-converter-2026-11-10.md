# HTML 转原生 PPT：转换器实测（2026-11-10）

对应设计稿 `docs/design/2026-11-10-html-page-design.md` 的阶段一、二。这里只记录转换器和主题，不涉及模型生成的页面。

## 做了什么

1. 浏览器（本机 Chrome，无头模式）按 1280×720 排版一页 HTML，量出每个色块和每段文字的位置。
2. 转成 `PageDesign` 里两种新元素：`rich_text`（文本框，逐段样式）和 `box`（形状，支持渐变、透明度、圆角、光晕）。图表占位转成原生图表，`<table>` 转成原生表格。
3. 现有的 `render.py` 输出 PPTX。

## 字体

Office 在 macOS 上把微软雅黑、等线、宋体、黑体放在应用包里，系统和浏览器默认看不到。浏览器通过 `@font-face` 直接加载这些文件后，量到的换行位置和 PowerPoint 一致。

| 检查 | 结果 |
|---|---|
| 四种字体 × 三种字号（44 px 粗体单行、28 px 单行、15 px 四行段落），浏览器截图对比 PowerPoint 导出 | 12 格里换行位置全部相同，行位置肉眼看不出差别（`docs/design/html-font-calibration.png`，上半是浏览器，下半是 PowerPoint） |
| 宋体粗体 | Office 的宋体没有真正的粗体，浏览器和 PowerPoint 都用合成粗体，结果一致 |
| 西文与中文不同字体（Georgia + 宋体） | 文本框里分别写入西文字体和中文字体，结果一致 |

没有装 Office 的机器会退回系统字体（苹方、宋体-简），这时换行位置可能不同。运行报告里的 `html_design.office_fonts` 会标明用的是哪一种。Windows 上没有验证。

## 三套主题的示范页

8 页手写示范页（封面、章节页、大数字、图表、表格、三栏、步骤、观点页）在三套主题下共 24 页：

- 自动检查（溢出、越界、重叠、字号过小、页面偏空、不支持的写法）：0 处问题。
- PowerPoint 导出后逐页看：没有溢出或重叠。导出图见 `docs/design/html-themes-{editorial,dark,bright}.png`。
- 全部是原生元素：文字是文本框，图表页是可编辑的原生图表，表格页是原生表格，没有图片。

## 已知的不足

- 光晕（CSS 居中阴影）在 PowerPoint 里比浏览器略重。
- 原生图表的样式由 PowerPoint 决定，和浏览器里的占位图不完全一样（柱宽、标签位置）。
- 只支持单层背景；多重背景、滤镜、旋转、图片、SVG 会在转换前被拒绝。
- 没有验证 Windows、WPS 和 Keynote。

## 复现

```bash
uv run pytest tests/test_v2_htmldeck_*.py
uv run ppt-agent v2 demo --prompt "AI Agent 入门" --pages 12 --style-profile consulting --html-theme bright --output-dir out/html-demo
```
