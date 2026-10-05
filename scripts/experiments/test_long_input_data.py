"""Source preservation and retrieval contracts for the long-input experiment."""

import hashlib
import math
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from long_input_data import (
    BM25Index,
    chunks,
    cnnic_chapters,
    full_text,
    read_documents,
    select_chapter,
    tokenize,
)


def make_doc(*texts: str, doc_id: str = "doc1") -> dict:
    return {
        "doc_id": doc_id,
        "path": f"/{doc_id}.pdf",
        "sha256": "fixture",
        "pages": [
            {"pdf_page": number, "text": text}
            for number, text in enumerate(texts, start=1)
        ],
        "chars": sum(len(text) for text in texts),
    }


def write_pdf(path: Path) -> None:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for text in ["First page", None, "Final evidence"]:
        page = writer.add_blank_page(width=200, height=200)
        if text is not None:
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({
                    NameObject("/F1"): writer._add_object(font),
                }),
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)


def test_read_preserves_blank_page_positions_and_hashes_actual_file(tmp_path):
    path = tmp_path / "report.pdf"
    write_pdf(path)
    documents = read_documents([path, path])

    assert [doc["doc_id"] for doc in documents] == ["doc1", "doc2"]
    doc = documents[0]
    assert doc["path"] == str(path.resolve())
    assert doc["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert doc["pages"] == [
        {"pdf_page": 1, "text": "First page"},
        {"pdf_page": 2, "text": ""},
        {"pdf_page": 3, "text": "Final evidence"},
    ]
    assert doc["chars"] == 24


def test_full_text_keeps_every_pdf_page_marker_and_document_boundary():
    result = full_text([make_doc("证据一", "", "证据三"), make_doc("另一份", doc_id="doc2")])
    assert result == (
        "[[doc1 PDF p1]]\n证据一\n\n[[doc1 PDF p2]]\n\n\n"
        "[[doc1 PDF p3]]\n证据三\n\n[[doc2 PDF p1]]\n另一份"
    )


def test_chapters_use_pdf_page_ranges_and_preserve_original_page_text():
    doc = make_doc(*(f"第{page}页原文" for page in range(1, 66)))
    chapters = cnnic_chapters(doc)
    assert [chapter["title"] for chapter in chapters] == [
        "主要发展特点", "用户普及", "产业发展", "典型应用", "发展环境", "国际态势",
    ]
    assert [(ch["pdf_page_start"], ch["pdf_page_end"]) for ch in chapters] == [
        (9, 14), (15, 22), (23, 24), (25, 42), (43, 50), (51, 63),
    ]
    assert chapters[0]["text"] == "第9页原文\n第10页原文\n第11页原文\n第12页原文\n第13页原文\n第14页原文"
    assert chapters[2]["pages"] == [
        {"pdf_page": 23, "text": "第23页原文"},
        {"pdf_page": 24, "text": "第24页原文"},
    ]
    assert chapters[5]["text"].endswith("第63页原文")
    assert all("第64页原文" not in ch["text"] for ch in chapters)


def test_incomplete_chapter_input_does_not_silently_label_missing_pages():
    with pytest.raises(ValueError, match="63"):
        cnnic_chapters(make_doc("too short"))


def test_chunk_keeps_a_cross_page_fact_in_one_source_span():
    result = chunks([make_doc("用户规模为", "5.15亿人", "尾部证据")], size=20, overlap=4)
    assert result == [{
        "doc_id": "doc1", "pdf_page_start": 1, "pdf_page_end": 3,
        "text": "用户规模为\n5.15亿人\n尾部证据",
    }]


def test_chunks_keep_last_partial_chunk_and_correct_overlap_page_spans():
    result = chunks([make_doc("abcdefgh", "ijklmnop", "TAIL")], size=10, overlap=2)
    assert result == [
        {"doc_id": "doc1", "pdf_page_start": 1, "pdf_page_end": 2, "text": "abcdefgh\ni"},
        {"doc_id": "doc1", "pdf_page_start": 2, "pdf_page_end": 2, "text": "\nijklmnop\n"},
        {"doc_id": "doc1", "pdf_page_start": 2, "pdf_page_end": 3, "text": "p\nTAIL"},
    ]
    assert "TAIL" in result[-1]["text"]
    assert result[0]["text"] + result[1]["text"][2:] + result[2]["text"][2:] == "abcdefgh\nijklmnop\nTAIL"


