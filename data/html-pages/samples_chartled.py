"""Five hand-made sample slides in the chart-led direction Jay picked (Ember Electrotech, IEA GEVO2025 launch).

Not the pipeline: a target to agree on before rebuilding the design system. Every figure is quoted from the
IEA Global EV Outlook 2025 text (page given on each slide); nothing is computed. Charts are native and editable.
Usage: uv run python data/html-pages/samples_chartled.py OUT.pptx FIGURE_PNG
"""
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

OUT, FIGURE = Path(sys.argv[1]), Path(sys.argv[2])

INK = RGBColor(0x1F, 0x23, 0x28)       # text
MUTED = RGBColor(0x6B, 0x72, 0x80)     # subtitles, axis
FAINT = RGBColor(0xD9, 0xDD, 0xE3)     # rules, gridlines
ACCENT = RGBColor(0x0E, 0x8A, 0x5F)    # the one strong colour (electric green)
ACCENT_SOFT = RGBColor(0x9F, 0xD8, 0xC0)
GREY_BAR = RGBColor(0xB8, 0xBE, 0xC7)
LATIN, CJK = "Helvetica Neue", "PingFang SC"

W, H = 12192000, 6858000              # 16:9 in EMU
MARGIN = Emu(560000)
pt = lambda value: Emu(int(value * 12700))


def font(run, size, *, bold=False, color=INK):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = LATIN
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        node = rpr.find(qn(tag))
        if node is None:
            node = etree.SubElement(rpr, qn(tag))
        node.set("typeface", CJK)


def text(slide, x, y, w, h, lines, *, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT):
    """lines: [(text, size, bold, color)] each its own paragraph."""
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    for index, (words, size, bold, color) in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.space_after = Pt(size * 0.35)
        run = paragraph.add_run()
        run.text = words
        font(run, size, bold=bold, color=color)
    return box


def rule(slide, x, y, w, color=FAINT, weight=0.75):
    line = slide.shapes.add_connector(1, x, y, x + w, y)
    line.line.color.rgb = color
    line.line.width = Pt(weight)
    return line


def frame(slide, number, title, subtitle, source):
    """The fixed frame every content slide shares: accent tick, conclusion title, subtitle, footer."""
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, MARGIN, pt(34), pt(46), pt(3))
    bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT; bar.line.fill.background()
    text(slide, MARGIN, pt(44), W - 2 * MARGIN, pt(40), [(title, 26, True, INK)])
    text(slide, MARGIN, pt(82), W - 2 * MARGIN, pt(24), [(subtitle, 14, False, MUTED)])
    rule(slide, MARGIN, H - pt(40), W - 2 * MARGIN)
    text(slide, MARGIN, H - pt(34), pt(700), pt(16), [(f"来源：IEA《Global EV Outlook 2025》{source}", 9, False, MUTED)])
    text(slide, W - MARGIN - pt(200), H - pt(34), pt(200), pt(16), [(f"全球电动车市场 2025   {number:02d}", 9, False, MUTED)],
         align=PP_ALIGN.RIGHT)


def style_chart(chart, *, colors, labels=True, gridlines=False, number_format='0.0', shown=None):
    chart.has_legend = False
    chart.has_title = False
    chart.font.size = Pt(11)
    chart.font.color.rgb = MUTED
    chart.font.name = CJK
    plot = chart.plots[0]
    plot.gap_width = 45
    for series in plot.series:
        for index, point_color in enumerate(colors):
            point = series.points[index]
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = point_color
            point.format.line.fill.background()
    if labels:
        plot.has_data_labels = True
        data_labels = plot.data_labels
        data_labels.number_format = number_format
        data_labels.number_format_is_linked = False
        data_labels.font.size = Pt(13)
        data_labels.font.bold = True
        data_labels.font.color.rgb = INK
        data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
        for index, label in enumerate(shown or []):
            # The source's own wording on the bar ("1100万+"), not a precision the source does not give.
            frame_ = plot.series[0].points[index].data_label.text_frame
            frame_.text = label
            font(frame_.paragraphs[0].runs[0], 13, bold=True)
    value_axis = chart.value_axis
    value_axis.visible = False
    value_axis.has_major_gridlines = gridlines
    category_axis = chart.category_axis
    category_axis.format.line.color.rgb = FAINT
    category_axis.tick_labels.font.size = Pt(12)
    category_axis.tick_labels.font.color.rgb = INK
    category_axis.tick_labels.font.name = CJK
    return chart


