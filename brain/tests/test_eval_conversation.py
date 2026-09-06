"""Ensure failed probes preserve artifacts and restore the resident model."""
import argparse
import asyncio
import json

import pytest

import eval_conversation as evaluation


def test_probe_stops_on_cpu_offload_and_restores(monkeypatch, tmp_path):
    calls = []
    async def warm(model, context):
        calls.append((model, context))
        return {'name': model, 'size': 100, 'size_vram': 80 if context == 65536 else 100}
    class Client:
        async def get(self, path):
            import httpx
            return httpx.Response(200, json={'version':'test'}, request=httpx.Request('GET','http://test'))
        async def aclose(self):
            pass
    monkeypatch.setattr(evaluation, 'warm', warm)
    monkeypatch.setattr(evaluation, 'gpu', lambda: [])
    monkeypatch.setattr(evaluation.hestia, 'client', Client())
    args = argparse.Namespace(output=tmp_path, context=32768, fill_chars=0,
        restore_context=32768, models=['candidate'], modes=['hestia'], context_only=True,
        probe_contexts=[32768,65536,131072], restore='resident')
    asyncio.run(evaluation.run(args))
    assert ('candidate',131072) not in calls
    assert calls[-1] == ('resident',32768)
    data = json.loads((tmp_path/'results.json').read_text())
    assert data['restored']
    assert data['contexts'][-1]['fully_gpu_resident'] is False


def test_initial_load_failure_still_restores_and_saves(monkeypatch, tmp_path):
    calls = []
    async def warm(model, context):
        calls.append(model)
        if model == 'candidate':
            raise RuntimeError('synthetic load failure')
        return {'name': model}
    class Client:
        async def get(self, path):
            import httpx
            return httpx.Response(200, json={'version':'test'}, request=httpx.Request('GET','http://test'))
        async def aclose(self):
            pass
    monkeypatch.setattr(evaluation, 'warm', warm)
    monkeypatch.setattr(evaluation.hestia, 'client', Client())
    args = argparse.Namespace(output=tmp_path, context=32768, fill_chars=0,
        restore_context=32768, models=['candidate'], modes=['hestia'], context_only=True,
        probe_contexts=[], restore='resident')
    with pytest.raises(RuntimeError, match='synthetic load failure'):
        asyncio.run(evaluation.run(args))
    assert calls == ['candidate','resident']
    assert json.loads((tmp_path/'results.json').read_text())['restored']
