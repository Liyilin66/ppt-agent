from ppt_agent.v2.evidence import EvidenceStore
from ppt_agent.v2.search import SearchResult


def test_web_snippets_enter_the_same_retrieval_store():
    store=EvidenceStore.from_paths([]).with_search_results([SearchResult(title='可信统计',url='https://example.com/report',snippet='生活助手30.0%，会议纪要、PPT29.7%。')])
    p=store.select('会议纪要PPT占比')
    assert '29.7' in p.text and p.references==['https://example.com/report']
    assert '可信统计' in store.document_map()


def test_display_source_has_domain_title_and_local_page_format():
    from ppt_agent.v2.typeset_pipeline import display_source
    metadata={'https://example.com/report':'可信统计'}
    assert display_source('https://example.com/report',metadata)=='example.com · 可信统计'
    assert display_source('report.pdf 第12页',{})=='report.pdf 第 12 页'
    assert display_source(None,metadata) is None


def test_numeric_check_receives_only_cited_pages():
    from ppt_agent.v2.typeset_pipeline import cited_evidence
    from ppt_agent.v2.evidence import EvidencePacket
    p=EvidencePacket(text='[a.pdf | PDF p1]\n会议纪要29.7%\n[a.pdf | PDF p2]\n生活助手30.0%',references=['a.pdf'],allowed_pages={'a.pdf':[1,2]},page_counts={'a.pdf':2})
    restricted=cited_evidence('a.pdf 第1页',p)
    assert '29.7' in restricted.text and '30.0' not in restricted.text


def test_metric_mismatch_retries_then_removes_the_invalid_item(tmp_path):
    import asyncio
    from ppt_agent.v2.evidence import EvidencePacket
    from ppt_agent.v2.orchestrator import _Checkpoints
    from ppt_agent.v2.planning import ContentBrief,PageBrief,PageSlot
    from ppt_agent.v2.typeset_pipeline import generate_content
    from ppt_agent.v2.visual.profiles import PROFILES
    from ppt_agent.v2.mock import MockLLMClient
    class Wrong(MockLLMClient):
        def __init__(self,fix):super().__init__();self.n=0;self.requests=[];self.fix=fix
        async def complete_json(self,**kw):
            self.n+=1;self.requests.append(kw)
            return {'archetype':'metrics','title':'使用目的', 'source':'a.pdf 第1页',
              'metrics':[{'value':'29.7%' if self.fix and self.n==2 else '30.0%','label':'会议纪要、PPT'},
                         {'value':'80.9%','label':'回答问题'}]}
    packet=EvidencePacket(text='[a.pdf | PDF p1]\n作为生活助手30.0%；生成会议纪要、PPT29.7%；回答问题80.9%。',references=['a.pdf'],allowed_pages={'a.pdf':[1]},page_counts={'a.pdf':1})
    for fix in [True,False]:
        client=Wrong(fix)
        content,record=asyncio.run(generate_content(client,_Checkpoints(tmp_path/str(fix),resume=False),
            PageSlot(page_number=3,kind='content',brief=PageBrief(title='使用目的')),
            ContentBrief(topic='主题',deck_title='主题'),PROFILES['corporate'],evidence=packet))
        assert 'metric_mismatch' in client.requests[1]['user']
        assert record['numeric_check']['blocked_attempts']>=1
        assert record['numeric_check']['passed_after_retry']==fix
        assert '30.0' not in content.model_dump_json()
        if not fix:assert record['numeric_check']['removed']


def test_live_search_provider_results_are_present_in_content_requests(tmp_path):
    from ppt_agent.v2.mock import MockLLMClient
    from ppt_agent.v2.orchestrator import BuildRequest,build_deck
    import json
    class Search:
        async def search(self,*args,**kw):
            return [SearchResult(title='产品方案依据',url='https://example.com/report',snippet='明确问题，先开展验证，再规模推广。生活助手30.0%，会议纪要、PPT29.7%。')]
    class Capture(MockLLMClient):
        def __init__(self):super().__init__();self.requests=[]
        async def complete_json(self,**kw):self.requests.append(kw);return await super().complete_json(**kw)
    c=Capture();r=build_deck(BuildRequest(prompt='产品方案',page_count=10,enable_search=True,output_dir=str(tmp_path)),c,search_provider=Search(),progress=lambda _:None)
    assert r.pptx_path
    store=json.loads((tmp_path/'checkpoints/evidence_store.json').read_text())
    assert store['documents'][0]['url']=='https://example.com/report'
    requests=[x for x in c.requests if x['task']=='page_content']
    assert requests and any('https://example.com/report' in x['user'] for x in requests)
