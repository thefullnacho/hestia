import asyncio
import json

import httpx

import hestia
from eval_support import fixtures


def test_context_preparation_is_inside_turn_deadline(monkeypatch):
    async def slow(_):
        await asyncio.sleep(1)
    monkeypatch.setattr(hestia, '_build_system_prompt', slow)
    monkeypatch.setattr(hestia, 'TURN_BUDGET', .02)
    trace = hestia.TurnTrace()
    assert asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'hello'}], trace=trace)) == hestia._TOO_SLOW
    assert trace.total_seconds < .2


def test_admission_rejects_excess_turn_and_recovers(monkeypatch):
    monkeypatch.setattr(hestia, '_turn_slots', asyncio.BoundedSemaphore(1))
    async def scenario():
        await hestia._turn_slots.acquire()
        try:
            assert await hestia.run_agent([{'role': 'user', 'content': 'hello'}]) == hestia._BUSY
        finally:
            hestia._turn_slots.release()
    asyncio.run(scenario())


def test_no_tool_starts_when_final_reserve_is_reached(monkeypatch):
    async def chat(*_):
        return {'tool_calls': [{'function': {'name': 'home', 'arguments': {'action': 'turn_off', 'entity_id': 'light.light_kitchen_lights'}}}]}
    monkeypatch.setattr(hestia, '_ollama_chat', chat)
    monkeypatch.setattr(hestia, 'FINAL_RESERVE', 100)
    with fixtures() as state:
        asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'turn off kitchen lights'}]))
        assert state['calls'] == []


def test_native_final_stream_emits_before_generation_finishes(monkeypatch):
    received = []
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":{"content":"Hello "},"done":false}\n'
            assert received == ['Hello ']
            yield b'{"message":{"content":"world"},"done":true,"eval_count":2}\n'
    async def handle(request):
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200, stream=Chunks())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            monkeypatch.setattr(hestia, 'client', client)
            async def sink(text):
                received.append(text)
            token = hestia._stream_sink.set(sink)
            try:
                result = await hestia._ollama_chat([], [])
                assert result['content'] == 'Hello world'
            finally:
                hestia._stream_sink.reset(token)
    asyncio.run(scenario())


def test_sse_framing_and_request_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(hestia.note_taker, 'ENABLED', False)
    async def agent(messages, *, trace):
        return 'Hello'
    monkeypatch.setattr(hestia, 'run_agent', agent)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hestia.app), base_url='http://test') as client:
            response = await client.post('/v1/chat/completions', json={
                'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True})
            assert response.status_code == 200
            assert 'Hello' in response.text
            assert response.text.endswith('data: [DONE]\n\n')
            assert 'usage' in response.text
    asyncio.run(scenario())


def test_invalid_json_and_oversize_body_are_rejected():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hestia.app), base_url='http://test') as client:
            assert (await client.post('/v1/chat/completions', content='{')).status_code == 400
            assert (await client.post('/v1/chat/completions', content='x'*160001)).status_code == 413
    asyncio.run(scenario())


def test_independent_reads_overlap(monkeypatch):
    active, peak = 0, 0
    async def read(name, args, budget):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return 'fixture result', 'ok'
    replies = [{'tool_calls': [
        {'function': {'name': 'home', 'arguments': {'action': 'get_state'}}},
        {'function': {'name': 'weather', 'arguments': {'action': 'rain'}}}]}, {'content': 'Result'}]
    async def chat(*_):
        return replies.pop(0)
    monkeypatch.setattr(hestia, '_run_tool', read)
    monkeypatch.setattr(hestia, '_ollama_chat', chat)
    with fixtures():
        asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'How is the garden and will it rain?'}]))
    assert peak == 2
