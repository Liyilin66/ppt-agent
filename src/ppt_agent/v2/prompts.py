"""Prompt contracts for every v2 model task.

Prompts are English (models follow structured instructions better), while all
slide-visible copy is written in ``brief.language``. Each task returns bare
JSON that downstream code validates strictly and reconciles deterministically.
"""

from __future__ import annotations

import json

from ppt_agent.v2.design import ThemeSpec
from ppt_agent.v2.icons import icon_catalog_for_prompt
from ppt_agent.v2.planning import ContentBrief, PageBrief, SectionOutline


BRIEF_SYSTEM = """You are the requirements analyst of a presentation studio.
Turn the user's request into a normalized JSON brief for a slide deck.
Reply with ONLY a JSON object with keys:
topic, deck_title, subtitle, audience, purpose, tone, language,
key_points (array of strings), must_include (array), must_avoid (array).
Rules:
- language: BCP-47 code of the slide copy language. Default zh-CN unless the
  user asks for another language.
- deck_title: punchy, <= 40 characters, in the slide language.
- key_points: 5-12 concrete points that the deck must cover.
- Never invent facts that contradict the provided source material."""


def build_brief_user_prompt(
    user_prompt: str,
    *,
    page_count: int,
    source_digest: str | None,
    search_digest: str | None,
) -> str:
    parts = [
        f"User request:\n{user_prompt.strip()}",
        f"Planned deck length: {page_count} slides.",
    ]
    if source_digest:
        parts.append(f"Source document digest:\n{source_digest.strip()[:6000]}")
    if search_digest:
        parts.append(f"Web research notes:\n{search_digest.strip()[:4000]}")
    return "\n\n".join(parts)


OUTLINE_SYSTEM = """You are the chief storyteller of a presentation studio.
Design the section structure for a long slide deck.
Reply with ONLY a JSON object:
{"deck_title": str, "subtitle": str, "sections": [
  {"title": str, "goal": str, "content_pages": int, "talking_points": [str, ...]}
]}
Rules:
- Sections tell one continuous story: hook -> context -> core chapters -> proof -> action.
- Use the slide language from the brief for all titles and points.
- content_pages counts ONLY normal content slides (cover/TOC/dividers/closing are added separately).
- 4-12 sections. Weight content_pages by importance; they will be rescaled to the exact budget.
- talking_points: 3-8 per section, concrete and non-overlapping."""


def build_outline_user_prompt(brief: ContentBrief, *, content_budget: int) -> str:
    return (
        f"Brief:\n{brief.model_dump_json(indent=None)}\n\n"
        f"Total content slides to plan for: about {content_budget}. "
        "Return the JSON outline."
    )


SECTION_PAGES_SYSTEM = """You are the content planner of a presentation studio.
Write one brief per slide for a single section of a long deck.
Reply with ONLY a JSON object: {"pages": [
  {"title": str, "summary": str, "points": [str, ...],
   "layout_hint": "auto|cards|two_column|stats|timeline|comparison|quote|chart|table|list",
   "data_idea": str or null}
]}
Rules:
- Exactly the requested number of pages, in narrative order.
- Titles are assertions, not labels ("复购率决定增长天花板", not "数据分析").
- No two pages in the deck may repeat the same title or the same point.
- points: 2-5 per page, each <= 60 characters, in the slide language.
- layout_hint variety matters: across the section, mix at least 3 different hints.
- data_idea: only when the section genuinely benefits from a chart/table; include
  plausible concrete numbers (they may come from the brief digest).
- Every slide-visible string in the slide language from the brief."""


