"""Wrap one slide's HTML into a complete document the browser can lay out."""

from __future__ import annotations

from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH

W, H = int(CANVAS_WIDTH), int(CANVAS_HEIGHT)

BASE_CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#fff}}
.slide{{width:{W}px;height:{H}px;position:relative;overflow:hidden}}
ul,ol{{list-style:none}}
table{{border-collapse:collapse}}
"""

# Draws a plain stand-in for each chart so screenshots show where it sits.
# The real chart is a native PowerPoint chart built from the same data.
CHART_SKETCH_JS = """
document.querySelectorAll('.chart[data-chart]').forEach(function (el) {
  var spec;
  try { spec = JSON.parse(el.dataset.chart); } catch (e) { return; }
  var values = ((spec.series || [])[0] || {}).values || [];
  var max = Math.max.apply(null, values.concat([1e-9]));
  var horizontal = spec.kind === 'bar';
  var holder = document.createElement('div');
  holder.style.cssText = 'display:flex;gap:6%;width:100%;height:100%;padding:6% 4% 12%;'
    + (horizontal ? 'flex-direction:column;align-items:flex-start;' : 'align-items:flex-end;');
  values.forEach(function (v, i) {
    var bar = document.createElement('div');
    var share = Math.max(v / max, 0.02) * 100;
    bar.style.cssText = 'flex:1;background:var(--chart,#888);opacity:' + (spec.highlight == null || spec.highlight === i ? 1 : 0.35) + ';'
      + (horizontal ? 'width:' + share + '%;' : 'height:' + share + '%;');
    holder.appendChild(bar);
  });
  el.appendChild(holder);
});
"""


def wrap(body_html: str, theme_css: str, font_css: str, backdrop_html: str = "") -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<style>{font_css}</style><style>{BASE_CSS}{theme_css}</style></head>"
        f'<body><div class="slide">{backdrop_html}{body_html}</div>'
        f"<script>{CHART_SKETCH_JS}</script></body></html>"
    )
