"""Exercise MCP using the official in-memory client and real stdio transport."""
import asyncio
import json
import os
import select
import subprocess
import sys

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import JSONRPCMessage

from ppt_agent import __version__, deck_jobs
from ppt_agent.mcp_server import create_server


def test_ping_over_sdk_memory_connection_loads_dotenv_and_shared_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PPT_AGENT_DATA_DIR', '')
    monkeypatch.delenv('PPT_AGENT_DATA_DIR')
    (tmp_path / '.env').write_text('PPT_AGENT_DATA_DIR=shared-data\n')
    directory = tmp_path / 'shared-data'
    store = deck_jobs.JobStore(directory / 'jobs.sqlite3')
    job = store.create_job(job_type='long_deck_v2')

    async def check():
        async with create_connected_server_and_client_session(create_server()) as client:
            tools = await client.list_tools()
            assert [tool.name for tool in tools.tools] == ['ping']
            result = await client.call_tool('ping', {})
            assert result.isError is False
            value = result.structuredContent or json.loads(result.content[0].text)
            assert value == {'version': __version__, 'data_dir': str(directory.resolve())}

    asyncio.run(check())
    assert deck_jobs.JobStore(directory / 'jobs.sqlite3').get_latest_job().job_id == job.job_id


def test_stdio_cli_emits_only_protocol_and_logs_to_stderr(tmp_path):
    environment = {**os.environ, 'PPT_AGENT_DATA_DIR': str(tmp_path / 'data')}
    # A pre-existing stdout log handler must not leak into protocol output.
    command = [sys.executable, '-c',
               'import logging,sys; logging.basicConfig(stream=sys.stdout); '
               'from ppt_agent.cli import main; raise SystemExit(main(["mcp"]))']
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, cwd=tmp_path, env=environment)
    output = []
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    def receive():
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready, 'MCP response timed out'
        line = process.stdout.readline()
        assert line, 'MCP process exited before replying'
        JSONRPCMessage.model_validate_json(line)
        output.append(line)
        return json.loads(line)
    try:
        send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'pytest','version':'1'}}})
        assert receive()['id'] == 1
        send({'jsonrpc':'2.0','method':'notifications/initialized'})
        send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        assert [t['name'] for t in receive()['result']['tools']] == ['ping']
        send({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'ping','arguments':{}}})
        result = receive()['result']
        assert result.get('isError', False) is False
        value = result.get('structuredContent') or json.loads(result['content'][0]['text'])
        assert value == {'version':__version__, 'data_dir':str((tmp_path / 'data').resolve())}
        process.stdin.close()
        process.wait(timeout=10)
        assert process.returncode == 0
        assert process.stdout.read() == ''
        assert 'ppt-agent MCP ready' in process.stderr.read()
        assert (tmp_path / 'data' / 'jobs.sqlite3').is_file()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_mcp_import_does_not_load_api():
    subprocess.run([sys.executable, '-c',
                    "import sys; import ppt_agent.mcp_server; assert 'ppt_agent.api' not in sys.modules"],
                   check=True)