def test_chunks_do_not_mix_documents_or_emit_an_overlap_only_tail():
    result = chunks([make_doc("0123456789"), make_doc("XYZ", doc_id="doc2")], size=10, overlap=3)
    assert [(item["doc_id"], item["text"]) for item in result] == [
        ("doc1", "0123456789"), ("doc2", "XYZ"),
    ]
    assert chunks([]) == []
    assert chunks([make_doc("", "")]) == []


def test_filtered_pages_keep_original_pdf_numbers_in_chunks_and_full_text():
    doc = make_doc("range starts here", "range ends here")
    doc["pages"][0]["pdf_page"] = 11
    doc["pages"][1]["pdf_page"] = 14
    result = chunks([doc], size=100, overlap=0)
    assert result[0]["pdf_page_start"] == 11
    assert result[0]["pdf_page_end"] == 14
    assert full_text([doc]) == (
        "[[doc1 PDF p11]]\nrange starts here\n\n"
        "[[doc1 PDF p14]]\nrange ends here"
    )


@pytest.mark.parametrize("size,overlap", [(0, 0), (3, -1), (3, 3), (3, 4)])
def test_invalid_chunk_stride_is_rejected(size, overlap):
    with pytest.raises(ValueError):
        chunks([make_doc("evidence")], size=size, overlap=overlap)


def test_tokenize_retains_chinese_bigrams_and_normalizes_ascii_words():
    assert tokenize("用户普及 AI 2025，用户") == ["用户", "户普", "普及", "ai", "2025", "用户"]
    assert tokenize("！ ") == []


def test_bm25_ranks_topic_relevance_and_matches_the_standard_formula():
    index = BM25Index(["AI AI", "AI health", "finance"])
    matches = index.search("ai", top_k=3)
    assert [position for position, _ in matches] == [0, 1]
    expected_first = math.log(1 + 1.5 / 2.5) * (2 * 2.5) / (2 + 1.5 * (0.25 + 0.75 * 2 / (5 / 3)))
    assert matches[0][1] == pytest.approx(expected_first)
    chinese = BM25Index(["产业发展促进芯片技术", "用户普及规模与用户数量", "国际态势与政策"])
    assert chinese.search("用户规模数量", 1)[0][0] == 1


def test_bm25_empty_and_unmatched_queries_never_recall_arbitrary_documents():
    index = BM25Index(["用户规模", "产业发展", ""])
    assert index.search("", 3) == []
    assert index.search("!!!", 3) == []
    assert index.search("火星考古", 3) == []
    assert index.search("用户", 0) == []
    assert BM25Index([]).search("用户", 3) == []


def test_bm25_equal_scores_keep_input_order():
    assert BM25Index(["共同证据", "共同证据", "共同证据"]).search("共同", 2) == [
        (0, pytest.approx(0.13353139262452257)),
        (1, pytest.approx(0.13353139262452257)),
    ]


def test_chapter_selection_uses_title_and_only_first_500_characters():
    chapters = [
        {"title": "主要发展特点", "text": "通用内容" * 200 + "用户数量"},
        {"title": "用户普及", "text": "用户数量规模统计"},
        {"title": "发展环境", "text": "法律治理政策"},
    ]
    chapter, score = select_chapter("用户数量", chapters)
    assert chapter is chapters[1]
    assert score > 0
    assert select_chapter("火星考古", chapters) == (None, 0.0)
    assert select_chapter("", chapters) == (None, 0.0)
    assert select_chapter("政策", []) == (None, 0.0)
    assert select_chapter("法律治理", chapters)[0] is chapters[2]


def test_chapter_title_fallback_handles_an_infix_that_has_no_bigram_match():
    chapters = [{"title": "用户普及", "text": ""}, {"title": "产业发展", "text": ""}]
    chapter, score = select_chapter("户", chapters)
    assert chapter is chapters[0]
    assert score == 0.0
