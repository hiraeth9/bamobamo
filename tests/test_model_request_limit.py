"""Offline HTTPX transports and process concurrency; never access an API."""
import asyncio
from datetime import date
import json
import multiprocessing
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from model_request_limit import ModelRequestGate, ModelRequestLimitReached, model_request_limit

TODAY = date(2026, 10, 16)


def rule(tmp_path, maximum=2, day=TODAY):
    config = tmp_path/'model-request-limit.json'
    state = tmp_path/'counter.json'
    config.write_text(json.dumps({'date':day.isoformat(), 'max_requests':maximum, 'state_path':str(state)}))
    return config, state


def reserve_in_process(arguments):
    state, maximum = arguments
    try:
        ModelRequestGate(state, maximum).reserve()
        return True
    except ModelRequestLimitReached:
        return False


def test_processes_share_one_atomic_integer_and_cannot_overspend(tmp_path):
    state = tmp_path/'counter.json'
    with multiprocessing.get_context('fork').Pool(6) as pool:
        allowed = pool.map(reserve_in_process, [(str(state), 5)]*20)
    assert sum(allowed) == 5
    assert state.read_text() == '5\n'


def test_sync_failures_and_later_retries_keep_the_reserved_count(tmp_path):
    config, state = rule(tmp_path)
    calls = []
    def failing(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout('offline transport failure', request=request)
    with httpx.Client(transport=httpx.MockTransport(failing)) as client:
        with model_request_limit(config, today=TODAY):
            for _ in range(2):
                with pytest.raises(httpx.ReadTimeout):
                    client.post('https://provider.test/v1/chat/completions', json={'private':'never stored'},
                                headers={'Authorization':'offline-test-token'})
        # A new launcher context must reuse the shared count, not reset it.
        with model_request_limit(config, today=TODAY), pytest.raises(ModelRequestLimitReached, match='2次'):
            client.post('https://provider.test/v1/chat/completions')
    assert len(calls) == 2
    assert state.read_text() == '2\n'


def test_unlimited_mode_keeps_existing_count_and_counts_failed_sends(tmp_path):
    config, state = rule(tmp_path, maximum=None)
    state.write_text('54\n')
    sends = []
    def failing(request):
        sends.append(request)
        raise httpx.ReadTimeout('offline failure', request=request)
    with model_request_limit(config, today=TODAY) as gate:
        assert gate.check_available() is None
        with httpx.Client(transport=httpx.MockTransport(failing)) as client:
            for _ in range(9):
                with pytest.raises(httpx.ReadTimeout):
                    client.post('https://provider.test/v1/chat/completions')
        assert not gate.denied and gate.check_available() is None
    assert len(sends) == 9 and state.read_text() == '63\n'


def test_sync_async_main_title_and_auxiliary_requests_share_the_cap(tmp_path):
    config, state = rule(tmp_path, maximum=3)
    sent = []
    def response(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={'ok':True})
    async def asynchronous():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            # Same endpoint used by title/vision calls; no role-based exemption.
            await client.post('https://provider.test/v1/chat/completions', json={'role':'title'})
            await client.post('https://provider.test/compatible-mode/v1/responses', json={'role':'auxiliary'})
            with pytest.raises(ModelRequestLimitReached):
                await client.post('https://provider.test/v1/responses')
    original = httpx.Client.send, httpx.AsyncClient.send
    with model_request_limit(config, today=TODAY):
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            client.post('https://provider.test/v1/chat/completions', json={'role':'main'})
        asyncio.run(asynchronous())
    assert len(sent) == 3 and state.read_text() == '3\n'
    assert (httpx.Client.send, httpx.AsyncClient.send) == original


def test_async_concurrent_attempts_stop_before_transport(tmp_path):
    config, state = rule(tmp_path, maximum=4)
    sent = []
    async def response(request):
        await asyncio.sleep(0)
        sent.append(request.url.path)
        return httpx.Response(503)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            async def attempt():
                try:
                    await client.post('https://provider.test/v1/responses')
                    return True
                except ModelRequestLimitReached:
                    return False
            return await asyncio.gather(*(attempt() for _ in range(12)))
    with model_request_limit(config, today=TODAY):
        allowed = asyncio.run(exercise())
    assert sum(allowed) == len(sent) == 4
    assert state.read_text() == '4\n'


@pytest.mark.parametrize('method,path,counted', [
    ('POST', '/compatible-mode/v1/chat/completions', True),
    ('POST', '/openai/deployments/model/chat/completions', True),
    ('POST', '/v1/responses/', True),
    ('GET', '/v1/responses', False),
    ('POST', '/v1/embeddings', False),
    ('POST', '/mcp', False),
])
def test_only_post_model_paths_are_counted(tmp_path, method, path, counted):
    config, state = rule(tmp_path)
    with model_request_limit(config, today=TODAY), httpx.Client(
            transport=httpx.MockTransport(lambda request:httpx.Response(200))) as client:
        assert client.request(method, 'https://provider.test'+path).status_code == 200
    assert state.exists() == counted
    if counted:
        assert json.loads(state.read_text()) == 1


def test_no_configuration_leaves_both_httpx_methods_and_files_unchanged(tmp_path):
    original = httpx.Client.send, httpx.AsyncClient.send
    with model_request_limit(tmp_path/'absent.json', today=TODAY) as gate:
        assert gate is None
        assert (httpx.Client.send, httpx.AsyncClient.send) == original
    assert list(tmp_path.iterdir()) == []


def test_non_current_shanghai_date_does_not_activate_or_reset_counter(tmp_path):
    config, state = rule(tmp_path, day=date(2026, 10, 15))
    state.write_text('9\n')
    original = httpx.Client.send, httpx.AsyncClient.send
    with model_request_limit(config, today=TODAY) as gate:
        assert gate is None
        assert (httpx.Client.send, httpx.AsyncClient.send) == original
    assert state.read_text() == '9\n'


def test_preflight_is_read_only_and_denial_survives_a_swallowed_exception(tmp_path):
    state = tmp_path/'counter.json'
    gate = ModelRequestGate(state, 1)
    assert gate.check_available() == 1
    assert not state.exists() and not gate.denied
    gate.reserve()
    assert not gate.denied  # Using the last permitted send is still allowed.
    gate.raise_if_denied()
    try:
        gate.reserve()
    except BaseException:
        pass  # Mirrors Hermes' thread/future error handling.
    assert gate.denied
    with pytest.raises(ModelRequestLimitReached, match='上限'):
        gate.raise_if_denied()
    with pytest.raises(ModelRequestLimitReached):
        ModelRequestGate(state, 1).check_available()
    assert state.read_text() == '1\n'
