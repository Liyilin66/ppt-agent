"""An independent look at several versions of one page, and which to keep.

The reviewer sees pictures of the page as PowerPoint renders it, labelled A, B,
C in an order that changes from one look to the next. It is never told where a
version came from, and is told that having a picture or a chart is not in
itself a merit. Each version is scored on seven things; a wrong number, a
distorted chart or a wrong structural relation is a veto, not a low score.

Choosing is conservative: the version already in place stays unless another
beats it clearly and consistently. A small difference in taste is not a reason
to change a page.
"""

from __future__ import annotations

import base64
import io
import json
import random
from dataclasses import dataclass, field
from typing import Any

CRITERIA = {
    "hierarchy": "信息层级与视觉焦点：第一眼落在哪里是否明确，主次是否分明；同一个数字在标题、大字和说明里重复出现，"
                 "没有带来新信息，要扣分",
    "polish": "版式高级感：是否像专业编辑设计，而不是普通报告排版",
    "fit": "内容与视觉表达的匹配度：图、表、图解画的是不是这一页的论点",
    "legibility": "投影可读性：最小的字在会议室投影上能不能看清，有没有折断、截断、挤压",
    "composition": "留白、比例与构图：有没有大块空洞、拥挤的角落、失衡的两栏",
    "consistency": "整套设计语言一致性：与参考页相比，颜色、字体、线条和图形风格是否属于同一套",
    "accuracy": "数据及结构关系是否准确：页面上的数字、图表和关系与给出的事实是否一致",
}
MARGIN = 0.4        # how far ahead, on a five-point mean, a challenger must be to replace the page in place
LOOKS = 5           # looks per page, each with the versions in another order; fewer, and one look decides
VETO_LOOKS = 2      # looks that must name the same fault before it vetoes a version

REVIEW_SYSTEM = (
    "你是独立的演示文稿视觉评审。你会看到同一页幻灯片的几个版本（标为 A、B、C……），以及同一套演示文稿里的几张参考页。"
    "你不知道这些版本是怎么来的，也不需要猜；请只根据画面本身评分。\n"
    "注意：页面上有没有图片或图表，本身既不加分也不减分。有图但图与论点无关、看不清或风格割裂，应当低于一张排得好的纯文字页；"
    "图直接支撑论点并且清楚，才是优点。\n"
    "对每个版本按下面七项各打 1 到 5 分（5 最好），分数要拉开差距，不要都给 4：\n"
    + "\n".join(f"- {key}：{text}" for key, text in CRITERIA.items()) + "\n"
    "否决项（veto）：只有三种情况——页面上的数字与给出的事实相矛盾（同一个指标写了另一个数）；"
    "图表里图形的长短高低明显与它自己标出的数值不成比例；结构关系与给出的事实相矛盾（层级、归属、先后画反了；"
    "或者用箭头把两个不是同一指标前后取值的数连起来，例如把增量指向总量）。"
    "把它写进这个版本的 veto 列表并写清是哪里。事实清单没有列出的内容不是错误：source_figures 里列出的资料原图是原样照排的，"
    "图里有清单之外的数据是正常的，不要因此否决；读图估出来的数值与清单有细微出入也不算矛盾。没有就留空。\n"
    "issues 写这个版本最需要修的一两处具体问题（哪里、怎么不好），没有就留空。\n"
    '只输出 JSON：{"versions":[{"label":"A","scores":{"hierarchy":0,"polish":0,"fit":0,"legibility":0,"composition":0,'
    '"consistency":0,"accuracy":0},"veto":[],"issues":[]}],"best":"A","why":"不超过40个字"}'
)


@dataclass
class Version:
    name: str                      # where it came from; never shown to the reviewer
    png: bytes
    vetoes: list[str] = field(default_factory=list)   # faults found by rules or by a person, before any review


def _thumb(png: bytes, size: int = 48) -> list[int]:
    from PIL import Image

    return list(Image.open(io.BytesIO(png)).convert("L").resize((size, size)).getdata())


def distinct(versions: list[Version], tolerance: float = 1.5) -> list[list[Version]]:
    """Versions grouped by what they look like: the same page under two names is judged once."""

    groups: list[tuple[list[int], list[Version]]] = []
    for version in versions:
        look = _thumb(version.png)
        for known, members in groups:
            if sum(abs(a - b) for a, b in zip(known, look)) / len(look) <= tolerance:
                members.append(version)
                break
        else:
            groups.append((look, [version]))
    return [members for _, members in groups]


