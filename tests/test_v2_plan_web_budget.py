"""Execute the browser's page count functions against server policy."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from ppt_agent.v2.planning import build_skeleton, editable_plan_from_skeleton, DeckOutline, SectionOutline


@pytest.mark.parametrize('total,sections', [(10, 3), (20, 6), (30, 6), (100, 8), (36, 6)])
def test_browser_page_count_matches_server(total, sections):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required to execute browser counting functions')
    outline = DeckOutline(deck_title='预算', sections=[SectionOutline(title=f'章{i}', content_pages=3)
                                                   for i in range(sections)])
    plan = editable_plan_from_skeleton(build_skeleton(outline, total_pages=total, language='zh-CN'))
    source = (Path(__file__).parents[1] / 'src/ppt_agent/webui/app.js').read_text()
    functions = source.split('function planContentPages(plan) {', 1)[1].split('function updatePlanSummary()', 1)[0]
    js = 'function planContentPages(plan) {' + functions + '\nconsole.log(planTotalPages(' + plan.model_dump_json() + '));'
    result = subprocess.run([node, '-'], input=js, text=True, capture_output=True, check=True)
    assert int(result.stdout.strip()) == total == plan.total_pages()
