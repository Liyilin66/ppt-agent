"""How many pages the source can carry, decided before any page is written.

The outline divides the requested page count among sections by weight, not by what the source
holds. A section given four pages and four facts makes four pages that retell each other (P3,
CNNIC: users, penetration, users again, penetration again). Here pages that draw on the same
evidence are grouped; where a group has fewer independent facts than pages, each of its pages is
kept, moved to source material no page uses yet (a new evidence packet, which must hold facts of
its own), or dropped. The deck then has the pages the source supports, and says how many that is.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ppt_agent.v2.content_plan import facts, figures

OVERLAP = 0.6          # share of evidence chunks two pages have in common to count as one source
FACTS_PER_PAGE = 2     # independent measured facts a page needs to say something its group does not
SENTENCES_PER_PAGE = 3  # for sources without figures (policy text): statements a page needs
UNCOVERED_LIMIT = 24

BUDGET_SYSTEM = (
    "你是这套演示文稿的内容编辑。下面几组页面各自共用一份资料，但资料里独立的事实不够每页讲一件新事。"
    "请逐页决定：keep（保留，讲这组资料里的一部分）、retarget（改讲资料里还没有任何一页用到的内容：从 uncovered 里选 1 到 3 段，"
    "给出新的页标题和 2 到 4 个要点，标题和要点只能来自所选段落）、drop（删掉这一页，它的内容由同组别的页讲）。\n"
    "要求：每组至少保留一页；同组保留的页数不超过资料里独立事实能支撑的数量；只有当 uncovered 里确实有和本套主题相关、"
    "足够讲一页的新内容时才 retarget，不要为了凑页而改讲无关内容；一段 uncovered 只能给一页。\n"
    '只输出 JSON：{"pages":[{"page":页码,"decision":"keep|retarget|drop","chunks":["chunk_id"],"title":"","points":[""],'
    '"why":"不超过30字"}]}'
)


def _sentences(text: str) -> list[str]:
    import re

    joined = re.sub(r"\s*\n\s*", "", text or "")
    return [s for s in re.split(r"(?<=[。；！？])", joined) if len(re.findall(r"[一-鿿]", s)) >= 12]


def units(text: str) -> tuple[int, str]:
    """Independent facts in a text: distinct measured figures, or, for a text without figures,
    statements a page could carry."""

    measured = set().union(*(figures(sentence) for sentence in facts(text, limit=64))) if facts(text, limit=64) else set()
    if measured:
        return len(measured), "figures"
    return len(_sentences(text)) // SENTENCES_PER_PAGE * FACTS_PER_PAGE, "statements"


def groups(numbers: list[int], packets: dict[int, Any]) -> list[list[int]]:
    """Pages joined when their evidence overlaps (transitively)."""

    parent = {number: number for number in numbers}

    def root(number: int) -> int:
        while parent[number] != number:
            number = parent[number]
        return number

    for index, first in enumerate(numbers):
        for second in numbers[index + 1:]:
            a = set(getattr(packets.get(first), "chunk_ids", []) or [])
            b = set(getattr(packets.get(second), "chunk_ids", []) or [])
            if a and b and len(a & b) / min(len(a), len(b)) >= OVERLAP:
                parent[root(second)] = root(first)
    found: dict[int, list[int]] = {}
    for number in numbers:
        found.setdefault(root(number), []).append(number)
    return sorted(found.values())


def over_budget(group: list[int], packets: dict[int, Any]) -> dict[str, Any] | None:
    text = "\n".join(dict.fromkeys(getattr(packets.get(number), "text", "") or "" for number in group))
    count, kind = units(text)
    if len(group) < 2 or count >= FACTS_PER_PAGE * len(group):
        return None
    return {"pages": group, "facts": count, "kind": kind, "supports": max(1, count // FACTS_PER_PAGE)}


def uncovered(store: Any, packets: dict[int, Any]) -> list[dict[str, Any]]:
    """Chunks of the source no page's packet holds, with the facts in them."""

    used = {chunk for packet in packets.values() if packet is not None for chunk in packet.chunk_ids}
    found = []
    for chunk in store.chunks:
        if chunk["chunk_id"] in used or store._docs[chunk["doc_id"]]["page_kind"] == "web":  # noqa: SLF001
            continue
        count, _ = units(chunk["text"])
        if count >= FACTS_PER_PAGE:
            found.append({"chunk_id": chunk["chunk_id"], "pages": chunk["pages"],
                          "facts": (facts(chunk["text"], limit=5) or _sentences(chunk["text"])[:3])})
    return found[:UNCOVERED_LIMIT]


