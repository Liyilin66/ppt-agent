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
   "data_idea": str or null, "speaker_notes": str}
]}
Rules:
- Exactly the requested number of pages, in narrative order.
- Titles are assertions, not labels ("复购率决定增长天花板", not "数据分析").
- No two pages in the deck may repeat the same title or the same point.
- points: 2-5 per page, each <= 60 characters, in the slide language.
- layout_hint variety matters: across the section, mix at least 3 different hints.
- data_idea: only when the section genuinely benefits from a chart/table; include
  plausible concrete numbers (they may come from the brief digest).
- speaker_notes: 2-4 spoken sentences the presenter reads aloud for this page,
  in the slide language; conversational, not a copy of the bullet points.
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
Propose a color theme AND a deck-unique style signature as ONLY a JSON object:
{"name": str, "mood": str, "motif": "corner_arc|side_band|dot_grid|top_rule|diagonal",
 "palette": {"background": "#RRGGBB", "surface": "#RRGGBB", "surface_alt": "#RRGGBB",
  "primary": "#RRGGBB", "primary_soft": "#RRGGBB", "secondary": "#RRGGBB",
  "accent": "#RRGGBB", "text": "#RRGGBB", "muted": "#RRGGBB", "on_primary": "#RRGGBB"},
 "style": {"composition": str, "decor": str, "shape_language": str, "cover_concept": str}}
