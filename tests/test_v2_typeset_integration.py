import json
from pathlib import Path

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest,build_deck,plan_deck

class ProfileClient(MockLLMClient):
    def __init__(self):
        super().__init__();self.tasks=[]
    async def complete_json(self, *, task, **kwargs):
        self.tasks.append(task)
        payload=await super().complete_json(task=task, **kwargs)
        if task=='brief':payload.update(deck_type='launch',deck_type_reason='受众是发布会用户，需要突出核心价值。')
        return payload

def test_default_typeset_uses_selected_profile_not_theme_or_coordinates(tmp_path):
    client=ProfileClient()
    result=build_deck(BuildRequest(prompt='产品发布',page_count=10,output_dir=str(tmp_path)),client,progress=lambda _:None)
    assert result.pptx_path
    assert 'page_content' in client.tasks
    assert 'page_design' not in client.tasks and 'theme' not in client.tasks and 'anchor_design' not in client.tasks
    report=json.loads(Path(result.run_report_path).read_text())
    assert report['request']['layout_engine']=='typeset'
    theme=json.loads((tmp_path/'checkpoints/theme.json').read_text())
    from ppt_agent.v2.visual.profiles import PROFILES
    assert theme==PROFILES['launch'].theme().model_dump(mode='json')

def test_explicit_free_keeps_original_pipeline(tmp_path):
    client=ProfileClient()
    result=build_deck(BuildRequest(prompt='旧流程',page_count=10,output_dir=str(tmp_path),layout_engine='free'),client,progress=lambda _:None)
    assert result.pptx_path
    assert 'page_design' in client.tasks and 'theme' in client.tasks
    assert 'page_content' not in client.tasks

def test_plan_override_survives_resume(tmp_path):
    request=BuildRequest(prompt='发布会',page_count=10,output_dir=str(tmp_path),style_profile='consulting')
    plan=plan_deck(request,ProfileClient(),progress=lambda _:None)
    assert plan.brief.deck_type=='consulting'
    assert plan.brief.deck_type_reason
    updated=plan_deck(request.model_copy(update={'resume':True,'style_profile':'training'}),ProfileClient(),progress=lambda _:None)
    assert updated.brief.deck_type=='training'

def test_resume_rejects_mixing_layout_engines(tmp_path):
    request=BuildRequest(prompt='引擎边界',page_count=10,output_dir=str(tmp_path))
    build_deck(request,MockLLMClient(),progress=lambda _:None)
    import pytest
    with pytest.raises(ValueError,match='layout engine'):
        build_deck(request.model_copy(update={'resume':True,'layout_engine':'free'}),MockLLMClient(),progress=lambda _:None)

def test_fresh_free_run_clears_previous_typeset_engine(tmp_path):
    request=BuildRequest(prompt='引擎边界',page_count=10,output_dir=str(tmp_path))
    build_deck(request,MockLLMClient(),progress=lambda _:None)
    build_deck(request.model_copy(update={'layout_engine':'free'}),MockLLMClient(),progress=lambda _:None)
    assert json.loads((tmp_path/'checkpoints/typeset_config.json').read_text())['layout_engine']=='free'
