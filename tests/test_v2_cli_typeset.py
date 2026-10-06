import argparse
import pytest
from ppt_agent.v2.cli import _add_build_arguments, _build_request


@pytest.mark.parametrize('engine', ['typeset', 'free'])
def test_cli_layout_engine_and_profile(engine):
    parser = argparse.ArgumentParser()
    _add_build_arguments(parser, offline=True)
    args = parser.parse_args(['--prompt', '需求', '--output-dir', '/tmp/demo',
                              '--layout-engine', engine, '--style-profile', 'consulting'])
    request = _build_request(args, offline=True)
    assert request.layout_engine == engine
    assert request.style_profile == 'consulting'