async def decide(client: Any, checkpoints: Any, slots: list[Any], packets: dict[int, Any],
                 store: Any) -> dict[str, Any]:
    """Decisions for pages in over-budget groups; cached at ``typeset/page_budget.json``."""

    saved = checkpoints.load("typeset/page_budget.json")
    if saved is not None:
        return saved
    numbers = sorted(slot.page_number for slot in slots)
    over = [found for group in groups(numbers, packets) if (found := over_budget(group, packets))]
    result: dict[str, Any] = {"requested_content_pages": len(numbers), "over_budget": over, "decisions": {}}
    if over and store is not None:
        spare = uncovered(store, packets)
        by_number = {slot.page_number: slot for slot in slots}
        asked = {"groups": [{"pages": [{"page": number, "title": by_number[number].brief.title if by_number[number].brief else "",
                                       "facts": facts(packets[number].text if packets.get(number) else "", limit=10)}
                                      for number in found["pages"]],
                             "independent_facts": found["facts"]} for found in over],
                 "uncovered": spare}
        try:
            reply = await client.complete_json(task="page_budget", system=BUDGET_SYSTEM,
                                               user=json.dumps(asked, ensure_ascii=False),
                                               max_output_tokens=3000, context={"pages": numbers})
        except Exception as error:  # noqa: BLE001 - the outline stands as planned
            reply = {"error": str(error)[:120]}
        result["decisions"] = validate(reply, over, spare)
    supported = len(numbers) - sum(entry["decision"] == "drop" for entry in result["decisions"].values())
    result["supported_content_pages"] = supported
    checkpoints.save("typeset/page_budget.json", result)
    return result


def validate(reply: Any, over: list[dict[str, Any]], spare: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep only decisions the rules allow: each group keeps a page, a chunk goes to one page, a
    retargeted page names uncovered chunks and has a title."""

    entries = {entry.get("page"): entry for entry in (reply.get("pages") if isinstance(reply, dict) else None) or []
               if isinstance(entry, dict)}
    free = {chunk["chunk_id"] for chunk in spare}
    taken: set[str] = set()
    decisions: dict[str, dict[str, Any]] = {}
    for found in over:
        group = found["pages"]
        for number in group:
            entry = entries.get(number) or {}
            decision = entry.get("decision")
            chunks = [chunk for chunk in entry.get("chunks") or [] if chunk in free and chunk not in taken]
            if decision == "retarget" and chunks and str(entry.get("title") or "").strip():
                taken.update(chunks)
                decisions[str(number)] = {"decision": "retarget", "chunks": chunks[:3],
                                          "title": str(entry["title"])[:40],
                                          "points": [str(point)[:60] for point in entry.get("points") or []][:4],
                                          "why": str(entry.get("why") or "")[:60]}
            elif decision == "drop":
                decisions[str(number)] = {"decision": "drop", "why": str(entry.get("why") or "")[:60]}
        kept = [number for number in group if decisions.get(str(number), {}).get("decision") != "drop"]
        if not kept:
            decisions.pop(str(group[0]), None)  # a group always keeps its first page
    return decisions


def apply(skeleton: Any, packets: dict[int, Any], budget: dict[str, Any], store: Any) -> tuple[Any, dict[int, Any]]:
    """The skeleton and packets after the decisions: retargeted pages get new evidence and brief,
    dropped pages go, pages are numbered again, a section left without pages loses its divider."""

    decisions = budget.get("decisions") or {}
    if not decisions:
        return skeleton, packets
    by_id = {chunk["chunk_id"]: chunk for chunk in store.chunks} if store is not None else {}
    slots, new_packets = [], {}
    dropped = {int(number) for number, entry in decisions.items() if entry["decision"] == "drop"}
    content_sections = Counter(slot.section_index for slot in skeleton.slots
                               if slot.kind == "content" and slot.page_number not in dropped)
    for slot in skeleton.slots:
        if slot.page_number in dropped:
            continue
        if slot.kind == "section_divider" and not content_sections.get(slot.section_index):
            continue
        entry = decisions.get(str(slot.page_number))
        packet = packets.get(slot.page_number)
        if slot.kind == "content" and entry and entry["decision"] == "retarget":
            packet = store.packet_of([by_id[chunk] for chunk in entry["chunks"] if chunk in by_id])
            brief = slot.brief.model_copy(update={"title": entry["title"], "points": entry["points"] or slot.brief.points}) \
                if slot.brief is not None else slot.brief
            slot = slot.model_copy(update={"brief": brief})
        number = len(slots) + 1
        if slot.kind == "content":
            new_packets[number] = packet
        slots.append(slot.model_copy(update={"page_number": number}))
    return skeleton.model_copy(update={"slots": slots, "total_pages": len(slots)}), new_packets
