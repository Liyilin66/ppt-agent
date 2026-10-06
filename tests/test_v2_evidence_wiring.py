import json
from pathlib import Path

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest,build_deck
from ppt_agent.v2.typeset_pipeline import source_is_valid

class Capture(MockLLMClient):
    def __init__(self):super().__init__();self.requests=[]
    async def complete_json(self,**kwargs):
        self.requests.append(kwargs)
        return await super().complete_json(**kwargs)


def test_planning_uses_entire_document_map_and_content_uses_bounded_evidence(tmp_path):
    path=tmp_path/'report.md'
    path.write_text('# 背景与挑战\n'+('背景说明。'*1600)+'\n# 核心理念\n'+('核心方法。'*1600)+'\n# 末章投资\n后段验证数字987.6亿元。')
    client=Capture();request=BuildRequest(prompt='行业汇报覆盖各章',page_count=10,source_paths=[str(path)],output_dir=str(tmp_path/'out'))
    result=build_deck(request,client,progress=lambda _:None)
    brief=next(x for x in client.requests if x['task']=='brief')
    assert '987.6' in brief['user'] and '末章投资' in brief['user']
    store=json.loads((tmp_path/'out/checkpoints/evidence_store.json').read_text())
    assert store['documents']
    for x in client.requests:
        if x['task']=='page_content':
            p=json.loads(x['user'])
            assert len(p['source_digest'] or '')<=6500
            assert 'evidence' in x['context']
    report=json.loads(Path(result.run_report_path).read_text())
    assert all('evidence' in r for r in report['typeset_pages'])


def test_source_is_limited_to_this_pages_evidence():
    assert source_is_valid('report.pdf 第45页',['report.pdf'],{'report.pdf':63},allowed_pages={'report.pdf':[45,46]})
    assert not source_is_valid('report.pdf 第48页',['report.pdf'],{'report.pdf':63},allowed_pages={'report.pdf':[45,46]})
    assert not source_is_valid('report.pdf 第45–48页',['report.pdf'],{'report.pdf':63},allowed_pages={'report.pdf':[45,46]})
    assert not source_is_valid('other.pdf 第45页',['report.pdf'],{'report.pdf':63},allowed_pages={'report.pdf':[45,46]})


def test_citations_cannot_hide_unselected_page_in_second_clause():
    assert not source_is_valid('report.pdf 第45页；report.pdf 第48页',['report.pdf'],{'report.pdf':63},allowed_pages={'report.pdf':[45,46]})


def test_section_prompt_preserves_complete_bounded_packet():
    from ppt_agent.v2.prompts import build_section_pages_user_prompt
    from ppt_agent.v2.planning import ContentBrief,SectionOutline
    text='说明'*1800+'末尾数据987.6亿元'
    result=build_section_pages_user_prompt(ContentBrief(topic='主题',deck_title='报告',source_digest=text),
        SectionOutline(title='末章',content_pages=1),page_count=1,deck_title='报告',prior_titles=[])
    assert '987.6' in result


def test_pdf_citation_must_include_selected_physical_page():
    allowed={'report.pdf':[45]}
    assert not source_is_valid('report.pdf',['report.pdf'],{'report.pdf':63},allowed_pages=allowed)
    assert source_is_valid('report.pdf PDF p45',['report.pdf'],{'report.pdf':63},allowed_pages=allowed)
    assert not source_is_valid('report.pdf PDF p48',['report.pdf'],{'report.pdf':63},allowed_pages=allowed)


def test_every_cited_page_and_filename_with_spaces_is_checked():
    for source in ['report.pdf 第45页、第48页','report.pdf PDF p45,p48','report.pdf 第45页 第999页']:
        assert not source_is_valid(source,['report.pdf'],{'report.pdf':63},allowed_pages={'report.pdf':[45]})
    assert source_is_valid('AI Report.pdf 第45页',['AI Report.pdf'],{'AI Report.pdf':63},allowed_pages={'AI Report.pdf':[45]})


def test_cached_packet_is_the_generation_packet_not_a_new_selection(tmp_path):
    p=tmp_path/'report.md';p.write_text('# 背景与挑战\n背景问题和行业判断。\n# 核心理念\n核心方法验证。')
    request=BuildRequest(prompt='报告',page_count=10,source_paths=[str(p)],output_dir=str(tmp_path/'out'))
    build_deck(request,MockLLMClient(),progress=lambda _:None)
    cached=tmp_path/'out/checkpoints/typeset/content_003.json'
    original=json.loads(cached.read_text())['record']['evidence']
    result=build_deck(request.model_copy(update={'resume':True}),MockLLMClient(),progress=lambda _:None)
    first=json.loads(Path(result.run_report_path).read_text())['typeset_pages'][0]
    assert first['evidence']==original


def test_revision_prompt_and_cache_keep_actual_selected_evidence(tmp_path):
    import asyncio
    from ppt_agent.v2.revise import revise_deck_async
    path=tmp_path/'report.md';path.write_text('# 背景与挑战\n背景问题与方法验证。\n# 核心理念\n核心方法与方案。')
    request=BuildRequest(prompt='报告',page_count=10,source_paths=[str(path)],output_dir=str(tmp_path/'out'),deck_name='revision')
    build_deck(request,MockLLMClient(),progress=lambda _:None)
    class Edit(Capture):
        async def complete_json(self,**kwargs):
            payload=await super().complete_json(**kwargs)
            if kwargs['task']=='page_content':payload['title']='实际修改后的结论'
            return payload
    client=Edit()
    asyncio.run(revise_deck_async(output_dir=tmp_path/'out',deck_name='revision',message='修改第3页标题',selected_pages=[3],client=client,progress=lambda _:None))
    req=next(x for x in client.requests if x['task']=='page_content')
    prompt=json.loads(req['user']);assert prompt['source_digest']==req['context']['evidence']['text']
    stored=json.loads((tmp_path/'out/checkpoints/typeset/content_003.json').read_text())
    assert stored['record']['evidence']==req['context']['evidence']
