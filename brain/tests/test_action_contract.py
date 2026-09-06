import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import hestia
import operation_store
import tools
from tool_contract import receipt, validate


@pytest.mark.parametrize('args', [[], True, 'bad', {'action': 'turn_on', 'entity_id': 2},
    {'action': 'turn_on', 'entity_id': 'light.kitchen', 'brightness_pct': True},
    {'action': 'turn_on', 'entity_id': 'light.kitchen', 'brightness_pct': 101},
    {'action': 'turn_on', 'entity_id': 'all'}, {'action': 'turn_on'},
    {'action': 'turn_on', 'entity_id': 'light.kitchen', 'extra': 1}])
def test_invalid_calls_cannot_dispatch(args):
    assert validate('home', args, tools.SCHEMAS)


def test_disallowed_tool_rejected_even_if_registered():
    assert 'not allowed' in validate('memory', {'op': 'write', 'content': 'x'}, [])


def test_write_errors_and_timeouts_are_not_success():
    assert receipt('reminder', {'action': 'create'}, "I couldn't read the time").status == 'failed'
    assert receipt('home', {'action': 'toggle'}, 'Error talking to HA').status == 'unknown'
    assert receipt('records', {'action': 'log'}, 'slow', 'timeout').status == 'unknown'


def test_unknown_write_stops_changed_argument_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    async def prompt(_):
        return ''
    monkeypatch.setattr(hestia, '_build_system_prompt', prompt)
    monkeypatch.setattr(hestia, '_request_schemas', lambda _: tools.SCHEMAS)
    calls = []
    def dispatch(name, args):
        calls.append(args)
        return 'Error talking to HA: timeout'
    monkeypatch.setattr(tools, 'dispatch', dispatch)
    replies = [
        {'tool_calls': [{'function': {'name': 'home', 'arguments': {'action': 'toggle', 'entity_id': 'light.kitchen'}}}]},
        {'tool_calls': [{'function': {'name': 'home', 'arguments': {'action': 'toggle', 'entity_id': 'light.other'}}}]},
        {'content': 'All done!'}]
    async def chat(*_):
        return replies.pop(0)
    monkeypatch.setattr(hestia, '_ollama_chat', chat)
    answer = asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'toggle the lights'}]))
    assert len(calls) == 1
    assert 'Unknown' in answer
    assert 'All done' not in answer


def test_request_replay_and_conflicting_key(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(hestia.note_taker, 'ENABLED', False)
    calls = []
    async def agent(messages, *, trace):
        calls.append(messages)
        return 'receipt'
    monkeypatch.setattr(hestia, 'run_agent', agent)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hestia.app), base_url='http://test') as client:
            headers = {'Idempotency-Key': 'unique-key-123'}
            body = {'messages': [{'role': 'user', 'content': 'test'}]}
            first = await client.post('/v1/chat/completions', json=body, headers=headers)
            second = await client.post('/v1/chat/completions', json=body, headers=headers)
            assert first.json() == second.json()
            body['messages'][0]['content'] = 'different'
            third = await client.post('/v1/chat/completions', json=body, headers=headers)
            assert third.status_code == 409
    asyncio.run(scenario())
    assert len(calls) == 1


def test_late_write_completes_once_and_remains_observable(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    release, finished = threading.Event(), threading.Event()
    calls = []
    def dispatch(name, args):
        calls.append(args)
        release.wait(1)
        finished.set()
        return 'Logged vaccination.'
    monkeypatch.setattr(tools, 'dispatch', dispatch)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(hestia, '_tool_executor', pool)
    monkeypatch.setattr(hestia, '_tool_slots', asyncio.BoundedSemaphore(1))
    async def scenario():
        trace = hestia.TurnTrace(request_id='late-write')
        token = hestia._trace.set(trace)
        try:
            args = {'action': 'log', 'detail': 'vaccination'}
            _, outcome = await hestia._run_tool('records', args, .02)
            assert outcome == 'timeout'
            release.set()
            await asyncio.to_thread(finished.wait, 1)
            # Wait for the worker's durable receipt, then repeat under the same operation ID.
            pool.shutdown(wait=True)
            assert operation_store.operations(trace.request_id)[0]['status'] == 'succeeded'
            assert operation_store.execute_once(trace.request_id, 'records', args, dispatch) == 'Logged vaccination.'
            assert len(calls) == 1
        finally:
            hestia._trace.reset(token)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_text_recovery_accepts_only_exact_valid_read_calls():
    import json
    from tool_contract import read_call_from_text
    call = {'name': 'records', 'arguments': {'action': 'entity', 'name': 'Biscuit'}}
    assert read_call_from_text(json.dumps(call), tools.SCHEMAS)
    assert read_call_from_text(json.dumps(call), []) is None
    assert read_call_from_text('Example: ' + json.dumps(call), tools.SCHEMAS) is None
    call['arguments'] = {'action': 'log', 'detail': 'vaccinated'}
    assert read_call_from_text(json.dumps(call), tools.SCHEMAS) is None


@pytest.mark.parametrize('value,valid', [(2, True), ('2 lb 7 oz', True),
                                        (True, False), ([], False), (None, False), (0, False)])
def test_harvest_union_quantity_schema(value, valid):
    from copy import deepcopy
    schemas = deepcopy(tools.SCHEMAS)
    schema = next(s['function']['parameters'] for s in schemas if s['function']['name'] == 'records')
    schema['properties']['qty']['type'] = ['number', 'string']
    error = validate('records', {'action': 'harvest', 'bed': 'Test bed', 'crop': 'Tomatoes', 'qty': value}, schemas)
    assert (error is None) == valid


def test_transient_failure_is_not_replayed_under_key(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(hestia.note_taker, 'ENABLED', False)
    replies = [hestia._BUSY, 'real answer']
    async def agent(messages, *, trace):
        return replies.pop(0)
    monkeypatch.setattr(hestia, 'run_agent', agent)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hestia.app), base_url='http://test') as client:
            headers = {'Idempotency-Key': 'retry-key-0001'}
            body = {'messages': [{'role': 'user', 'content': 'hello there'}]}
            first = await client.post('/v1/chat/completions', json=body, headers=headers)
            second = await client.post('/v1/chat/completions', json=body, headers=headers)
            assert first.json()['choices'][0]['message']['content'] == hestia._BUSY
            assert second.json()['choices'][0]['message']['content'] == 'real answer'
            # The real answer is now the durable one.
            third = await client.post('/v1/chat/completions', json=body, headers=headers)
            assert third.json() == second.json()
    asyncio.run(scenario())
    assert replies == []


def test_keyless_requests_leave_no_ledger_row(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(hestia.note_taker, 'ENABLED', False)
    async def agent(messages, *, trace):
        return 'answer'
    monkeypatch.setattr(hestia, 'run_agent', agent)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hestia.app), base_url='http://test') as client:
            r = await client.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'hello'}]})
            assert r.status_code == 200
    asyncio.run(scenario())
    # No key means nothing to replay, so the request never touches the ledger.
    assert not (tmp_path / 'operations.db').exists() or not operation_store.operations('')
    from contextlib import closing
    with closing(operation_store._connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 0


def test_ledger_prunes_old_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    from contextlib import closing
    with closing(operation_store._connect()) as conn, conn:
        conn.execute('INSERT INTO requests VALUES (?, ?, ?, ?)', ('old-key', 'fp', '{}', 1.0))
        conn.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)', ('old-op', 'old-key', 'home', 'succeeded', 'x', 1.0))
    operation_store.claim_request('fresh-key-0001', [])
    with closing(operation_store._connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0