Rules:
- background: near-white tinted toward the brand hue. surface: pure or near white.
- text on background must exceed WCAG 7:1 contrast; on_primary on primary >= 4.5:1.
- primary_soft: a pale tint of primary usable as a card/badge background.
- Choose a palette that fits the topic and audience; avoid neon on corporate topics.
- style: a concrete, topic-specific design language that makes THIS deck look unlike
  any other deck. Each field is one short English sentence a designer can execute:
  * composition: the grid/alignment tendency (e.g. "asymmetric editorial layout,
    oversized left-aligned titles, 2/3 + 1/3 splits").
  * decor: recurring decorative devices (e.g. "hairline rules and small numbered
    chips; no blobs or circles").
  * shape_language: which shapes dominate (e.g. "sharp parallelograms and diagonal
    cuts echoing motion").
  * cover_concept: an art-direction idea for the cover and section dividers that is
    specific to the topic (e.g. for a marine-biology deck: "deep gradient with a
    rising bubble column of ellipses on the right third").
  Avoid the generic combo "two soft circles + left-aligned title" — invent something
  tailored to the topic."""


def build_theme_user_prompt(brief: ContentBrief) -> str:
    return (
        f"Topic: {brief.topic}\nAudience: {brief.audience}\nTone: {brief.tone}\n"
        f"Purpose: {brief.purpose}\nReturn the JSON theme."
    )


_ELEMENT_SCHEMA_TEMPLATE = """ELEMENT TYPES (each needs a unique "id"; sizes/positions in canvas units):
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
- image: {{"type":"image","id":str,"frame":...,"src":<EXACT file name from AVAILABLE
          IMAGES>,"label":str}} — only when the prompt lists AVAILABLE IMAGES; never
          invent src values. Contain-fit into the frame; keep the frame's aspect
          close to the image's.

COLOR ROLES (the ONLY colors allowed; never write hex values):
background, surface, surface_alt, primary, primary_soft, secondary, accent,
text, muted, on_primary, success, warning, danger."""


PAGE_DESIGN_SYSTEM_TEMPLATE = (
    """You are the slide designer of a presentation studio.
Design ONE slide as a JSON layout on a 1280x720 canvas (x grows right, y grows down).
Reply with ONLY a JSON object:
{{"role": "content|quote|stats|comparison|timeline",
  "title": str, "background": <color role>,
  "elements": [ <element>, ... ], "speaker_notes": str}}

"""
    + _ELEMENT_SCHEMA_TEMPLATE
    + """

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
   pages — do not default to the same 3-card grid. Rotate structures (full-width
   band, split, stacked rows, oversized numeral, big quote, chart-dominant) while
   staying inside the deck style signature.
8. Numbers: use stat/stat_label roles for KPI figures. Charts only when the brief
   provides or implies real numbers; 3-8 categories max.
9. FILL THE CANVAS: content must cover well over half of the usable area and the
   bottom half must never be left empty. When the brief is light, enlarge cards,
   typography and spacing to fill the page — never shrink everything into one
   corner and leave the rest blank.
10. Charts and tables render their own labels: keep every other element out of
   their frames (no stat numbers or captions on top of a chart). Give tables at
   least 30 units of height per row, or cut rows.
11. All visible copy in {language}. Speaker notes 2-4 sentences, same language.
12. No overlapping text frames; text may only overlap a shape that acts as its card.

EXAMPLE (a stats page, abbreviated):
{{"role":"stats","title":"增长的三个引擎","background":"background","elements":[
 {{"type":"text","id":"t","frame":{{"x":64,"y":60,"w":700,"h":50}},"text":"增长的三个引擎","role":"title"}},
 {{"type":"shape","id":"c1","frame":{{"x":64,"y":160,"w":352,"h":300}},"shape":"rounded_rectangle","fill":"surface"}},
 {{"type":"icon","id":"i1","frame":{{"x":96,"y":192,"w":56,"h":56}},"name":"rocket","color":"primary"}},
 {{"type":"text","id":"s1","frame":{{"x":96,"y":268,"w":288,"h":56}},"text":"3.2x","role":"stat"}},
 {{"type":"text","id":"l1","frame":{{"x":96,"y":330,"w":288,"h":30}},"text":"获客效率提升","role":"stat_label"}},
 {{"type":"text","id":"d1","frame":{{"x":96,"y":368,"w":288,"h":72}},"text":"投放结构优化后单客成本下降","role":"body_small"}}
 /* two more cards at x:464 and x:864 */]}}"""
)


def build_page_design_system(language: str) -> str:
    return PAGE_DESIGN_SYSTEM_TEMPLATE.format(
        icons=icon_catalog_for_prompt(), language=language
    )


def format_available_images_block(available_images: list[dict] | None) -> str:
    if not available_images:
        return ""
    lines = []
    for item in available_images:
        size = ""
        if item.get("width") and item.get("height"):
            size = f" ({item['width']}x{item['height']}px)"
        lines.append(f"- {item['src']}{size}: {item.get('description', '')}".rstrip())
    return (
        "AVAILABLE IMAGES (user-provided files; place with the \"image\" element "
        "ONLY where one genuinely supports this page's brief — most pages should "
        "use none):\n" + "\n".join(lines) + "\n\n"
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
    available_images: list[dict] | None = None,
) -> str:
    neighbors = "; ".join(neighbor_titles) or "(none)"
    return (
        f"Deck: {deck_title} ({page_number}/{total_pages})\n"
        f"Section: {section_title}\n"
        f"Theme mood: {theme.mood} (motif: {theme.motif}; colors come from roles only)\n"
        f"Audience: {brief.audience} | Tone: {brief.tone}\n\n"
        f"DECK STYLE SIGNATURE (every page must express it):\n"
        f"{theme.style.as_prompt_block()}\n\n"
        f"{format_available_images_block(available_images)}"
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
Fix ONLY what the issues require (resize/move/rebalance frames, enlarge cards
and typography to fill empty regions, move elements off charts/tables, remove
table rows, shorten text, change colors to readable roles), preserving the
visual intent and the element schema.
Reply with ONLY the corrected slide JSON object in the exact same format."""


def build_repair_user_prompt(page_json: str, issues: list[str]) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in issues)
    return f"Slide JSON:\n{page_json}\n\nQA issues to fix:\n{issue_lines}"


ANCHOR_DESIGN_SYSTEM_TEMPLATE = (
    """You are the art director of a presentation studio, designing one STRUCTURAL
page (cover, section divider, or closing) as a JSON layout on a 1280x720 canvas
(x grows right, y grows down). Reply with ONLY a JSON object:
{{"role": "cover|section_divider|closing", "title": str,
  "background": <color role>,
  "background_gradient": {{"start": <color role>, "end": <color role>, "angle_deg": 0-359}} (optional),
  "elements": [ <element>, ... ]}}

"""
    + _ELEMENT_SCHEMA_TEMPLATE
    + """

STRUCTURAL PAGE RULES:
1. This page must read as a cover / divider / closing AT A GLANCE — one huge
   hero title, a strong backdrop, minimal supporting text. If it could be
   mistaken for a content slide, it is wrong.
2. Covers and closings take a dark hero backdrop: background "primary" or
   "secondary", usually with a background_gradient between them. NEVER plain
   "background" or "surface" as the page background for a cover/closing.
3. The hero title uses the "display" role (or "section" on dividers) and owns
   a large calm area. At most 3-4 text elements total, colored "on_primary"
   on dark backdrops.
4. Decoration means large abstract shapes (bleeding off the edges is
   encouraged) that execute the deck's cover concept and relate to the TOPIC.
   FORBIDDEN: charts, tables, bullet lists, architecture diagrams, terminal or
   UI mockups, icon networks with connector lines — those are content-page
   devices. 3-10 elements total.
5. Full-bleed art direction: no chrome strips are reserved. Never fall back to
   the generic "two translucent circles + left title" template.
6. Section dividers: make the section number a prominent graphic element and
   include a subtle progress cue (dots or a thin bar showing section X of N).
7. Closing pages: a short thanks line plus one quiet echo of the deck title.
8. All visible copy in {language}."""
)


def build_anchor_design_system(language: str) -> str:
    return ANCHOR_DESIGN_SYSTEM_TEMPLATE.format(
        icons=icon_catalog_for_prompt(), language=language
    )


def build_anchor_design_user_prompt(
    *,
    kind: str,
    brief: ContentBrief,
    theme: ThemeSpec,
    deck_title: str,
    subtitle: str | None = None,
    section_index: int | None = None,
    section_count: int | None = None,
    section_title: str | None = None,
    section_goal: str | None = None,
) -> str:
    lines = [
        f"Deck: {deck_title}",
        f"Topic: {brief.topic} | Audience: {brief.audience} | Tone: {brief.tone}",
        f"Theme mood: {theme.mood} (motif: {theme.motif}; colors come from roles only)",
        "",
        "DECK STYLE SIGNATURE (must drive this design):",
        theme.style.as_prompt_block(),
        "",
    ]
    if kind == "cover":
        lines += [
            "ASSIGNMENT: design the COVER page.",
            f"Deck title: {deck_title}",
            f"Subtitle: {subtitle or '(none)'}",
        ]
    elif kind == "section_divider":
        lines += [
            f"ASSIGNMENT: design the SECTION DIVIDER for section {section_index} of {section_count}.",
            f"Section title: {section_title}",
            f"Section goal: {section_goal or '(none)'}",
        ]
    else:
        lines += [
            "ASSIGNMENT: design the CLOSING page.",
            f"Deck title to echo: {deck_title}",
        ]
    lines.append("Design this structural page now. Return ONLY the JSON object.")
    return "\n".join(lines)


REVISION_PLAN_SYSTEM = """You are the revision planner of a presentation studio.
A deck is already generated; the user asks for changes in natural language.
Decide the minimal set of edits. Reply with ONLY a JSON object:
{"reply": str, "theme_instruction": str or null,
 "all_pages_instruction": str or null, "pages": [
  {"page_number": int, "instruction": str,
   "new_brief": {"title": str, "summary": str, "points": [str, ...],
     "layout_hint": "auto|cards|two_column|stats|timeline|comparison|quote|chart|table|list",
     "data_idea": str or null, "speaker_notes": str} or null}
]}
YOUR CAPABILITIES — plan only with these levers:
1. Redesign individual pages (layout and/or content) via `pages`.
2. Redesign EVERY page with one shared directive via `all_pages_instruction` —
   for recurring elements painted inside the pages themselves (e.g. "remove
   the left vertical rail on every page", "shrink the giant page numerals").
   This is expensive (one model call per page), so use it only when the change
   truly lives inside every page's elements.
3. Deck-wide visual settings via `theme_instruction`: the color palette, the
   style signature, the decorative motif (corner_arc/side_band/dot_grid/
   top_rule/diagonal, or "none" to remove stamped corner/side decorations),
   and the page chrome toggles — page numbers, the footer line, the section
   kicker (e.g. "hide page numbers on every page").
IMPORTANT: theme_instruction only changes deck-level tokens and stamped
furniture. It can NOT remove or alter shapes/text that the designer painted
inside each page (side rails, number chips, decorative panels) — those need
`all_pages_instruction` or per-page entries.
NOT possible: adding/removing/reordering pages, per-page chrome, animations,
images the deck does not have.

Rules:
- reply: 1-3 sentences to the user, in the user's language, saying what you will
  change. BE HONEST: promise only what the levers above can actually do. If the
  request (or part of it) is impossible, say so plainly in reply and leave that
  part out of the plan — never pretend it will be done.
- theme_instruction: fill ONLY for deck-wide requests; write an English
  directive for the art director covering palette / motif / chrome toggles /
  style. Deck-wide-only changes need no page entries.
- pages: exactly the pages whose layout or content must change, using the page
  numbers from the deck structure. Never include TOC pages. Never add or remove
  pages — if the user asks for that, explain in reply that page structure is
  edited in the outline step.
- instruction: a concrete English design/content directive for that single page.
- new_brief: only when the page's CONTENT (title/points/spoken notes) must change;
  write it fully, in the slide language. Otherwise null.
- Global rewording/restyle requests may list many pages; global recolor/chrome
  changes should use theme_instruction with an empty pages list."""


def build_revision_plan_user_prompt(
    *,
    message: str,
    deck_summary: str,
    selected_pages: list[int] | None = None,
    attachments_note: str = "",
) -> str:
    selected = (
        f"Pages the user currently has selected: {selected_pages}\n"
        if selected_pages
        else ""
    )
    return (
        f"DECK STRUCTURE:\n{deck_summary}\n\n"
        f"{attachments_note}"
        f"{selected}"
        f"USER CHANGE REQUEST:\n{message.strip()}\n\n"
        "Plan the revision now. Return ONLY the JSON object."
    )


THEME_REVISE_SYSTEM = (
    THEME_SYSTEM
    + """

You are REVISING an existing theme: apply the instruction, keep every field the
instruction does not touch identical to the current theme, and keep all
contrast rules satisfied.
Two extra top-level keys are available when revising (echo them from the
current theme when untouched):
- "motif": set to "none" to remove deck-wide side/corner decorations.
- "chrome": {"show_page_number": bool, "show_footer": bool,
   "show_section_kicker": bool} — deck-wide page furniture toggles (e.g. hide
   page numbers on every page)."""
)


def build_theme_revise_user_prompt(*, current_theme_json: str, instruction: str) -> str:
    return (
        f"CURRENT THEME:\n{current_theme_json}\n\n"
        f"REVISION INSTRUCTION:\n{instruction.strip()}\n\n"
        "Return the full revised JSON theme."
    )


def append_revision_block(
    user_prompt: str, *, instruction: str, current_page_json: str | None
) -> str:
    current = (
        f"CURRENT DESIGN OF THIS SLIDE (JSON):\n{current_page_json}\n\n"
        if current_page_json
        else ""
    )
    return (
        f"{user_prompt}\n\n"
        f"{current}"
        f"REVISION REQUEST FOR THIS SLIDE:\n{instruction.strip()}\n"
        "Redesign the slide applying this revision. Keep everything the request "
        "does not touch close to the current design. Return ONLY the JSON object."
    )


IMAGE_DIGEST_SYSTEM = """You are the research assistant of a presentation studio.
Look at the attached image and report what a slide writer needs from it.
Reply with ONLY a JSON object:
{"description": str, "extracted_text": str}
Rules:
- description: 2-4 sentences, in the language given by the user prompt, covering
  what the image shows, any data/figures it contains, and what it could support
  in a presentation.
- extracted_text: any legible text/numbers in the image, transcribed compactly;
  empty string if none."""


def build_image_digest_user_prompt(*, name: str, language: str) -> str:
    return (
        f"Image file name: {name}\n"
        f"Write the description in: {language}\n"
        "Analyze the attached image now. Return ONLY the JSON object."
    )


IMAGE_CLASSIFY_SYSTEM = """You are the intake analyst of a presentation studio.
Look at the attached image and decide how it relates to slide making.
Reply with ONLY a JSON object:
{"category": "slide|informative|unrelated", "confidence": 0.0-1.0,
 "reasoning": str, "description": str, "extracted_text": str,
 "title_guess": str}
Categories:
- "slide": the image IS a complete presentation page — a PPT screenshot, an
  AI-generated slide, an infographic or poster-style page with deliberate
  title/body/visual layout.
- "informative": not a finished slide, but it carries content usable in a
  presentation — product screenshots, flowcharts, data charts, whiteboard
  photos, diagrams, documents.
- "unrelated": no presentation-usable information — selfies, pets, scenery,
  random photos.
Rules:
- reasoning: ONE short sentence for the end user, in the language given in the
  user prompt, explaining the judgement.
- description: 2-4 sentences on what the image shows (same language).
- extracted_text: legible text/numbers transcribed compactly; "" if none.
- title_guess: a short title for this image's content (same language)."""


def build_image_classify_user_prompt(*, name: str, language: str) -> str:
    return (
        f"Image file name: {name}\n"
        f"Write reasoning/description in: {language}\n"
        "Classify the attached image now. Return ONLY the JSON object."
    )


THEME_FROM_IMAGES_SYSTEM = (
    THEME_SYSTEM
    + """

You are EXTRACTING the theme from the attached reference image(s) instead of
inventing one: sample the dominant background, primary brand color, secondary
and accent hues, and text colors directly from the images (adjusted only as
needed to satisfy the contrast rules). The style signature should describe the
composition/decoration language actually visible in the images so rebuilt pages
feel native to them."""
)


def build_theme_from_images_user_prompt(*, names: list[str], language: str) -> str:
    listed = "\n".join(f"- {name}" for name in names)
    return (
        f"Reference images:\n{listed}\n"
        f"Language context: {language}\n"
        "Extract the JSON theme from the attached image(s) now."
    )


_REBUILD_ROUTE_BRIEFS = {
    "rebuild": """ASSIGNMENT: faithful EDITABLE REBUILD of the attached slide image.
- Reproduce the layout: same title, same text (verbatim, fix obvious OCR noise),
  same visual hierarchy, positions proportional to the original on the
  1280x720 canvas.
- Recreate shapes, cards, charts and tables as native elements (charts need the
  visible data values; estimate honestly from the image).
- Photographic or illustration regions can NOT become editable shapes: cover
  each with an image element whose src is "crop:x,y,w,h" using coordinates
  normalized 0-1 relative to the original image; it will be cropped from the
  source automatically. Use at most 4 crop regions.
- Map colors to the closest theme color roles.""",
    "design_from_content": """ASSIGNMENT: design ONE NEW slide from the information in the attached image.
- Do NOT copy the layout. Understand the content, then reorganize it into a
  titled, well-structured page (points, stats, chart/table if the image
  contains real data) following the deck style signature.""",
    "embed_with_notes": """ASSIGNMENT: design ONE slide that PRESENTS the attached image itself.
- Place the original image prominently with an image element whose src is
  exactly the provided file name (never "crop:...").
- Surround it with editable interpretation: a title, 2-4 takeaway points or
  annotations, and one conclusion line. The image is the exhibit; your text
  explains it.""",
    "style_reference": """ASSIGNMENT: design ONE style-sample slide in the visual style of the attached
image, without copying its content.
- Show off the extracted theme: a hero title, subtitle, two or three sample
  cards/stats with placeholder-but-plausible copy about the style itself
  (e.g. what the palette and composition communicate).
- Do NOT reproduce any concrete text, data or subject matter from the image.""",
    "extract_text": """ASSIGNMENT: design ONE slide holding ONLY the text/data extracted from the
attached image.
- Transcribe headings, bullet text, figures; rebuild tables as table elements
  and charted data as chart elements with the visible values.
- Add NO new content beyond a minimal organizing title. No decorative
  storytelling; this is a faithful, editable transcription.""",
}


def build_image_page_user_prompt(
    *,
    route: str,
    theme: ThemeSpec,
    name: str,
    page_number: int,
    total_pages: int,
    language: str,
    user_note: str | None = None,
) -> str:
    assignment = _REBUILD_ROUTE_BRIEFS.get(route, _REBUILD_ROUTE_BRIEFS["design_from_content"])
    note = f"\nUser note for this image: {user_note.strip()}" if user_note else ""
    return (
        f"Source image file: {name} (page {page_number}/{total_pages})\n"
        f"Slide language: {language}\n"
        f"Theme mood: {theme.mood} (motif: {theme.motif}; colors come from roles only)\n\n"
        f"DECK STYLE SIGNATURE:\n{theme.style.as_prompt_block()}\n\n"
        f"{assignment}{note}\n\n"
        "Design this one slide now. Return ONLY the JSON object."
    )


IMAGE_PAGE_SYSTEM_TEMPLATE = (
    """You are the slide reconstruction specialist of a presentation studio.
You look at ONE attached source image and produce ONE slide as a JSON layout on
a 1280x720 canvas (x grows right, y grows down). Reply with ONLY a JSON object:
{{"role": "content", "title": str, "background": <color role>,
  "background_gradient": {{"start": <color role>, "end": <color role>, "angle_deg": 0-359}} (optional),
  "show_chrome": false,
  "elements": [ <element>, ... ], "speaker_notes": str}}

"""
    + _ELEMENT_SCHEMA_TEMPLATE
    + """

RECONSTRUCTION RULES:
1. Follow the ASSIGNMENT in the user message exactly — it defines whether you
   faithfully rebuild, redesign, embed, style-sample or transcribe.
2. Honest editability: text, shapes, charts and tables become native editable
   elements; photographic regions stay images (via "crop:x,y,w,h" src values,
   normalized 0-1 against the source image) — never fake a photo with shapes.
3. The full canvas is yours (show_chrome is false); keep text inside
   x:32..1248, y:24..696 so nothing clips.
4. Colors come from theme roles only. Text must stay readable on its backdrop.
5. Charts/tables need real values visible in the image; never invent numbers in
   faithful modes. 4-18 elements. speaker_notes: 1-3 sentences in the slide
   language describing this page.
6. All visible copy in {language}."""
)


def build_image_page_system(language: str) -> str:
    return IMAGE_PAGE_SYSTEM_TEMPLATE.format(
        icons=icon_catalog_for_prompt(), language=language
    )
