import asyncio

import pytest

import briefing
import briefing_store as store
import ha_announce
import hestia
from eval_support import fixtures


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, 'DATA_DIR', tmp_path)
    return tmp_path


def test_archive_preserves_snapshot_and_updates_receipt(archive):
    identifier = store.create(['Due: oil the test hinge.'], 'Oil the hinge.', 'test-model', 'model', {'push': {'status': 'pending'}})
    store.update_delivery(identifier, {'push': {'status': 'accepted'}})
    entry = store.recent()[0]
    assert entry['facts'] == ['Due: oil the test hinge.']
    assert entry['text'] == 'Oil the hinge.'
    assert entry['delivery']['push']['status'] == 'accepted'
    assert (archive/'briefings.db').stat().st_mode & 0o777 == 0o600


def test_missing_read_is_non_mutating_and_date_specific(archive):
    assert 'No saved briefing' in store.context('What was yesterday\'s briefing?')
    assert not (archive/'briefings.db').exists()
    store.create(['Today only'], 'Today only', 'test', 'model', {})
    assert 'Today only' not in store.context("Yesterday's briefing")
    assert 'Today only' in store.context("Yesterday's briefing\nFollow-up: What about today?")
    assert store.context('Tell me a joke') == ''


def test_briefing_context_reaches_production_loop_without_live_calls(monkeypatch):
    seen = []
    async def chat(messages, schemas):
        seen.append(messages[0]['content'])
        return {'content': 'The saved briefing mentioned oiling the hinge.'}
    monkeypatch.setattr(hestia, '_ollama_chat', chat)
    with fixtures():
        store.create(['Due: oil the hinge.'], 'Please oil the hinge.', 'test', 'model', {})
        asyncio.run(hestia.run_agent([{'role':'user','content':'What maintenance item did you mention this morning?'}]))
        assert 'Please oil the hinge.' in seen[0]
        assert 'historical snapshot, not current state' in seen[0]
        assert 'data_only' in seen[0]


def test_briefing_followup_preserves_topic():
    text = hestia._routing_text([{'role':'user','content':"Today's briefing?"},
        {'role':'assistant','content':'A hinge needed oiling.'},
        {'role':'user','content':'What about yesterday?'}])
    assert store.requested(text)
    assert 'yesterday' in text


def setup_delivery(monkeypatch):
    monkeypatch.setattr(briefing, 'build_facts', lambda: ['Due: test hinge.'])
    monkeypatch.setattr(briefing, 'narrate', lambda _: 'Oil the test hinge.')
    monkeypatch.setattr(briefing, 'ANNOUNCE', True)
    monkeypatch.setattr(briefing.sys, 'argv', ['briefing.py'])


def test_push_failure_does_not_suppress_voice_and_records_unknown(archive, monkeypatch):
    setup_delivery(monkeypatch)
    def fail(_):
        raise TimeoutError()
    calls = []
    monkeypatch.setattr(briefing, 'push', fail)
    monkeypatch.setattr(ha_announce, 'announce_report', lambda text: calls.append(text) or {'discovery':'ok','satellites':[]})
    assert briefing.main() == 1
    assert calls == ['Oil the test hinge.']
    row = store.recent()[0]
    assert row['delivery']['push']['status'] == 'unknown'
    assert row['delivery']['voice']['satellites'] == []


def test_dry_run_does_not_archive_or_deliver(archive, monkeypatch):
    setup_delivery(monkeypatch)
    monkeypatch.setattr(briefing.sys, 'argv', ['briefing.py','--dry-run'])
    monkeypatch.setattr(briefing, 'push', lambda _: pytest.fail('push in dry run'))
    monkeypatch.setattr(ha_announce, 'announce_report', lambda _: pytest.fail('voice in dry run'))
    assert briefing.main() == 0
    assert store.recent() == []


def test_fallback_and_disabled_channels_are_saved(archive, monkeypatch):
    setup_delivery(monkeypatch)
    def fail(_):
        raise ValueError()
    monkeypatch.setattr(briefing, 'narrate', fail)
    monkeypatch.setattr(briefing.sys, 'argv', ['briefing.py','--no-push','--no-announce'])
    assert briefing.main() == 0
    row = store.recent()[0]
    assert row['narration'] == 'fallback'
    assert row['model'] is None
    assert 'test hinge' in row['text']
    assert row['delivery']['voice']['status'] == 'disabled'


def test_archive_failure_does_not_block_delivery(archive, monkeypatch):
    setup_delivery(monkeypatch)
    def fail(*args, **kwargs):
        raise OSError()
    monkeypatch.setattr(store, 'create', fail)
    calls = []
    monkeypatch.setattr(briefing, 'push', lambda _: calls.append('push'))
    monkeypatch.setattr(ha_announce, 'announce_report', lambda _: calls.append('voice') or {'discovery':'ok','satellites':[]})
    assert briefing.main() == 1
    assert calls == ['push','voice']


def test_announcement_partial_acceptance(monkeypatch):
    monkeypatch.setattr(ha_announce, 'satellites', lambda: ['assist_satellite.a','assist_satellite.b'])
    class Response:
        def raise_for_status(self): return self
    def post(url, **kwargs):
        if kwargs['json']['entity_id'].endswith('.b'): raise TimeoutError()
        return Response()
    monkeypatch.setattr(ha_announce.httpx, 'post', post)
    report = ha_announce.announce_report('Synthetic test')
    assert [s['status'] for s in report['satellites']] == ['accepted','unknown']
    assert ha_announce.announce('Synthetic test') == ['assist_satellite.a']


def test_narration_uses_bounded_resident_options(monkeypatch):
    bodies = []
    class Response:
        def raise_for_status(self): return self
        def json(self): return {'message': {'content':'Hello'}}
    monkeypatch.setattr(briefing.httpx, 'post', lambda url, **kwargs: bodies.append(kwargs['json']) or Response())
    monkeypatch.setenv('HESTIA_NUM_CTX','32768')
    assert briefing.narrate(['Synthetic fact']) == 'Hello'
    assert bodies[0]['options']['num_ctx'] == 32768
    assert bodies[0]['options']['num_predict'] == 768


def test_shared_resident_dropin_is_one_configuration():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    brain = root/'deploy/systemd/hestia-brain.service.d/90-resident-trial.conf'
    scheduled = root/'deploy/systemd/hestia-briefing.service.d/90-resident-trial.conf'
    assert scheduled.is_symlink()
    assert scheduled.resolve() == brain.resolve()


def test_explicit_date_can_retrieve_older_than_recent_window(archive):
    from contextlib import closing
    import json
    identifier = store.create(['Historical test hinge'], 'Old narration', 'test', 'model', {})
    with closing(store._connect()) as conn, conn:
        entry = json.loads(conn.execute('SELECT payload FROM briefings WHERE id=?', (identifier,)).fetchone()[0])
        entry['created_at'] = '2025-01-02T07:10:00-05:00'
        conn.execute('UPDATE briefings SET created_at=?,payload=? WHERE id=?',
                     (entry['created_at'],json.dumps(entry),identifier))
    for i in range(31):
        store.create([f'Recent {i}'], 'Recent', 'test', 'model', {})
    assert 'Historical test hinge' in store.context('Briefing for 2025-01-02?')
    assert 'Historical test hinge' not in store.context('Latest briefing?')