async def review_page(client: Any, *, facts: dict[str, Any], versions: list[Version], reference_png: bytes | None,
                      seed: int = 0, looks: int = LOOKS) -> dict[str, Any]:
    """Scores, vetoes and issues per version name, over several blind looks."""

    groups = distinct(versions)
    rng = random.Random(seed)
    labels = [chr(ord("A") + index) for index in range(len(groups))]
    records, orders = [], []
    for look in range(looks):
        order = list(range(len(groups)))
        rng.shuffle(order)
        if look and order == orders[-1]:
            order = order[1:] + order[:1]
        orders.append(order)
        images = ([("image/png", base64.b64encode(reference_png).decode("ascii"))] if reference_png else []) + [
            ("image/png", base64.b64encode(groups[index][0].png).decode("ascii")) for index in order]
        user = (("第 1 张图是同一套演示文稿的参考页（只用来判断设计语言是否一致，不评分）。" if reference_png else "")
                + f"其后 {len(order)} 张依次是版本 " + "、".join(labels[:len(order)]) + "。\n"
                + "这一页应当表达的事实（已核对）：" + json.dumps(facts, ensure_ascii=False))
        try:
            reply = await client.complete_json(task="visual_review", system=REVIEW_SYSTEM, user=user, images=images,
                                               max_output_tokens=1500, context={"look": look})
        except Exception as error:  # noqa: BLE001
            records.append({"error": str(error)[:120]})
            continue
        seen = {str(entry.get("label")): entry for entry in (reply.get("versions") or []) if isinstance(entry, dict)} \
            if isinstance(reply, dict) else {}
        record = {"order": [[member.name for member in groups[index]] for index in order], "versions": {}}
        for label, index in zip(labels, order):
            entry = seen.get(label) or {}
            scores = {key: float(entry.get("scores", {}).get(key)) for key in CRITERIA
                      if isinstance(entry.get("scores", {}).get(key), (int, float))}
            record["versions"][index] = {"scores": scores, "veto": [str(item) for item in entry.get("veto") or []],
                                         "issues": [str(item) for item in entry.get("issues") or []]}
        best = str(reply.get("best") or "") if isinstance(reply, dict) else ""
        record["best"] = order[labels.index(best)] if best in labels[:len(order)] else None
        records.append(record)
    result: dict[str, Any] = {"looks": len(records), "versions": {}}
    for index, members in enumerate(groups):
        looks_at = [record["versions"][index] for record in records if "versions" in record and index in record["versions"]]
        scored = [look for look in looks_at if len(look["scores"]) == len(CRITERIA)]
        means = {key: round(sum(look["scores"][key] for look in scored) / len(scored), 2) for key in CRITERIA} if scored else {}
        flagged = [fault for look in looks_at for fault in look["veto"]]
        vetoes = list(dict.fromkeys(fault for member in members for fault in member.vetoes))
        if sum(bool(look["veto"]) for look in looks_at) >= min(VETO_LOOKS, max(1, len(looks_at))):
            vetoes += ["评审指出：" + fault for fault in dict.fromkeys(flagged)]
        entry = {"same_picture_as": [member.name for member in members], "scores": means,
                 "overall": round(sum(means.values()) / len(means), 2) if means else None,
                 "wins": sum(record.get("best") == index for record in records),
                 "vetoes": vetoes, "flagged_once": [] if vetoes else list(dict.fromkeys(flagged)),
                 "issues": list(dict.fromkeys(issue for look in looks_at for issue in look["issues"]))[:4]}
        for member in members:
            result["versions"][member.name] = entry
    return result


def choose(result: dict[str, Any], incumbent: str, margin: float = MARGIN) -> dict[str, Any]:
    """Which version to keep. The one in place stays unless another is clearly and consistently better."""

    versions = result["versions"]
    standing = {name: entry for name, entry in versions.items() if not entry["vetoes"] and entry["overall"] is not None}
    if not standing:
        return {"keep": None, "why": "没有一个版本通过：全部被否决或没有评出分数", "changed": False}
    best = max(standing, key=lambda name: (standing[name]["overall"], standing[name]["wins"], name == incumbent))
    if incumbent not in standing:
        return {"keep": best, "changed": True,
                "why": f"原版本被否决（{'；'.join(versions.get(incumbent, {}).get('vetoes') or ['未参评'])[:60]}），改用得分最高的合格版本"}
    here, there = standing[incumbent], standing[best]
    if best == incumbent or incumbent in there["same_picture_as"]:
        return {"keep": incumbent, "changed": False, "why": "原版本就是得分最高的合格版本"}
    lead = round(there["overall"] - here["overall"], 2)
    # Two challengers that look alike split the looks between them. What counts is that the page in
    # place was never the one preferred, or that the challenger was preferred most of the time.
    majority = there["wins"] * 2 > result["looks"] or here["wins"] == 0
    if lead >= margin and majority:
        return {"keep": best, "changed": True, "lead": lead,
                "why": f"高出原版本 {lead} 分；{result['looks']} 次评审中它被选为最好 {there['wins']} 次，原版本 {here['wins']} 次"}
    return {"keep": incumbent, "changed": False, "lead": lead,
            "why": f"最高分版本只领先 {lead} 分" + ("" if majority else f"，且原版本仍有 {here['wins']} 次被选为最好") + "；差距不足以换掉原版本"}
