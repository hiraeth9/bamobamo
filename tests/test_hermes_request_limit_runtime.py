"""Offline real-Hermes worker/SDK/hard-exit regression, with an isolated profile.

The child uses the installed Hermes Python and HTTPX MockTransport. No production
configuration, live request counter, session database or credentials are used.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]


def runtime_fixture(mode, output):
    output.mkdir(parents=True, exist_ok=True)
    profile = output/'hermes-profile'
    profile.mkdir()
    os.environ['HERMES_HOME'] = str(profile)
    (profile/'config.yaml').write_text(json.dumps({'model':{'default':'offline-fixture', 'provider':'openai'},
                                                  'mcp_servers':{}}))
    sys.path[:0] = [str(PROJECT/'scripts'), str(PROJECT/'hermes')]
    import socket
    def forbid_network(*args, **kwargs):
        raise AssertionError('Offline Hermes regression forbids real socket connections')
    socket.socket.connect = forbid_network
    import httpx
    from openai import OpenAI
    from run_agent import AIAgent
    import hermes_cli.main as hermes_main
    from model_request_limit import model_request_limit
    from hermes_director_cli import guard_oneshot_limit_exit
    from datetime import datetime
    from zoneinfo import ZoneInfo

    state = output/'mock-counter.json'
    rule = output/'mock-rule.json'
    rule.write_text(json.dumps({'date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                               'max_requests':1, 'state_path':str(state)}))
    sent = []
    def response(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={
            'id':'offline-completion', 'object':'chat.completion', 'created':0, 'model':'offline-fixture',
            'choices':[{'index':0, 'message':{'role':'assistant', 'content':'offline ok'}, 'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1, 'completion_tokens':1, 'total_tokens':2}})
    def mock_client(**kwargs):
        return OpenAI(api_key='offline-fixture-key', base_url='https://provider.test/v1', max_retries=0,
                      http_client=httpx.Client(transport=httpx.MockTransport(response)))

    agent = AIAgent(api_key='offline-fixture-key', base_url='https://provider.test/v1', provider='openai',
                    api_mode='chat_completions', model='offline-fixture', enabled_toolsets=[], quiet_mode=True,
                    skip_context_files=True, skip_memory=True, skip_background_review=True,
                    save_trajectories=False, max_iterations=2, session_db=None)
    agent._create_request_openai_client = mock_client
    kwargs = {'model':'offline-fixture', 'messages':[{'role':'user', 'content':'offline request'}]}
    with model_request_limit(rule) as gate, guard_oneshot_limit_exit(hermes_main, gate):
        gate.check_available()
        first = agent._interruptible_api_call(kwargs)
        assert first.choices[0].message.content == 'offline ok'
        assert len(sent) == 1 and not gate.denied
        second = agent._interruptible_api_call(kwargs) if mode == 'denied' else first
        if mode == 'denied':
            # The actual Hermes interrupt worker catches Exception, so the
            # SystemExit leaves its result empty: this is the observed failure.
            assert second is None and gate.denied
        else:
            assert second is first and not gate.denied
        assert len(sent) == 1 and state.read_text() == '1\n'
        agent.close()
        hermes_main._cleanup_oneshot_runtime()
        print('REAL_HERMES_OFFLINE_LIMIT', json.dumps({'mode':mode, 'mock_model_sends':len(sent),
              'denied':gate.denied, 'real_network_requests':0, 'counter':1}), flush=True)
        # Exercise the real upstream os._exit path, not a fake main/lambda exit.
        hermes_main._exit_after_oneshot(0)


def test_real_hermes_thread_and_hard_exit_propagate_runtime_denial(tmp_path):
    import pytest
    python = PROJECT/'hermes/.venv/bin/python'
    if not python.is_file():
        pytest.skip('Requires the installed Hermes Python; ordinary gate tests remain offline and standalone')
    for mode, expected in [('allowed', 0), ('denied', 1)]:
        folder = tmp_path/mode
        env = {key:value for key,value in os.environ.items()
               if not any(word in key.upper() for word in ('API_KEY', 'API_TOKEN', 'ACCESS_TOKEN'))}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run([str(python), str(Path(__file__).resolve()), '--mode', mode, '--output', str(folder)],
                                cwd=PROJECT, env=env, capture_output=True, text=True, timeout=40)
        (tmp_path/(mode+'-stdout.txt')).write_text(result.stdout)
        (tmp_path/(mode+'-stderr.txt')).write_text(result.stderr)
        assert result.returncode == expected, result.stdout+'\n'+result.stderr
        assert 'REAL_HERMES_OFFLINE_LIMIT' in result.stdout
        assert ('本轮模型请求已达到上限' in result.stderr) == (mode == 'denied')
        assert (folder/'mock-counter.json').read_text() == '1\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['allowed', 'denied'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    runtime_fixture(arguments.mode, arguments.output)
