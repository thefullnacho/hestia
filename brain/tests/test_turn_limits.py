import asyncio
import json

import httpx
import pytest

import backend  # noqa: E402
import routing  # noqa: E402
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
    monkeypatch.setattr(backend, 'ollama_chat', chat)
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
            monkeypatch.setattr(backend, 'client', client)
            async def sink(text):
                received.append(text)
            token = hestia._stream_sink.set(sink)
            try:
                result = await backend.ollama_chat([], [])
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
    monkeypatch.setattr(backend, 'ollama_chat', chat)
    with fixtures():
        asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'How is the garden and will it rain?'}]))
    assert peak == 2


def test_simple_light_write_needs_only_one_model_round(monkeypatch):
    calls = []
    async def chat(*_):
        calls.append(1)
        return {'tool_calls': [{'function': {'name': 'home', 'arguments': {
            'action': 'turn_off', 'entity_id': 'light.light_kitchen_lights'}}}]}
    monkeypatch.setattr(backend, 'ollama_chat', chat)
    with fixtures():
        answer = asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'Turn off the kitchen lights.'}]))
    assert len(calls) == 1
    assert 'off' in answer


def test_mixed_read_and_write_preserves_information(monkeypatch):
    replies = [{'tool_calls': [{'function': {'name': 'home', 'arguments': {
        'action': 'turn_off', 'entity_id': 'light.light_kitchen_lights'}}}]},
        {'content': 'Biscuit is a corgi.'}]
    async def chat(*_):
        return replies.pop(0)
    monkeypatch.setattr(backend, 'ollama_chat', chat)
    with fixtures():
        answer = asyncio.run(hestia.run_agent([{'role': 'user', 'content':
            'Turn off the kitchen lights and tell me what breed Biscuit is.'}]))
    assert 'corgi' in answer
    assert 'Verified actions' in answer


def test_interrupted_stream_receipt_matches_visible_partial_text(monkeypatch):
    async def loop(messages):
        trace = hestia._trace.get()
        trace.emitted = True
        trace.streamed_text = 'Partial answer'
        return hestia._BACKEND_DOWN
    monkeypatch.setattr(hestia, '_agent_loop', loop)
    trace = hestia.TurnTrace()
    answer = asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'hello'}], trace=trace))
    assert answer == 'Partial answer\n' + hestia._BACKEND_DOWN
    assert trace.failure == hestia._BACKEND_DOWN


def test_empty_action_response_gets_one_recovery_attempt(monkeypatch):
    replies = [{'content': ''}, {'tool_calls': [{'function': {'name': 'home', 'arguments': {
        'action': 'turn_off', 'entity_id': 'light.light_kitchen_lights'}}}]}]
    async def chat(*_):
        return replies.pop(0)
    monkeypatch.setattr(backend, 'ollama_chat', chat)
    with fixtures() as state:
        answer = asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'Turn off the kitchen lights.'}]))
        assert state['light'] == 'off'
        assert 'off' in answer
    assert replies == []


def test_unattempted_explicit_write_cannot_claim_success(monkeypatch):
    calls = []
    async def chat(*_):
        calls.append(1)
        return {'content': 'Recorded successfully.'}
    monkeypatch.setattr(backend, 'ollama_chat', chat)
    with fixtures() as state:
        answer = asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'Log the vaccination.'}]))
        assert state['calls'] == []
    assert answer == hestia._NO_ACTION
    assert len(calls) == 2


def test_followup_actuation_is_an_explicit_required_write():
    assert routing.required_writes('Turn them back on.') == {'home'}
    assert routing.required_writes('How do I turn them on?') == set()


def test_stream_garbage_line_is_a_backend_failure(monkeypatch):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":{"content":"Hi"},"done":false}\n'
            yield b'<html>502 bad gateway</html>\n'
    async def handle(request):
        return httpx.Response(200, stream=Chunks())
    async def sink(text):
        pass
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            monkeypatch.setattr(backend, 'client', client)
            monkeypatch.setattr(routing, 'request_schemas', lambda _: [])
            async def prompt(_):
                return ''
            monkeypatch.setattr(hestia, '_build_system_prompt', prompt)
            token = hestia._stream_sink.set(sink)
            try:
                return await hestia.run_agent([{'role': 'user', 'content': 'hello'}])
            finally:
                hestia._stream_sink.reset(token)
    answer = asyncio.run(scenario())
    # The tokens already shown stay; the raw JSON decode error must not become the reply.
    assert answer == 'Hi\n' + hestia._BACKEND_DOWN
    assert 'Expecting value' not in answer


def test_non_light_turn_on_is_not_a_required_write():
    assert routing.required_writes('Turn on the news.') == set()
    assert routing.required_writes('Turn off the kitchen lights.') == {'home'}
    assert routing.required_writes('Turn it off.') == {'home'}


