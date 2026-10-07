"""Offline retrieval audits must not conflate document pages or semantic recall."""

import importlib.util
import json
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "eval_recall", Path(__file__).parents[1] / "scripts" / "eval_recall.py"
)
recall = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recall
SPEC.loader.exec_module(recall)


def checkpoint(root, number, evidence):
    path = root / "checkpoints" / "typeset" / f"content_{number:03}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"record": {"page_number": number, "evidence": evidence}}))


def test_gold_pdf_pages_exclude_printed_page_numbers():
    facts = recall.read_facts(Path(__file__).parents[1] / "eval/facts/T3_facts.md")
    assert len(facts) == 15
    assert facts[0]["pdf_pages"] == [9, 15]
    assert facts[0]["page_relation"] == "alternatives"
    assert facts[8]["pdf_pages"] == [36, 37]
    assert facts[12]["pdf_pages"] == [53, 54]


def test_wrong_document_and_page_counts_do_not_count_as_delivery(tmp_path):
    facts = [{"id": "B11", "segment": "后", "pdf_pages": [45], "page_relation": "single"}]
    checkpoint(tmp_path, 3, {"strategy": "bm25", "allowed_pages": {"other.pdf": [45]},
                             "page_counts": {"cnnic_genai_2025.pdf": 63}})
    result = recall.audit_run(tmp_path, facts)
    assert result["status"] == "available"
    assert result["facts"][0]["any_page_hit"] is False
    assert result["segments"]["后"]["any_page_hits"] == 0


def test_cross_page_fact_any_and_all_are_separate(tmp_path):
    facts = [{"id": "B13", "segment": "后", "pdf_pages": [53, 54], "page_relation": "range"}]
    checkpoint(tmp_path, 8, {"strategy": "chapter", "allowed_pages": {"cnnic_genai_2025.pdf": [54]}})
    result = recall.audit_run(tmp_path, facts)
    assert result["facts"][0]["any_page_hit"] is True
    assert result["facts"][0]["all_mentioned_pages_hit"] is False
    assert result["facts"][0]["delivered_to_slides"] == {"54": [8]}


def test_missing_metadata_and_wrong_report_are_unavailable(tmp_path):
    checkpoint(tmp_path, 3, None)
    result = recall.audit_run(tmp_path, [])
    assert result["status"] == "evidence_metadata_missing"
    assert result["segments"] is None
    checkpoint(tmp_path, 3, {"allowed_pages": {"https://example.org": [1]}, "strategy": "bm25"})
    assert recall.audit_run(tmp_path, [])["status"] == "source_not_present"


def test_partial_metadata_is_flagged_and_web_filename_is_not_attachment(tmp_path):
    facts = [{"id": "F01", "segment": "前", "pdf_pages": [9, 15], "page_relation": "alternatives"}]
    checkpoint(tmp_path, 3, {"strategy": "chapter", "allowed_pages": {
        "/uploads/cnnic_genai_2025.pdf": [9],
        "https://example.org/cnnic_genai_2025.pdf": [15]}})
    checkpoint(tmp_path, 4, None)
    result = recall.audit_run(tmp_path, facts)
    assert result["status"] == "partial_evidence_metadata"
    assert result["content_checkpoints"] == 2
    assert result["checkpoints_with_metadata"] == 1
    assert result["facts"][0]["hit_pdf_pages"] == [9]
    assert result["facts"][0]["all_mentioned_pages_hit"] is False


def test_no_content_files_is_not_zero_recall(tmp_path):
    assert recall.audit_run(tmp_path, [])["status"] == "no_checkpoints"