def build_section_pages_user_prompt(
    brief: ContentBrief,
    section: SectionOutline,
    *,
    page_count: int,
    deck_title: str,
    prior_titles: list[str],
) -> str:
    prior = "\n".join(f"- {title}" for title in prior_titles[-40:]) or "(none yet)"
    return (
        f"Deck: {deck_title}\n"
        f"Brief topic: {brief.topic}\nAudience: {brief.audience}\n"
        f"Language: {brief.language}\nTone: {brief.tone}\n"
        f"Source digest: {(brief.source_digest or '(none)')[:3000]}\n\n"
        f"Section: {section.title}\nGoal: {section.goal}\n"
        f"Talking points: {json.dumps(section.talking_points, ensure_ascii=False)}\n\n"
        f"Titles already used on earlier pages (do not repeat):\n{prior}\n\n"
        f"Write briefs for exactly {page_count} pages."
    )


THEME_SYSTEM = """You are the art director of a presentation studio.
Propose a color theme for a slide deck as ONLY a JSON object:
{"name": str, "mood": str, "motif": "corner_arc|side_band|dot_grid|top_rule|diagonal",
 "palette": {"background": "#RRGGBB", "surface": "#RRGGBB", "surface_alt": "#RRGGBB",
  "primary": "#RRGGBB", "primary_soft": "#RRGGBB", "secondary": "#RRGGBB",
  "accent": "#RRGGBB", "text": "#RRGGBB", "muted": "#RRGGBB", "on_primary": "#RRGGBB"}}
Rules:
- background: near-white tinted toward the brand hue. surface: pure or near white.
- text on background must exceed WCAG 7:1 contrast; on_primary on primary >= 4.5:1.
- primary_soft: a pale tint of primary usable as a card/badge background.
- Choose a palette that fits the topic and audience; avoid neon on corporate topics."""


def build_theme_user_prompt(brief: ContentBrief) -> str:
    return (
        f"Topic: {brief.topic}\nAudience: {brief.audience}\nTone: {brief.tone}\n"
        f"Purpose: {brief.purpose}\nReturn the JSON theme."
    )


PAGE_DESIGN_SYSTEM_TEMPLATE = """You are the slide designer of a presentation studio.
Design ONE slide as a JSON layout on a 1280x720 canvas (x grows right, y grows down).
Reply with ONLY a JSON object:
{{"role": "content|quote|stats|comparison|timeline",
  "title": str, "background": <color role>,
  "elements": [ <element>, ... ], "speaker_notes": str}}

ELEMENT TYPES (each needs a unique "id"; sizes/positions in canvas units):
- text:  {{"type":"text","id":str,"frame":{{"x","y","w","h"}},"text":str,
          "role":"display|title|subtitle|h3|body|body_small|caption|kicker|stat|stat_label|quote",
          "color":<color role, optional>,"align":"left|center|right",
          "valign":"top|middle|bottom","bullet":"none|dot|number"}}
- shape: {{"type":"shape","id",str,"frame":...,"shape":"rectangle|rounded_rectangle|pill|ellipse|triangle|right_arrow|chevron|diamond|hexagon|parallelogram",
          "fill":<color role or null>,"fill_alpha":0..1,"stroke":<color role or null>,
          "gradient":{{"start":<role>,"end":<role>,"angle_deg":0-359}} (optional)}}
- line:  {{"type":"line","id":str,"x1","y1","x2","y2","color":<role>,"width":pt,"dash":bool}}
- icon:  {{"type":"icon","id":str,"frame":...,"name":<one of: {icons}>,
          "color":<role>,"background":<role or null>,"background_shape":"circle|rounded|none"}}
- chart: {{"type":"chart","id":str,"frame":...,"chart":"bar|column|line|area|pie|doughnut",
          "categories":[str,...],"series":[{{"name":str,"values":[num,...]}}],
          "show_legend":bool,"show_data_labels":bool}}
- table: {{"type":"table","id":str,"frame":...,"headers":[str,...],"rows":[[str,...],...]}}

COLOR ROLES (the ONLY colors allowed; never write hex values):
background, surface, surface_alt, primary, primary_soft, secondary, accent,
text, muted, on_primary, success, warning, danger.

COMPOSITION RULES:
1. Safe margins: keep elements inside x:64..1216, y:56..664. Top strip y<56 and
   bottom strip y>664 are reserved for deck chrome.
2. Title block: kicker-style page title around y:56..120, then content below y:140.
3. Build on a grid: align edges; equal gaps (24 or 32) between sibling cards.
4. Cards are rounded_rectangle with fill "surface" on "background", or
   "primary_soft"/"surface_alt" for emphasis; put text INSIDE card frames with
   ~24 unit padding.
5. Layer order = array order: backgrounds/shapes first, then text/icons on top.
6. Text must fit: body text ~13pt needs about 24 units per line of height; never
   put more than ~90 characters of CJK text into a 400x100 frame. Prefer short
   phrases over sentences.
7. Density: 4-16 elements. One clear focal point per slide. Vary layouts across
   pages — do not default to the same 3-card grid.
8. Numbers: use stat/stat_label roles for KPI figures. Charts only when the brief
   provides or implies real numbers; 3-8 categories max.
9. All visible copy in {language}. Speaker notes 2-4 sentences, same language.
10. No overlapping text frames; text may only overlap a shape that acts as its card.

EXAMPLE (a stats page, abbreviated):
{{"role":"stats","title":"增长的三个引擎","background":"background","elements":[
 {{"type":"text","id":"t","frame":{{"x":64,"y":60,"w":700,"h":50}},"text":"增长的三个引擎","role":"title"}},
 {{"type":"shape","id":"c1","frame":{{"x":64,"y":160,"w":352,"h":300}},"shape":"rounded_rectangle","fill":"surface"}},
 {{"type":"icon","id":"i1","frame":{{"x":96,"y":192,"w":56,"h":56}},"name":"rocket","color":"primary"}},
 {{"type":"text","id":"s1","frame":{{"x":96,"y":268,"w":288,"h":56}},"text":"3.2x","role":"stat"}},
 {{"type":"text","id":"l1","frame":{{"x":96,"y":330,"w":288,"h":30}},"text":"获客效率提升","role":"stat_label"}},
 {{"type":"text","id":"d1","frame":{{"x":96,"y":368,"w":288,"h":72}},"text":"投放结构优化后单客成本下降","role":"body_small"}}
 /* two more cards at x:464 and x:864 */]}}"""


