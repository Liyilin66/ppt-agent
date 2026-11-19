"""Figures of the source document, reproduced as they were published.

A report's charts hold data its text never spells out. Rather than redraw them
from guesses, the page shows the original: the embedded picture, its own
caption and credit line, and the page it stands on. A figure goes only to a
page that cites the page it comes from and is about the same thing.
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

MIN_WIDTH, MIN_HEIGHT = 300, 150   # smaller pictures are logos and ornaments
REPEATS = 3                        # a picture on this many pages is a running header
MAX_EDGE = 1600                    # larger than a slide can show
MIN_SHARED = 3                     # two-character runs a caption and a page must share
_CAPTION = re.compile(r"^\s*(图\s*\d+)\s+(\S[^\n]{1,48})$", re.M)
_CREDIT = re.compile(r"^\s*(?:资料)?来源[:：]\s*(\S[^\n]{0,60})$", re.M)


def _grams(text: str) -> set[str]:
    return {run[i:i + 2] for run in re.findall(r"[㐀-鿿A-Za-z0-9]+", text or "") for i in range(len(run) - 1)}


def extract(source_paths: list[str | Path], assets_dir: Path) -> list[dict[str, Any]]:
    """Every captioned picture of the PDFs, saved under ``assets_dir`` as PNG.

    A page is used only when its captions and its pictures pair off one to one;
    where they do not, which picture a caption belongs to would be a guess."""

    from PIL import Image
    from pypdf import PdfReader

    found: list[dict[str, Any]] = []
    for doc_index, path in enumerate(source_paths, 1):
        path = Path(path)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        try:
            reader = PdfReader(str(path))
            pages = [(number, page, list(page.images)) for number, page in enumerate(reader.pages, 1)]
        except Exception:  # noqa: BLE001 - a PDF whose pictures cannot be read has no figures
            continue
        seen = Counter((image.name, len(image.data)) for _, _, images in pages for image in images)
        texts = {number: page.extract_text() or "" for number, page, _ in pages}
        for number, page, images in pages:
            text = texts[number]
            captions = _CAPTION.findall(text)
            pictures = []
            for image in images:
                if seen[(image.name, len(image.data))] >= REPEATS:
                    continue
                try:
                    picture = image.image
                    picture.load()
                except Exception:  # noqa: BLE001
                    continue
                if picture.width >= MIN_WIDTH and picture.height >= MIN_HEIGHT:
                    pictures.append(picture)
            if not captions or len(captions) != len(pictures):
                continue
            credits = _CREDIT.findall(text)
            for index, ((label, title), picture) in enumerate(zip(captions, pictures)):
                # Transparent areas were white paper in the report.
                flat = Image.new("RGB", picture.size, "white")
                rgba = picture.convert("RGBA")
                flat.paste(rgba, mask=rgba.split()[3])
                if max(flat.size) > MAX_EDGE:
                    flat.thumbnail((MAX_EDGE, MAX_EDGE))
                name = f"fig_d{doc_index}_p{number}_{index + 1}.png"
                assets_dir.mkdir(parents=True, exist_ok=True)
                buffer = io.BytesIO()
                flat.save(buffer, format="PNG")
                (assets_dir / name).write_bytes(buffer.getvalue())
                found.append({
                    "id": name, "label": re.sub(r"\s+", " ", label), "title": title.strip(),
                    "credit": credits[index].strip() if len(credits) == len(captions) else None,
                    "document": path.name, "page": number, "ref": f"{path.name} 第{number}页",
                    "width": flat.width, "height": flat.height,
                    # what the report says around the figure: the only check on what the figure is read to mean
                    "context": "\n".join(texts.get(near, "") for near in (number - 1, number, number + 1)),
                })
        # Figures drawn as vector paths, or captioned another way ("Sources:", "IEA. CC BY"), on pages
        # where no embedded picture was paired with a caption (P5: the IEA report's charts).
        from ppt_agent.v2 import figure_discovery

        have = {item["page"] for item in found if item["document"] == path.name}
        found += [item for item in figure_discovery.discover(path, doc_index, assets_dir) if item["page"] not in have]
    return found


def caption(figure: dict[str, Any]) -> str:
    """The line under a reproduced figure: its own caption, credit and page."""

    parts = [f"{figure['label']} {figure['title']}".strip()]
    if figure.get("credit"):
        parts.append("来源：" + figure["credit"])
    parts.append(f"原文第 {figure['page']} 页")
    return " · ".join(parts)


def _cited(source: str | None, document: str) -> set[int]:
    pages: set[int] = set()
    for clause in re.split(r"[;；]", source or ""):
        if document not in clause:
            continue
        for first, last in re.findall(r"第\s*(\d+)(?:\s*[-–—]\s*(\d+))?\s*页", clause):
            pages.update(range(int(first), int(last or first) + 1))
    return pages


def assign(figures: list[dict[str, Any]], contents: dict[int, dict[str, Any]],
           taken: set[int] = frozenset()) -> dict[int, dict[str, Any]]:
    """At most one figure a page, each figure on one page: the page that cites
    the figure's own page and shares the most of its caption's words.
    ``taken`` are pages that already draw something (a chart)."""

    scored = []
    for figure in figures:
        words = _grams(re.sub(r"[（(]单位[^）)]*[）)]", "", figure["title"]))
        for page, content in contents.items():
            if page in taken or figure["page"] not in _cited(content.get("source"), figure["document"]):
                continue
            said = " ".join(str(part) for part in (
                content.get("title"), (content.get("highlight") or {}).get("text"), content.get("lead"),
                *(f"{item.get('heading') or ''} {item.get('body') or ''}" for item in content.get("items") or [])))
            shared = len(words & _grams(said))
            if shared >= MIN_SHARED:
                scored.append((shared, -page, figure))
    placed: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    for shared, negative_page, figure in sorted(scored, key=lambda entry: (-entry[0], -entry[1])):
        page = -negative_page
        if page in placed or figure["id"] in used:
            continue
        placed[page] = {"id": figure["id"], "caption": caption(figure), "ref": figure["ref"],
                        "ratio": round(figure["width"] / figure["height"], 3)}
        used.add(figure["id"])
    return placed


# --- reading a figure: is it fit to show, and can it be redrawn without guessing? -------------

LEGIBLE_PX = 10.0      # the smallest text of a figure, as it would stand on the slide (about 7.5pt)
PLATE_WIDTH = 680.0    # a figure beside text: seven of the page's twelve columns
WIDE_PLATE = 960.0     # a figure across the page, title above and reading below (P5: a wide report chart
                       # whose axis labels are unreadable at seven columns reads at this width)
MAX_ENLARGE = 1.25     # a picture stretched further than this goes soft
MAX_ELEMENTS = 24      # more boxes and labels than this cannot be taken in from a slide
MAX_LEVELS, MAX_LEVEL_ITEMS, MAX_STEPS, MAX_BARS = 5, 8, 6, 16

READ_SYSTEM = (
    "你在检查一份报告里的一张图，判断它能不能直接放进演示文稿，以及能不能按原样重画。只描述你在图里实际看到的，不要推测。\n"
    "1. smallest_text_px：图里最小的文字大约几个像素高（按这张图片自己的像素）。\n"
    "2. elements：图里大约有多少个独立的框、标签或数据项。\n"
    "3. kind：chart（柱、线、饼等数据图）、levels（上下分层的结构，如金字塔、分层架构）、flow（有先后顺序的流程）、other（其他）。\n"
    "4. 如果是 chart：values_printed 表示每个数值是否都以文字印在图上（只有坐标轴刻度、要靠柱子高度估计的，写 false）。"
    "values_printed 为 true 时，把 categories、values、unit 逐个照抄；为 false 时这三项留空，不要估计。"
    "forecast 写图例或纹样标明为预测的类别。\n"
    "5. 如果是 levels：levels 自上而下列出每一层。如果某一层旁边有一条线或箭头指向它的标注（例如右侧引出的名称），"
    "name 就是这个标注，items 是这一层里面印着的每个词；没有这样的标注时，name 是层内最醒目的词，items 是其余的词。"
    "逐字照抄，一个词一项，图上每一处文字都要出现在 name、items 或 groups 里。"
    "层里如果有带标题的子框（一个标题下面又框着几项），这一项写成 {\"name\":\"标题\",\"children\":[\"子项\"]}，不要把标题和子项并列。"
    "groups 是图上跨越一层或多层的括号、竖条或分组标注：name 是它的文字，covers 是它覆盖的层（自上而下从 1 数）。没有就留空。\n"
    "6. 如果是 flow：steps 按顺序列出每一步印着的文字。\n"
    "7. complete：你是否确信已经把图里所有的层、条目或数值都读全、没有遗漏也没有看不清的字。拿不准就写 false。\n"
    '只输出 JSON：{"smallest_text_px":0,"elements":0,"kind":"","values_printed":false,"categories":[],"values":[],"unit":"",'
    '"forecast":[],"levels":[{"name":"","items":[]}],"groups":[{"name":"","covers":[]}],"steps":[],"complete":false}'
)


CHECK_SYSTEM = (
    "有人准备把这张图按下面的内容重新画一遍。请对照原图逐项检查，只回答两件事："
    "missing 列出原图里印着、但重画内容里没有的文字或数值（坐标轴刻度、图注、纯装饰不算）；"
    "wrong 列出重画内容里和原图不一致的地方（文字不同、层次顺序不同、归属不同、数值不同；"
    "原图里一个标题框着几个子项、重画却把它们写成并列，或者反过来，也算不一致）。都没有就返回空列表。"
    '只输出 JSON：{"missing":[],"wrong":[]}'
)


GROUND_SYSTEM = (
    "下面是从一张图里读到的各层名称和几个分组的名称，以及这张图所在页前后的正文。"
    "对每个分组，在正文里找出说明「这个分组指的是哪些层」的原话。levels 只能从给出的层名里选；quote 逐字照抄正文，不超过 120 个字，"
    "里面要同时出现分组名和这些层名。正文没有说就把 levels 留空。不要根据常识推断。"
    '只输出 JSON：{"groups":[{"name":"","levels":[],"quote":""}]}'
)


def _named(redraw: dict[str, Any]) -> dict[str, Any]:
    """A label that marks exactly one level is that level's name; what the level
    was read as holding becomes its items."""

    levels = [dict(level) for level in redraw["levels"]]
    groups = []
    for group in redraw.get("groups") or []:
        if len(group["covers"]) == 1 and not levels[group["covers"][0] - 1].get("renamed"):
            level = levels[group["covers"][0] - 1]
            level["items"] = [word for word in re.split(r"\s+", level["name"]) if word] + level["items"]  # plain words
            level["name"], level["renamed"] = group["name"], True
        else:
            groups.append(group)
    return {"kind": "levels", "levels": [{"name": level["name"], "items": level["items"]} for level in levels],
            "groups": groups}


async def _ground(client: Any, redraw: dict[str, Any], context: str) -> tuple[dict[str, Any], str | None]:
    """Which levels a group spans is read off the picture's geometry, which a
    model reads badly. The report's own words decide it, or the figure is not redrawn."""

    groups = redraw.get("groups") or []
    if not groups:
        return redraw, None
    names = [level["name"] for level in redraw["levels"]]
    squashed = re.sub(r"\s+", "", context)
    try:
        reply = await client.complete_json(
            task="figure_ground", system=GROUND_SYSTEM,
            user=json.dumps({"层": names, "分组": [group["name"] for group in groups]}, ensure_ascii=False)
            + "\n正文：\n" + context[:6000], max_output_tokens=800, context={"groups": [group["name"] for group in groups]})
    except Exception as error:  # noqa: BLE001
        return redraw, "分组关系无法用正文核对：" + str(error)[:50]
    said = {str(entry.get("name")): entry for entry in (reply.get("groups") or []) if isinstance(entry, dict)} \
        if isinstance(reply, dict) else {}
    grounded = []
    for group in groups:
        entry = said.get(group["name"]) or {}
        quote = re.sub(r"\s+", "", str(entry.get("quote") or ""))
        levels = [name for name in entry.get("levels") or [] if name in names]
        if not levels or len(quote) < 8 or quote not in squashed or group["name"] not in quote \
                or any(name not in quote for name in levels):
            return redraw, f"分组「{group['name']}」包含哪些层，正文里没有原话可以证明"
        covers = sorted(names.index(name) + 1 for name in levels)
        grounded.append({"name": group["name"], "covers": covers, "quote": str(entry["quote"]).strip()[:160],
                         **({"read_from_picture": group["covers"]} if covers != group["covers"] else {})})
    return {**redraw, "groups": sorted(grounded, key=lambda group: group["covers"])}, None


