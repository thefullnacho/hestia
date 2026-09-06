import asyncio
import httpx

import hestia


def test_backend_metrics_and_settings_are_request_local(monkeypatch):
    async def handle(request):
        import json
        body = json.loads(request.content)
        assert body['model'] == 'test-model'
        assert body['think'] is True
        return httpx.Response(200, json={'message': {'content': 'hello'},
            'prompt_eval_count': 20, 'eval_count': 5, 'eval_duration': 100})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            monkeypatch.setattr(hestia, 'client', client)
            trace = hestia.TurnTrace(model='test-model', think=True)
            token = hestia._trace.set(trace)
            try:
                await hestia._ollama_chat([], [])
                await hestia._ollama_chat([], [])
            finally:
                hestia._trace.reset(token)
            assert trace.usage() == {'prompt_tokens': 40, 'completion_tokens': 10, 'total_tokens': 50}
            assert trace.backend['eval_duration'] == 200
            assert hestia._trace.get() is None
    asyncio.run(scenario())


def test_eval_uses_isolated_real_write_path(monkeypatch):
    from eval_support import fixtures, records_store
    from eval_models import _logged
    async def fake_chat(convo, schemas):
        if convo[-1]['role'] == 'tool':
            return {'content': 'Logged.'}
        return {'tool_calls': [{'function': {'name': 'records', 'arguments': {
            'action': 'log', 'subject': 'Biscuit', 'did': 'vaccinated', 'kind': 'health'}}}]}
    monkeypatch.setattr(hestia, '_ollama_chat', fake_chat)
    original = records_store.DB_PATH
    with fixtures() as state:
        asyncio.run(hestia.run_agent([{'role': 'user', 'content': 'Log Biscuit vaccination'}]))
        assert _logged(state)
        assert records_store.DB_PATH != original
    assert records_store.DB_PATH == original