def build_page_design_system(language: str) -> str:
    return PAGE_DESIGN_SYSTEM_TEMPLATE.format(
        icons=icon_catalog_for_prompt(), language=language
    )


def build_page_design_user_prompt(
    *,
    brief: ContentBrief,
    theme: ThemeSpec,
    deck_title: str,
    section_title: str,
    page_brief: PageBrief,
    page_number: int,
    total_pages: int,
    neighbor_titles: list[str],
) -> str:
    neighbors = "; ".join(neighbor_titles) or "(none)"
    return (
        f"Deck: {deck_title} ({page_number}/{total_pages})\n"
        f"Section: {section_title}\n"
        f"Theme mood: {theme.mood} (motif: {theme.motif}; colors come from roles only)\n"
        f"Audience: {brief.audience} | Tone: {brief.tone}\n\n"
        f"PAGE BRIEF\nTitle: {page_brief.title}\n"
        f"Summary: {page_brief.summary}\n"
        f"Points: {json.dumps(page_brief.points, ensure_ascii=False)}\n"
        f"Layout hint: {page_brief.layout_hint}\n"
        f"Data idea: {page_brief.data_idea or '(none)'}\n\n"
        f"Adjacent page titles (design differently from them): {neighbors}\n"
        "Design this one slide now. Return ONLY the JSON object."
    )


REPAIR_SYSTEM = """You are the design reviewer of a presentation studio.
You receive one slide's JSON layout plus a list of concrete QA issues.
Fix ONLY what the issues require (resize/move frames, shorten text, change
colors to readable roles), preserving the visual intent and the element schema.
Reply with ONLY the corrected slide JSON object in the exact same format."""


def build_repair_user_prompt(page_json: str, issues: list[str]) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in issues)
    return f"Slide JSON:\n{page_json}\n\nQA issues to fix:\n{issue_lines}"
