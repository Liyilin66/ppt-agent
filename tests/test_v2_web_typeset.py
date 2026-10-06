"""Execute the Web request builder, including engine selection."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('engine', ['typeset', 'free'])
def test_web_generation_payload_selects_engine(engine):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required to execute browser request builder')
    source = (Path(__file__).parents[1] / 'src/ppt_agent/webui/app.js').read_text()
    body = source.split('function buildLongDeckPayload() {', 1)[1].split('\nfunction saveLongDeckDraft()', 1)[0]
    values = {'long_topic': '行业分析', 'long_audience': '管理层', 'long_slide_count': '20',
              'long_user_requirements': '只使用来源资料', 'layoutEngine': engine}
    js = 'const values = ' + json.dumps(values, ensure_ascii=False) + ';\n'
    js += 'const document = {getElementById: id => ({value: values[id]})};\n'
    js += 'const activeInterviewId = null; function interviewAttachmentIds() {return [];}\n'
    js += 'function buildLongDeckPayload() {' + body
    js += '\nconsole.log(JSON.stringify(buildLongDeckPayload()));'
    result = subprocess.run([node, '-'], input=js, text=True, capture_output=True, check=True)
    payload = json.loads(result.stdout)
    assert payload['layout_engine'] == engine
    assert payload['deck_type'] == 'visual_design_v2'
