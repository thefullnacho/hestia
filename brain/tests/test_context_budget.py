import asyncio
import json

import pytest

import context_budget
import hestia
from eval_support import fixtures


def names(schemas):
    return {s['function']['name'] for s in schemas}


def prepare(text):
    async def run():
        await hestia._build_system_prompt(text)
        return hestia._request_schemas(text)
    return asyncio.run(run())


def test_mixed_lights_and_shopping_keep_both_tools():
    with fixtures():
        assert {'home', 'shopping'} <= names(prepare('Turn off kitchen lights and add milk to the shopping list'))


def test_followup_preserves_light_topic_and_adds_reminder():
    query = hestia._routing_text([
        {'role': 'user', 'content': 'Turn off kitchen lights'},
        {'role': 'assistant', 'content': 'Done'},
        {'role': 'user', 'content': 'Remind me tomorrow to turn them back on'}])
    with fixtures():
        assert {'home', 'reminder'} <= names(prepare(query))


def test_soil_observation_keeps_records_but_pure_readout_can_skip_tools():
    with fixtures():
        assert 'records' in names(prepare('The carrot bed is dry today'))
        assert prepare('How dry is the carrot bed?') == []
        assert 'records' in names(prepare('How dry is the carrot bed and log the reading'))


def test_unavailable_soil_does_not_force_grounded_answer(monkeypatch):
    with fixtures():
        async def unavailable(**kwargs):
            return '', '(home catalog unavailable: offline)'
        monkeypatch.setattr(hestia.tools.home, 'context_catalogs', unavailable)
        assert 'home' in names(prepare('How dry is the carrot bed?'))


def test_schema_selection_does_not_fetch_catalog(monkeypatch):
    monkeypatch.setattr(hestia.tools, 'soil_catalog', lambda: pytest.fail('sync network access'))
    with fixtures():
        prepare('How dry is the carrot bed?')


def test_history_pruning_preserves_current_tool_group():
    messages = [{'role': 'system', 'content': 'policy'},
                {'role': 'user', 'content': 'old' * 200},
                {'role': 'assistant', 'content': 'old answer'},
                {'role': 'user', 'content': 'current'},
                {'role': 'assistant', 'tool_calls': [{'function': {'name': 'home'}}]},
                {'role': 'tool', 'content': 'result'}]
    fitted = context_budget.fit(messages, [], 1100, 100)
    assert fitted == messages[:1] + messages[3:]


def test_oversize_current_request_rejected_and_truncation_visible():
    with pytest.raises(ValueError, match='exceed'):
        context_budget.fit([{'role': 'user', 'content': 'x' * 1000}], [], 1000, 200)
    block = json.loads(context_budget.evidence('page', 'x' * 100, 10))
    assert block['truncated'] is True
    assert block['trust'] == 'data_only'


@pytest.mark.parametrize('messages', [[], [{'role': 'tool', 'content': 'Done'}],
    [{'role': 'user', 'content': ['image']}],
    [{'role': 'user', 'content': 'x', 'tool_calls': []}],
    [{'role': 'assistant', 'content': 'x'}]])
def test_invalid_history_is_rejected(messages):
    assert context_budget.validate_messages(messages)
