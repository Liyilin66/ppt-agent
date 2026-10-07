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


def test_chinese_top_level_headings_match_toc_and_midpage_starts():
    # Excerpts preserve the CAICT report's headings and physical-page positions.
    pages = ['版权声明', '目 录\n一、总体态势........1\n二、技术创新........8\n三、应用赋能........32\n四、安全治理........44\n五、发展展望........54']
    pages += ['人工智能发展报告（2024 年）\n1\n一、总体态势\n人工智能浪潮席卷全球。',
              '人工智能发展报告（2024 年）\n2\n一、总体态势\n续页正文。',
              '人工智能发展报告（2024 年）\n8\n二、技术创新\n基础模型仍在快速演进迭代。',
              '三、应用赋能\n行业应用不断发展。',
              '人工智能发展报告（2024 年）\n44\n' + '上一章节的安全技术分析。' * 30 + '\n四、安全治理\n安全治理体系发展。',
              '人工智能发展报告（2024 年）\n54\n' + '上一章节的治理技术分析。' * 30 + '\n五、发展展望\n未来发展趋势。']
    chapters = store(pages).chapters
    assert [c['title'] for c in chapters] == ['一、总体态势', '二、技术创新', '三、应用赋能', '四、安全治理', '五、发展展望']
    assert [c['pages'][0] for c in chapters] == [3, 5, 6, 7, 8]


def test_arabic_headings_without_toc_require_a_consistent_sequence():
    chapters = store(['1. Industry overview\nMarket analysis.',
                      '1. Industry overview\nContinued analysis.',
                      '2. Technology trends\nTechnology analysis.',
                      '3. Future outlook\nFuture analysis.']).chapters
    assert [c['title'] for c in chapters] == ['1. Industry overview', '2. Technology trends', '3. Future outlook']
    assert [c['pages'] for c in chapters] == [[1, 2], [3], [4]]


def test_inline_numbered_lists_do_not_create_chapters():
    assert store(['正文先介绍行业。\n一、建议加强技术创新，形成新的机制。\n二、建议完善应用。',
                  '本页继续正文分析。\n1. 短期落实政策\n2. 长期持续建设']).chapters == []


def test_fallback_does_not_change_existing_primary_heading_family():
    chapters = store(['第一章 用户规模\n一、总体态势\n用户增长。',
                      '第二章 发展趋势\n二、技术创新\n发展趋势。']).chapters
    assert [c['title'] for c in chapters] == ['第一章 用户规模', '第二章 发展趋势']
