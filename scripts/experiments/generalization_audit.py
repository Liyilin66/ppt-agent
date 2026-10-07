#!/usr/bin/env python3
"""Offline generalization inspection with reproducible, unfiltered number sampling.

Value occurrence is only a candidate match. Human inspection must confirm metric,
unit, date, cited physical page, and final rendered slide before accepting it.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import random
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ppt_agent.v2.evidence import EvidencePacket
from ppt_agent.v2.typeset_pipeline import cited_evidence, source_is_valid

NUMBER = re.compile(r"(?<![A-Za-z0-9.])\d+(?:,\d{3})*(?:\.\d+)?(?:[%％]|万亿|亿|万|倍|年|家|项|台|个|次)?")
HEADER = re.compile(r"^\[(.+?) \| (PDF|logical|web) p(\d+)\]\n", re.M)
SKIP = {"source", "speaker_notes", "ref", "archetype", "unit_format"}


def visible_numbers(value, path="", parent=None):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in SKIP:
                yield from visible_numbers(item, f"{path}.{key}".strip("."), value)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from visible_numbers(item, f"{path}.{index}", parent)
    elif isinstance(value, (str, float, int)) and not isinstance(value, bool):
        text = str(value)
        for match in NUMBER.finditer(text):
            yield {"field": path, "number": match.group(), "visible_text": text,
                   "context": parent if isinstance(parent, dict) else None}


def split_blocks(text):
    matches = list(HEADER.finditer(text))
    return [{"name": match[1], "kind": match[2], "page": int(match[3]),
             "text": text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]}
            for i, match in enumerate(matches)]


def numeric_core(value):
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", value)
    return Decimal(match.group().replace(",", "")) if match else None


def matches_in_pages(number, pages):
    wanted = numeric_core(number)
    matches = []
    for page, original in pages:
        compact = re.sub(r"\s+", "", original)
        for match in re.finditer(r"(?<![\d.])\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])", compact):
            try:
                equal = Decimal(match.group().replace(",", "")) == wanted
            except InvalidOperation:
                equal = False
            if equal:
                matches.append({"pdf_page": page, "matched_value": match.group(),
                                "excerpt": compact[max(0, match.start()-75):match.end()+75]})
                if sum(item["pdf_page"] == page for item in matches) >= 3:
                    break
    return matches


def coverage(pages, ranges):
    return {label: {"pages": sorted(page for page in pages if low <= page <= high),
                    "selected_count": sum(low <= page <= high for page in pages),
                    "available_count": high-low+1}
            for label, low, high in ranges}


def inspect(run, source_pages, source, seed=20261007, sample_size=10):
    originals = {int(page["page"]): page["text"] for page in source_pages}
    paths = sorted((run / "checkpoints/typeset").glob("content_*.json"))
    if not paths:
        raise ValueError("No saved content checkpoints; run is not ready for inspection")
    selected = set()
    strategies = Counter()
    archetypes = Counter()
    per_slide, file_pool, web_only, uncited = [], [], [], []
    for path in paths:
        data = json.loads(path.read_text())
        content, record = data["content"], data["record"]
        evidence = record.get("evidence")
        packet = EvidencePacket(**evidence) if evidence else EvidencePacket()
        pages = sorted({int(page) for name, values in packet.allowed_pages.items()
                        if "://" not in name and Path(name).name == source for page in values})
        selected.update(pages)
        strategies[packet.strategy if evidence else "metadata_missing"] += 1
        archetypes[content.get("archetype", "unknown")] += 1
        citation = content.get("source") or ""
        valid = source_is_valid(citation, packet.references, packet.page_counts, packet.allowed_pages)
        blocks = split_blocks(cited_evidence(citation, packet).text)
        cited_pdf = sorted({block["page"] for block in blocks if block["kind"] == "PDF"
                            and Path(block["name"]).name == source})
        per_slide.append({"slide": record["page_number"], "title": content.get("title"),
                          "strategy": packet.strategy if evidence else None,
                          "allowed_pdf_pages": pages, "cited_pdf_pages": cited_pdf,
                          "source": citation, "source_valid": valid,
                          "fallback": record.get("fallback", False),
                          "validation_errors": record.get("validation_errors", [])})
        for candidate in visible_numbers(content):
            candidate.update(slide=record["page_number"], title=content.get("title"),
                             source=citation, source_valid=valid, checkpoint=str(path))
            if content.get("archetype") == "chart" and candidate["field"].startswith("values."):
                index = int(candidate["field"].split(".")[-1])
                candidate["chart_unit_format"] = content.get("unit_format")
                candidate["chart_category"] = content.get("categories", [])[index]
            # Selection is based on citation class only, never whether the number matches.
            if source in citation and "://" not in citation.replace(source, ""):
                candidate["cited_pdf_pages"] = cited_pdf
                file_pool.append(candidate)
            elif source in citation:  # Mixed web/file citations still permit PDF inspection.
                candidate["cited_pdf_pages"] = cited_pdf
                candidate["mixed_web_and_file_source"] = True
                file_pool.append(candidate)
            elif "http://" in citation or "https://" in citation:
                web_only.append(candidate)
            else:
                uncited.append(candidate)
    file_pool.sort(key=lambda item: (item["slide"], item["field"], item["number"], item["visible_text"]))
    samples = random.Random(seed).sample(file_pool, min(sample_size, len(file_pool)))
    for candidate in samples:
        cited_pages = candidate["cited_pdf_pages"]
        candidate["matches_on_cited_pdf_pages"] = matches_in_pages(candidate["number"],
            [(page, originals[page]) for page in cited_pages if page in originals])
        candidate["matches_anywhere_in_pdf"] = matches_in_pages(candidate["number"], sorted(originals.items()))
        candidate["verification"] = "manual_required"
        candidate["automatic_value_occurs_on_cited_page"] = bool(candidate["matches_on_cited_pdf_pages"])
    report_paths = sorted(run.glob("*run_report.json"))
    report = json.loads(report_paths[0].read_text()) if report_paths else {}
    total = max(originals)
    return {"run": str(run), "source": source, "physical_pdf_pages": total,
            "content_checkpoints": len(paths), "strategy_counts": dict(strategies),
            "archetype_counts": dict(archetypes), "union_allowed_pdf_pages": sorted(selected),
            "physical_thirds": coverage(selected, [("前", 1, 21), ("中", 22, 42), ("后", 43, total)]),
            "body_thirds_starting_7": coverage(selected, [("前", 7, 25), ("中", 26, 44), ("后", 45, total)]),
            "per_slide": per_slide, "fallback_pages_from_checkpoints": sum(bool(p["fallback"]) for p in per_slide),
            "source_empty_pages_from_checkpoints": sum(not p["source"] for p in per_slide),
            "source_invalid_pages_from_checkpoints": sum(not p["source_valid"] for p in per_slide),
            "content_statistics": report.get("content_statistics"),
            "numeric_check_statistics": report.get("numeric_check_statistics"),
            "quality_gate": report.get("quality_gate"),
            "sample_seed": seed, "sample_unit": "visible numeric occurrence (including dates/years); without deduplication",
            "file_cited_numeric_candidate_count": len(file_pool),
            "web_only_numeric_candidates_excluded_from_pdf_sample": web_only,
            "uncited_numeric_candidates_excluded_from_file_sample": uncited,
            "sample": samples,
            "limitations": ["Page availability is not semantic coverage or factual correctness.",
                            "Number occurrence does not establish matching metric, unit or date.",
                            "This reads content JSON, not final rendered PPT; PowerPoint review is separate.",
                            "No matches are filtered out before sampling; all sampled items require manual verification."]}


def render_markdown(result):
    lines = ["# 新报告离线检查", "", f"输入：{result['source']}，{result['physical_pdf_pages']} 个物理 PDF 页。",
             f"内容页策略：`{json.dumps(result['strategy_counts'], ensure_ascii=False)}`。",
             f"原型：`{json.dumps(result['archetype_counts'], ensure_ascii=False)}`。", "",
             "| 物理 PDF 分段 | 送达页数 / 分段总页数 | 页码 |", "|---|---|---|"]
    for label, item in result["physical_thirds"].items():
        lines.append(f"| {label} | {item['selected_count']}/{item['available_count']} | {item['pages']} |")
    lines += ["", f"退化内容页：{result['fallback_pages_from_checkpoints']}；空出处：{result['source_empty_pages_from_checkpoints']}；规则判定出处不合格：{result['source_invalid_pages_from_checkpoints']}。",
              f"PDF 引用数字候选 {result['file_cited_numeric_candidate_count']} 个；网页引用数字排除 {len(result['web_only_numeric_candidates_excluded_from_pdf_sample'])} 个；未引用数字排除 {len(result['uncited_numeric_candidates_excluded_from_file_sample'])} 个。",
              f"固定 seed={result['sample_seed']}，按数字出现位置抽样（含年份、允许同值重复），先抽样后匹配，未筛除失败项。", "",
              "| 抽样 | PPT 页 / 字段 | 数字 | 引用 PDF 页 | 同值出现的引用页 | 待核对原句（摘录） |", "|---|---|---|---|---|---|"]
    for i, candidate in enumerate(result["sample"], 1):
        matches = candidate["matches_on_cited_pdf_pages"]
        excerpt = matches[0]["excerpt"] if matches else "引用页未找到同值；必须人工核对"
        excerpt = excerpt.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {candidate['slide']} / {candidate['field']} | {candidate['number']} | {candidate['cited_pdf_pages']} | {sorted({m['pdf_page'] for m in matches})} | {excerpt} |")
    lines += ["", "以上只定位数字同值出现，指标、单位、年份与最终渲染必须人工确认，全部状态仍为 manual_required。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--source-pages", type=Path, required=True)
    parser.add_argument("--source", default="caict_ai_2024.pdf")
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect(args.run, json.loads(args.source_pages.read_text()), args.source, args.seed, args.sample_size)
    output = args.output or args.run.parent / "audit"
    output.mkdir(parents=True, exist_ok=True)
    (output / "inspection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (output / "inspection.md").write_text(render_markdown(result))
    print(json.dumps({key: result[key] for key in ("content_checkpoints", "strategy_counts", "archetype_counts", "file_cited_numeric_candidate_count")}, ensure_ascii=False))
    print(f"Saved {output}")


if __name__ == "__main__":
    main()