prs = Presentation()
prs.slide_width, prs.slide_height = W, H
blank = prs.slide_layouts[6]

# 1 · Cover: one strong band of colour, title, date. Nothing else.
slide = prs.slides.add_slide(blank)
band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, pt(330), H)
band.fill.solid(); band.fill.fore_color.rgb = ACCENT; band.line.fill.background()
text(slide, pt(380), pt(190), pt(520), pt(150), [("全球电动车市场", 40, True, INK), ("增长、分化与 2030 年的补能挑战", 20, False, MUTED)])
rule(slide, pt(380), pt(330), pt(60), color=ACCENT, weight=2.5)
text(slide, pt(380), pt(345), pt(500), pt(60), [("基于 IEA《Global EV Outlook 2025》", 12, False, MUTED), ("2026 年 11 月", 12, False, MUTED)])
text(slide, pt(40), H - pt(80), pt(260), pt(40), [("17,000,000+", 26, True, RGBColor(0xFF, 0xFF, 0xFF)),
                                                 ("2024 年全球电动车销量", 11, False, RGBColor(0xE6, 0xF4, 0xEE))])

# 2 · One chart, one conclusion: horizontal bars, the leader in the accent colour, values on the bars.
slide = prs.slides.add_slide(blank)
frame(slide, 2, "中国卖出了全球近三分之二的电动车", "2024 年全球电动车销量超过 1700 万辆；中国以外，新兴市场的增速首次逼近美国", "第 15–16 页")
data = CategoryChartData()
data.categories = ["中国", "美国", "其他市场"]
data.add_series("2024 年销量（百万辆）", (11.0, 1.6, 1.3))
chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, MARGIN, pt(140), pt(560), pt(300), data).chart
style_chart(chart, colors=[ACCENT, GREY_BAR, GREY_BAR], shown=["1100万+", "160万", "130万"])
chart.category_axis.reverse_order = True
text(slide, MARGIN, pt(122), pt(400), pt(16), [("2024 年电动车销量", 10, False, MUTED)])
rule(slide, pt(660), pt(150), 0)
for index, (big, small) in enumerate([("近 2/3", "中国占全球电动车销量的比重"),
                                      ("近一半", "中国新车销量中电动车的比重"),
                                      ("+40%", "美、中、欧以外市场的销量增速，达到 130 万辆")]):
    y = pt(150 + index * 92)
    rule(slide, pt(660), y, pt(240))
    text(slide, pt(660), y + pt(10), pt(240), pt(80), [(big, 28, True, ACCENT if index == 0 else INK), (small, 11, False, MUTED)])

# 3 · Actual versus expected, then where it comes from.
slide = prs.slides.add_slide(blank)
frame(slide, 3, "2025 年电动车销量预计突破 2000 万辆", "约占全球新车销量的四分之一；中国预计超过 1400 万辆，多于 2023 年的全球销量", "第 15、26–27 页")
data = CategoryChartData()
data.categories = ["2024 年", "2025 年（预期）"]
data.add_series("全球销量（百万辆）", (17, 20))
shape = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, MARGIN, pt(150), pt(300), pt(290), data)
style_chart(shape.chart, colors=[GREY_BAR, ACCENT], shown=["1700万+", "2000万+"])
text(slide, MARGIN, pt(126), pt(300), pt(16), [("全球电动车销量", 10, False, MUTED)])
data = CategoryChartData()
data.categories = ["中国", "欧洲", "其他市场"]
data.add_series("2025 年预期销量（百万辆）", (14, 4, 1.8))
shape = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, pt(400), pt(150), pt(320), pt(220), data)
style_chart(shape.chart, colors=[ACCENT, ACCENT_SOFT, ACCENT_SOFT], shown=["1400万+", "约400万", "约180万"])
shape.chart.category_axis.reverse_order = True
text(slide, pt(400), pt(126), pt(320), pt(16), [("2025 年各市场预期销量", 10, False, MUTED)])
text(slide, pt(400), pt(385), pt(320), pt(60), [("中国电动车销量份额预计约 60%", 11, False, INK),
                                                 ("其他市场预计增长 30% 以上", 11, False, INK)])
