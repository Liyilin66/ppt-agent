from pathlib import Path

from ppt_agent.v2.evidence import EvidenceStore


def store(pages, title='report.pdf'):
    return EvidenceStore([{'doc_id': 'doc1', 'name': title, 'page_kind': 'PDF', 'pages': [{'page': i + 1, 'text': text} for i, text in enumerate(pages)]}])


def test_chunks_cross_pages_and_preserve_last():
    evidence = store(['a' * 1450 + 'cross', 'boundary ' + 'b' * 1500 + 'FINAL_CANARY'])
    assert any(len(c['pages']) > 1 for c in evidence.chunks)
    packet = evidence.select('FINAL_CANARY', mode='retrieval')
    assert 'FINAL_CANARY' in packet.text
    assert 2 in packet.allowed_pages['report.pdf']
    assert len(packet.text) <= 6500


def test_document_map_sees_last_chapter_and_numbers():
    evidence = store(['第一章 用户规模\n2025年用户规模5.15亿人。' + '内容' * 1000,
                      '第二章 海外投资\n2025年韩国投资1.1万亿韩元。' + '材料' * 1000])
    mapping = evidence.document_map(max_chars=500)
    assert '海外投资' in mapping and '1.1万亿韩元' in mapping
    assert 'PDF p2' in mapping and len(mapping) <= 500


def test_chapter_selection_and_low_relevance_fallback():
    evidence = store(['第一章 用户规模\n用户规模增长5亿人。' * 30,
                      '第二章 海外投资\n海外投资韩国资金。' * 30])
    assert evidence.select('用户规模', section_query='用户规模').strategy == 'chapter'
    assert evidence.select('completely unrelated rockets').strategy == 'bm25'


def test_truncated_selection_does_not_claim_unseen_pages():
    evidence = store(['第一章 用户规模\n用户规模' * 400, '用户规模' * 400])
    packet = evidence.select('用户规模', max_chars=300)
    assert len(packet.text) <= 300
    assert packet.allowed_pages == {'report.pdf': [1]}


def test_roundtrip_and_text_logical_pages(tmp_path):
    path = tmp_path / 'notes.md'
    path.write_text('# 第一部分\n用户规模5亿。\n# 最后一部分\n韩国投资1万亿。')
    evidence = EvidenceStore.from_paths([path])
    assert evidence.documents[0]['page_kind'] == 'logical'
    restored = EvidenceStore.from_dict(evidence.to_dict())
    assert restored.document_map() == evidence.document_map()
    assert 'PDF' not in restored.select('韩国投资').text


def test_all_pdf_pages_parsed(monkeypatch, tmp_path):
    path = tmp_path / 'all.pdf'
    path.write_bytes(b'fake')
    class Page:
        def __init__(self, i): self.i = i
        def extract_text(self): return f'page marker {self.i}'
    class Reader:
        def __init__(self, path): self.pages = [Page(i) for i in range(130)]
    monkeypatch.setattr('pypdf.PdfReader', Reader)
    evidence = EvidenceStore.from_paths([path])
    assert len(evidence.documents[0]['pages']) == 130
    assert 'marker 129' in evidence.select('129', mode='retrieval').text


def test_toc_does_not_become_chapter():
    evidence = store(['目录\n第一章 用户规模........1\n第二章 海外投资........2',
                      '第一章 用户规模\n用户规模增长。', '第二章 海外投资\n海外投资增长。'])
    assert len(evidence.chapters) == 2
    assert [c['pages'][0] for c in evidence.chapters] == [2, 3]


def test_multiple_sources_route_to_retrieval():
    evidence = EvidenceStore([
        {'doc_id': 'a', 'name': 'a.pdf', 'page_kind': 'PDF', 'pages': [{'page': 1, 'text': '第一章 用户规模\n用户规模5亿。'}]},
        {'doc_id': 'b', 'name': 'b.pdf', 'page_kind': 'PDF', 'pages': [{'page': 1, 'text': '第一章 海外投资\n海外投资100亿元。'}]},
    ])
    packet = evidence.select('海外投资', section_query='海外投资')
    assert packet.strategy == 'bm25'
    assert packet.allowed_pages == {'b.pdf': [1]}


def test_replaceable_retriever_and_empty_match():
    evidence = store(['第一章 投资\n投资韩国100亿元。'])
    assert evidence.select('zero matching vocabulary').text == ''
    class EmptyRetriever:
        def search(self, query, top_k): return []
    evidence.retriever = EmptyRetriever()
    assert evidence.select('投资韩国', mode='retrieval').text == ''


def test_markdown_retains_all_heading_sections(tmp_path):
    path = tmp_path / 'notes.md'
    path.write_text('# 用户规模\n用户规模5亿。\n# 海外投资\n海外投资100亿元。')
    evidence = EvidenceStore.from_paths([path])
    assert len(evidence.chapters) == 2
    assert '海外投资' in evidence.document_map()
    assert evidence.parsed_files == [str(path.resolve())]


def test_map_keeps_final_page_number_in_long_chapter():
    pages = ['第一章 全球发展\n2025年本国投入100亿元，支持全国创新项目。' * 20]
    pages += ['2025年投资200亿元，支持产业发展的创新项目。' * 20 for _ in range(8)]
    pages += ['2025年末尾国家投入987.6亿元，设立最后一页创新资金。']
    mapping = store(pages).document_map(max_chars=650)
    assert '987.6亿元' in mapping
    assert 'PDF p10' in mapping


def test_chapter_boundary_chunk_retains_new_chapter_start():
    evidence = store(['第一章 用户增长\n' + '用户增长资料。' * 210,
                      '第二章 海外投资\n海外投资 UNIQUE_FIRST_SENTENCE 999亿元。' + '海外投资资料。' * 210])
    packet = evidence.select('UNIQUE_FIRST_SENTENCE', section_query='海外投资')
    assert packet.strategy == 'chapter'
    assert 'UNIQUE_FIRST_SENTENCE' in packet.text
    assert packet.allowed_pages == {'report.pdf': [2]}
