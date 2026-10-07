#!/usr/bin/env python3
"""Audit source-page delivery from saved typeset checkpoints; no model calls.

This measures whether a gold source page reached a content-generation request.
It does NOT prove the relevant passage was present in the selected page fragment,
that the model read it, or that the fact appeared correctly in the final slide.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

DEFAULT_SOURCE = "cnnic_genai_2025.pdf"
SEGMENTS = ("前", "中", "后")


def read_facts(path: Path) -> list[dict]:
    facts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if (len(cells) < 4 or not re.fullmatch(r"[FMB]\d{2}", cells[0])
                or cells[1] not in SEGMENTS):
            continue
        # Parentheses contain printed page numbers, which must not enter the gold set.
        physical = re.sub(r"[（(][^）)]*[）)]", "", cells[2])
        pages = set()
        for match in re.finditer(r"(\d+)(?:\s*[–—-]\s*(\d+))?", physical):
            start = int(match[1])
            end = int(match[2] or start)
            if end < start:
                raise ValueError(f"Reversed PDF page range in {cells[0]}")
            pages.update(range(start, end + 1))
        if not pages:
            raise ValueError(f"No PDF pages for {cells[0]}")
        facts.append({"id": cells[0], "segment": cells[1], "pdf_pages": sorted(pages),
                      "page_relation": "alternatives" if "另见" in physical else
                      ("range" if len(pages) > 1 else "single"), "fact": cells[3]})
    if not facts:
        raise ValueError(f"No fact rows in {path}")
    return facts


def source_matches(name: str, source: str) -> bool:
    # Do not confuse a web URL containing a similarly named PDF with the attachment.
    return "://" not in name and Path(name).name == Path(source).name


def audit_run(run: Path, facts: list[dict], source: str = DEFAULT_SOURCE) -> dict:
    paths = sorted((run / "checkpoints/typeset").glob("content_*.json"))
    delivered = defaultdict(set)
    strategies = Counter()
    selected_strategies = Counter()
    metadata_count = 0
    target_present = False
    checkpoints = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        record = data.get("record") or {}
        evidence = record.get("evidence")
        number = int(record.get("page_number", re.search(r"content_(\d+)", path.name)[1]))
        if not isinstance(evidence, dict) or not isinstance(evidence.get("allowed_pages"), dict):
            continue
        metadata_count += 1
        strategy = evidence.get("strategy") or "unknown"
        strategies[strategy] += 1
        allowed = evidence["allowed_pages"]
        names = set(allowed) | set(evidence.get("page_counts") or {})
        target_present |= any(source_matches(name, source) for name in names)
        selected = sorted({int(page) for name, pages in allowed.items()
                           if source_matches(name, source) for page in pages})
        if selected:
            selected_strategies[strategy] += 1
        for page in selected:
            delivered[page].add(number)
        checkpoints.append({"slide": number, "checkpoint": str(path),
                            "strategy": strategy, "source_pdf_pages": selected})
    status = ("no_checkpoints" if not paths else "evidence_metadata_missing" if not metadata_count
              else "source_not_present" if not target_present else
              "partial_evidence_metadata" if metadata_count != len(paths) else "available")
    result = {"run": str(run), "source": source, "status": status,
              "content_checkpoints": len(paths), "checkpoints_with_metadata": metadata_count,
              "strategy_counts": dict(strategies), "source_strategy_counts": dict(selected_strategies),
              "source_pdf_pages_delivered": sorted(delivered), "checkpoints": checkpoints,
              "facts": [], "segments": None}
    if not target_present:
        return result
    segments = {segment: {"total": 0, "any_page_hits": 0, "all_mentioned_pages_hits": 0}
                for segment in SEGMENTS}
    for fact in facts:
        hit = [page for page in fact["pdf_pages"] if page in delivered]
        item = {**fact, "hit_pdf_pages": hit, "any_page_hit": bool(hit),
                "all_mentioned_pages_hit": len(hit) == len(fact["pdf_pages"]),
                "delivered_to_slides": {str(page): sorted(delivered[page]) for page in hit}}
        result["facts"].append(item)
        segment = segments[fact["segment"]]
        segment["total"] += 1
        segment["any_page_hits"] += int(item["any_page_hit"])
        segment["all_mentioned_pages_hits"] += int(item["all_mentioned_pages_hit"])
    result["segments"] = segments
    return result


def markdown(results: list[dict]) -> str:
    lines = ["# T3 检索资料页召回审计", "",
             "口径：事实所标物理 PDF 页是否进入任一内容页的 `allowed_pages`。只匹配 CNNIC 附件文件名，网页和其他报告同页码不计。",
             "**这不是语义召回率**：页码进入资料不保证相关句子进入截取块，也不保证成品采用事实。",
             "`任一页` 为较宽口径；`全部标注页` 为较严口径。F01 的 9/15 页为另见关系，全页口径只是补充，不代表两页都必需。",
             "历史目录包含同一次运行的快照，不能当作独立重复实验；缺元数据或并非 T3 输入的运行记为不可评分。", "",
             "| 运行 | 状态 | 前 任一/全部 | 中 任一/全部 | 后 任一/全部 | 合计 任一/全部 |", "|---|---|---|---|---|---|"]
    for result in results:
        stats = result["segments"]
        if stats is None:
            lines.append(f"| {result['run']} | {result['status']} | — | — | — | — |")
            continue
        cells = [f"{stats[s]['any_page_hits']}/{stats[s]['total']} · {stats[s]['all_mentioned_pages_hits']}/{stats[s]['total']}" for s in SEGMENTS]
        any_hits = sum(v["any_page_hits"] for v in stats.values())
        all_hits = sum(v["all_mentioned_pages_hits"] for v in stats.values())
        total = sum(v["total"] for v in stats.values())
        lines.append(f"| {result['run']} | {result['status']} | {' | '.join(cells)} | {any_hits}/{total} · {all_hits}/{total} |")
    for result in results:
        if not result["facts"]:
            continue
        lines += ["", f"## {result['run']}", "", f"策略统计：`{json.dumps(result['strategy_counts'], ensure_ascii=False)}`。", "",
                  "| 事实 | PDF 页 | 任一页 | 全部标注页 | 命中的 PDF 页 → PPT 页 |", "|---|---|---|---|---|"]
        for fact in result["facts"]:
            locations = "; ".join(f"{page} → {','.join(map(str, slides))}" for page, slides in fact["delivered_to_slides"].items()) or "—"
            lines.append(f"| {fact['id']} | {','.join(map(str, fact['pdf_pages']))} | {'是' if fact['any_page_hit'] else '否'} | {'是' if fact['all_mentioned_pages_hit'] else '否'} | {locations} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="*", type=Path, help="Run roots containing checkpoints/typeset")
    parser.add_argument("--facts", type=Path, default=Path("eval/facts/T3_facts.md"))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--discover", type=Path, help="Discover all saved typeset runs recursively")
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/generalization/recall"))
    args = parser.parse_args()
    runs = set(args.runs)
    if args.discover:
        runs.update(path.parent.parent.parent for path in args.discover.glob("**/checkpoints/typeset/content_*.json"))
    if not runs:
        parser.error("Supply run directories or --discover")
    facts = read_facts(args.facts)
    results = [audit_run(run, facts, args.source) for run in sorted(runs)]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "recall.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = markdown(results)
    (args.output / "recall.md").write_text(report, encoding="utf-8")
    print(report.split("\n## ")[0])
    print(f"Saved full fact/checkpoint traces to {args.output}")


if __name__ == "__main__":
    main()