text(slide, pt(750), pt(150), pt(160), pt(260), [("+25%", 34, True, ACCENT), ("2025 年全球销量的预期增幅，与 2024 年相近", 11, False, MUTED)])

# 4 · Three small multiples, one shared frame: Ember's "the rise of new connections" rhythm.
slide = prs.slides.add_slide(blank)
frame(slide, 4, "补能时间要减半，充电网络要扩大数倍", "兆瓦级充电把重卡补能时间从 30 分钟缩短到 15 分钟；印度公共充电点要从 7.5 万个增至约 37.5 万个，日本目标约为现在的 9 倍", "第 116–117 页")
panels = [
    ("300kWh 重卡补能时间，分钟", ["350kW", "兆瓦级"], (30, 15), [GREY_BAR, ACCENT], '0'),
    ("印度公共充电点，万个", ["2024 年", "2030 年（STEPS）"], (7.5, 37.5), [GREY_BAR, ACCENT], '0.0'),  # 75 000 -> about 375 000
    ("日本公共充电点目标，倍数（相对 2024 年）", ["2024 年", "2030 年目标"], (1, 9), [GREY_BAR, ACCENT], '0"×"'),
]
for index, (caption, categories, values, colors, number_format) in enumerate(panels):
    x = MARGIN + index * pt(300)
    rule(slide, x, pt(132), pt(270), color=INK, weight=1)
    text(slide, x, pt(140), pt(270), pt(30), [(caption, 11, True, INK)])
    data = CategoryChartData()
    data.categories = categories
    data.add_series(caption, values)
    shape = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, pt(175), pt(270), pt(250), data)
    style_chart(shape.chart, colors=colors, number_format=number_format)
text(slide, MARGIN, pt(440), W - 2 * MARGIN, pt(20),
     [("注：印度 2030 年为 IEA 既定政策情景（STEPS）下的预测；日本为政府目标（30 万个）。", 10, False, MUTED)])

# 5 · The source's own chart across the page, with the conclusion and one annotation beside it.
slide = prs.slides.add_slide(blank)
frame(slide, 5, "十年间，电动车从小众走到每五辆新车中就有一辆", "全球电动车销量 2014–2024 年（IEA 原图）", "第 15 页，原图")
right = W - MARGIN - pt(210)
picture = slide.shapes.add_picture(str(FIGURE), MARGIN, pt(125), width=right - MARGIN - pt(30))
if picture.height > pt(300):  # a tall figure keeps the page's height, not the column's width
    ratio = pt(300) / picture.height
    picture.height, picture.width = pt(300), int(picture.width * ratio)
rule(slide, right, pt(135), W - right - MARGIN, color=INK, weight=1)
text(slide, right, pt(145), W - right - MARGIN, pt(280), [
    ("20%+", 30, True, ACCENT), ("2024 年全球新车中电动车的比重", 11, False, MUTED),
    ("350 万辆", 22, True, INK), ("2024 年比 2023 年多卖出的电动车，超过 2020 年全年销量", 11, False, MUTED),
    ("约 5800 万辆", 22, True, INK), ("2024 年底全球电动车保有量，约占乘用车的 4%", 11, False, MUTED)])

prs.save(OUT)
print(OUT)