def _same_reading(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Two readings agree when they hold the same words in the same places.

    Which levels a bracket spans is not compared: that is geometry, which the
    report's text decides, not the reading."""

    if first["kind"] != second["kind"]:
        return False
    if first["kind"] != "levels":
        return _norm(first) == _norm(second)

    def key(made: dict[str, Any]) -> Any:
        named = _named(made)
        return _norm([[(level["name"], sorted(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in level["items"]))
                       for level in named["levels"]],
                      sorted(group["name"] for group in named["groups"])])

    return key(first) == key(second)


READ_EDGE = 1500  # small print in a small picture is misread; enlarged, it is not


def _for_reading(path: Path) -> tuple[str, str]:
    """The picture as the reader sees it: enlarged when it is small."""

    import base64

    from PIL import Image

    picture = Image.open(path).convert("RGB")
    scale = READ_EDGE / max(picture.size)
    if scale > 1.05:
        picture = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return "image/png", base64.b64encode(buffer.getvalue()).decode("ascii")


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", "", value)
    if isinstance(value, list):
        return [_norm(item) for item in value]
    if isinstance(value, dict):
        return {key: _norm(item) for key, item in sorted(value.items())}
    return value


def _item(raw: Any) -> Any:
    """An entry of a level: a word, or a titled box holding words."""

    if isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
        children = [str(child).strip() for child in raw.get("children") or [] if str(child).strip()]
        if not name:
            return None
        return {"name": name, "children": children} if children else name
    return str(raw).strip() or None


def item_text(item: Any) -> str:
    """How an entry is written out: a titled box keeps what it holds in brackets."""

    return f"{item['name']}（{'、'.join(item['children'])}）" if isinstance(item, dict) else str(item)


def _structure(read: dict[str, Any]) -> dict[str, Any] | None:
    """What a reading says the figure holds, in a form two readings can be compared in."""

    kind = read.get("kind")
    if not read.get("complete"):
        return None
    if kind == "levels":
        levels = [{"name": str(level.get("name") or "").strip(),
                   "items": [made for made in (_item(item) for item in level.get("items") or []) if made]}
                  for level in read.get("levels") or [] if isinstance(level, dict)]
        groups = [{"name": str(group.get("name") or "").strip(),
                   "covers": sorted(int(number) for number in group.get("covers") or [] if isinstance(number, int))}
                  for group in read.get("groups") or [] if isinstance(group, dict) and str(group.get("name") or "").strip()]
        if 3 <= len(levels) <= MAX_LEVELS and all(level["name"] and len(level["items"]) <= MAX_LEVEL_ITEMS for level in levels) \
                and all(group["covers"] and 1 <= group["covers"][0] and group["covers"][-1] <= len(levels) for group in groups):
            return {"kind": "levels", "levels": levels, "groups": sorted(groups, key=lambda group: group["covers"])}
    if kind == "flow":
        steps = [str(step).strip() for step in read.get("steps") or [] if str(step).strip()]
        if 3 <= len(steps) <= MAX_STEPS:
            return {"kind": "flow", "steps": steps}
    if kind == "chart" and read.get("values_printed"):
        categories, values = read.get("categories") or [], read.get("values") or []
        if 2 <= len(categories) == len(values) <= MAX_BARS and all(isinstance(value, (int, float)) for value in values):
            return {"kind": "chart", "categories": [str(item).strip() for item in categories],
                    "values": [float(value) for value in values], "unit": str(read.get("unit") or "").strip(),
                    "forecast": [str(item).strip() for item in read.get("forecast") or []]}
    return None


async def assess(client: Any, checkpoints: Any, figure: dict[str, Any], image_path: Path) -> dict[str, Any]:
    """Whether a figure can be shown as it is, redrawn, or only cited.

    The figure is read twice, independently. It is redrawn only when both
    readings are complete and identical, word for word and value for value:
    a redrawing nobody can check must at least not depend on one reading."""

    name = f"figures/{figure['id']}.json"
    saved = checkpoints.load(name) if checkpoints is not None else None
    if saved is not None:
        return saved
    result: dict[str, Any] = {"id": figure["id"], "use": "original", "legible": True, "why": "", "redraw": None}
    readings: list[dict[str, Any]] = []

    def agreement() -> dict[str, Any] | None:
        """A structure two of the readings give alike."""
        made = [_structure(read) for read in readings]
        for index, first_made in enumerate(made):
            for other in made[index + 1:]:
                if first_made is not None and other is not None and _same_reading(first_made, other):
                    return first_made
        return None

    for attempt in range(3):
        if attempt == 2 and (agreement() is not None or not all(_structure(read) for read in readings)):
            break  # a third reading only settles two complete readings that differ
        try:
            reply = await client.complete_json(
                task="figure_read", system=READ_SYSTEM,
                user=f"这张图的图注：{figure['label']} {figure['title']}（第 {attempt + 1} 次独立读取）",
                images=[_for_reading(image_path)], max_output_tokens=1500, context={"figure": figure["id"]})
        except Exception as error:  # noqa: BLE001
            result.update(why="读图失败：" + str(error)[:80], error=True)
            return result
        readings.append(reply if isinstance(reply, dict) else {})
    first = readings[0]
    # The reader saw the picture enlarged; sizes are brought back to the picture's own pixels.
    enlarged = max(1.0, READ_EDGE / max(figure["width"], figure["height"]))
    enlarged = enlarged if enlarged > 1.05 else 1.0
    small = min((float(read.get("smallest_text_px") or 0) / enlarged for read in readings
                 if read.get("smallest_text_px")), default=0.0)
    shown = small * min(MAX_ENLARGE, WIDE_PLATE / max(figure["width"], 1)) if small else 0.0
    elements = max(int(read.get("elements") or 0) for read in readings)
    result.update(kind=first.get("kind"), elements=elements, smallest_text_px=round(small, 1), shown_text_px=round(shown, 1),
                  # A chart is read by its shape, not label by label: the element count limits diagrams only
                  # (P5: IEA's stacked bars have ~90 marks and read fine; their text size still has to).
                  legible=bool(shown) and shown >= LEGIBLE_PX and (elements <= MAX_ELEMENTS or first.get("kind") == "chart"),
                  # the narrowest the figure may be shown with its smallest text still readable
                  min_width=round(min(WIDE_PLATE, LEGIBLE_PX / small * figure["width"])) if small else None)
    one = agreement()
    agreed = one is not None
    if one is not None and one["kind"] == "levels":
        one = _named(one)
    complete = [made for made in (_structure(read) for read in readings) if made is not None]
    gaps: list[str] = []
    if agreed:
        # A third look, with the redrawing in hand: is anything in the figure missing from it, or different?
        try:
            check = await client.complete_json(
                task="figure_check", system=CHECK_SYSTEM,
                user="准备重画的内容：" + json.dumps(one, ensure_ascii=False),
                images=[_for_reading(image_path)], max_output_tokens=600, context={"figure": figure["id"]})
            gaps = [str(item) for key in ("missing", "wrong") for item in (check.get(key) or [])] \
                if isinstance(check, dict) and "missing" in check else ["复核没有给出结论"]
        except Exception as error:  # noqa: BLE001
            gaps = ["复核失败：" + str(error)[:60]]
        result["check"] = gaps
    if agreed and not gaps and one["kind"] == "levels":
        one, trouble = await _ground(client, one, figure.get("context") or "")
        if trouble:
            gaps = [trouble]
            result["check"] = gaps
    if agreed and not gaps:
        result.update(use="redraw", redraw=one, why="两次独立读图一致，对照原图没有遗漏或出入，跨层的分组关系有正文原话为证，可以按原样重画")
    elif result["legible"]:
        why = ("对照原图发现遗漏或出入：" + "；".join(gaps)[:60] if gaps else
               "几次读图的结果不一致，不重画" if len(complete) >= 2 else
               "数值没有印在图上，重画只能靠估计" if first.get("kind") == "chart" else "结构超出可重画的范围或没有读全")
        result.update(use="original", why=why + "；原图放到页面上字还看得清，可以直接用")
    else:
        reason = (f"原图有约 {elements} 个框和标签，放在一页上读不过来" if elements > MAX_ELEMENTS and first.get("kind") != "chart"
                  else f"原图放到页面上最小的字只有约 {shown:.0f} 像素，看不清")
        result.update(use="evidence", why=reason + "，而且无法可靠重画；只作为出处保留，不放上页面")
    result["readings_agree"] = agreed
    result["readings"] = [_structure(read) for read in readings]  # kept so a person can see what the readings differed on
    if checkpoints is not None:
        checkpoints.save(name, result)
    return result