def test_explicit_light_command_always_offers_home():
    with fixtures():
        for text in ['Log that Biscuit was vaccinated, then turn on the porch lights.',
                     'Turn them back on.', 'shut off the outside', 'kill the outside',
                     'put on the outside', 'switch on the lights in the outside']:
            token = routing.prepared.set(None)
            try:
                offered = {s['function']['name'] for s in routing.request_schemas(text)}
            finally:
                routing.prepared.reset(token)
            assert routing.required_writes(text) <= offered, text


def test_repair_is_skipped_for_a_tool_the_request_did_not_offer(monkeypatch):
    async def handle(request):
        body = json.loads(request.content)
        assert 'format' not in body, 'must not run schema repair for an un-offered tool'
        return httpx.Response(200, json={'message': {'content': 'plain answer'}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            monkeypatch.setattr(backend, 'client', client)
            trace = hestia.TurnTrace(repair_tool='home')
            token = hestia._trace.set(trace)
            try:
                records_only = [s for s in hestia.tools.SCHEMAS if s['function']['name'] == 'records']
                msg = await backend.ollama_chat([], records_only)
                assert msg['content'] == 'plain answer'
                assert trace.repair_tool == ''
            finally:
                hestia._trace.reset(token)
    asyncio.run(scenario())



# A command whose object is a light area the house names ("Outside lights" is "outside"), with
# the verbs people actually use. LFM3-1.7B read "shut off the outside" as a state question on
# 14% of held-out tries, and the detector, wanting the word "light", never asked for the write.
@pytest.mark.parametrize("text", [
    "shut off the outside", "kill the outside", "cut the outside", "turn on the outside",
    "put on the outside", "turn the outside on", "switch on the lights in the outside",
    "kill the lights in the outside", "flip on the lights in the outside", "shut off the living room",
])
def test_bare_area_commands_are_required_light_writes(monkeypatch, text):
    monkeypatch.setitem(hestia.tools.home._cache, "areas", ["kitchen", "living room", "outside"])
    assert routing.required_writes(text) == {"home"}


@pytest.mark.parametrize("text", [
    "kill the outside noise", "turn on the kitchen radio", "turn off the kitchen timer",
    "shut off the kitchen faucet", "turn off the outside faucet", "put it on the list",
    "cut it out", "kill it", "put on some music", "How do I turn the outside off?",
    "turn on the living room tv",
])
def test_talk_that_mentions_an_area_is_not_a_light_command(monkeypatch, text):
    monkeypatch.setitem(hestia.tools.home._cache, "areas", ["kitchen", "living room", "outside"])
    assert "home" not in routing.required_writes(text)


def test_with_no_catalog_read_yet_only_light_words_count(monkeypatch):
    monkeypatch.setitem(hestia.tools.home._cache, "areas", [])
    assert routing.required_writes("shut off the outside") == set()
    assert routing.required_writes("shut off the outside lights") == {"home"}


def test_only_names_that_say_lights_become_areas(monkeypatch):
    import time
    from tools import home
    monkeypatch.setattr(home, "_cache", {"t": 0.0, "catalog": "", "soil": "", "areas": []})
    monkeypatch.setattr(time, "time", lambda: 1.0)
    home._cache_states([
        {"entity_id": "light.light_outside_lights", "state": "off", "attributes": {"friendly_name": "Outside lights"}},
        {"entity_id": "light.light_dining_room_lights", "state": "off", "attributes": {"friendly_name": "Dining room lights"}},
        {"entity_id": "light.tv", "state": "off", "attributes": {"friendly_name": "TV"}},
        {"entity_id": "light.fan", "state": "off", "attributes": {"friendly_name": "Fan"}},
    ])
    assert home.light_areas() == ["dining room", "outside"]


def test_a_detected_command_keeps_its_tools_after_a_read(monkeypatch):
    """LFM3 looked up the lights before "shut off the outside"; the loop then dropped tools for a
    quick answer, so the write it needed next could never happen."""
    seen = []
    replies = [{'tool_calls': [{'function': {'name': 'home', 'arguments': {'action': 'get_state'}}}]},
               {'tool_calls': [{'function': {'name': 'home', 'arguments': {
                   'action': 'turn_off', 'entity_id': 'light.light_kitchen_lights'}}}]},
               {'content': 'Done.'}]
    async def chat(convo, schemas):
        seen.append([s['function']['name'] for s in schemas])
        return replies.pop(0)
    monkeypatch.setattr(backend, 'ollama_chat', chat)
    with fixtures() as state:
        asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'shut off the kitchen'}]))
        assert state['light'] == 'off'
    assert 'home' in seen[1]
