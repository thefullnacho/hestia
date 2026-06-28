"""Note-taker — the offline logic: robust proposal parsing, dedup/novelty against what's
already known, and the propose-to-inbox vs autowrite paths. The model call is injected so
these run without Ollama."""
from __future__ import annotations

import note_taker


# ----- parse_proposals (pure) ----------------------------------------------

def test_parse_plain_json_array():
    raw = '[{"content": "User likes 1080p, not 4K", "type": "preference", "confidence": 0.9}]'
    props = note_taker.parse_proposals(raw)
    assert len(props) == 1
    assert props[0]["content"] == "User likes 1080p, not 4K"
    assert props[0]["type"] == "preference" and props[0]["confidence"] == 0.9


def test_parse_strips_code_fence_and_prose(inbox):
    raw = 'Sure! Here you go:\n```json\n[{"content": "Trash goes out Tuesday", "type": "routine"}]\n```'
    props = inbox.parse_proposals(raw)
    assert props and props[0]["content"] == "Trash goes out Tuesday"


def test_parse_garbage_returns_empty(inbox):
    assert inbox.parse_proposals("I couldn't find anything useful.") == []
    assert inbox.parse_proposals("") == []


def test_parse_bare_object_is_wrapped(inbox):
    # format=json makes the model emit a single object, not an array — must still parse
    raw = '{"content": "User prefers tea over coffee", "type": "preference", "confidence": 0.8}'
    props = inbox.parse_proposals(raw)
    assert len(props) == 1 and props[0]["content"] == "User prefers tea over coffee"


def test_parse_wrapper_object_uses_inner_list(inbox):
    raw = '{"facts": [{"content": "Trash goes out Tuesday", "type": "routine"}]}'
    props = inbox.parse_proposals(raw)
    assert len(props) == 1 and props[0]["content"] == "Trash goes out Tuesday"


def test_parse_coerces_type_clamps_conf_drops_empty_and_dedups(inbox):
    raw = """[
      {"content": "Likes porch light dim", "type": "not_real", "confidence": 5},
      {"content": "", "type": "preference"},
      {"content": "Likes porch light dim", "type": "preference", "confidence": 0.8}
    ]"""
    props = inbox.parse_proposals(raw)
    assert len(props) == 1                       # empty dropped, duplicate collapsed
    assert props[0]["type"] == "preference"      # unknown type coerced
    assert props[0]["confidence"] == 1.0         # clamped into [0,1]


def test_parse_caps_proposal_count(inbox):
    raw = "[" + ",".join(
        f'{{"content": "fact number {i}", "type": "reference"}}' for i in range(20)) + "]"
    assert len(inbox.parse_proposals(raw)) == inbox._MAX_PROPOSALS


# ----- novelty / dedup -----------------------------------------------------

def test_is_novel_false_when_memory_already_knows(mem, inbox):
    mem.write("The good coffee is the bag with the orange label", type="preference")
    assert inbox.is_novel("the good coffee is the orange label bag") is False
    assert inbox.is_novel("The dog's vet is Dr. Smith in town") is True


def test_is_novel_false_when_already_queued(mem, inbox):
    inbox._write_proposal({"content": "Trash goes out on Tuesday night",
                           "type": "routine", "confidence": 0.7})
    assert inbox.is_novel("trash goes out tuesday night") is False


# ----- run() orchestration -------------------------------------------------

_MSGS = [{"role": "user", "content": "Remember I always want movie downloads in 1080p, never 4K."},
         {"role": "assistant", "content": "Got it."}]


def test_run_writes_proposals_to_inbox(mem, inbox):
    fake = lambda _t: '[{"content": "User wants movies in 1080p, never 4K", "type": "preference", "confidence": 0.9}]'
    ids = inbox.run(_MSGS, "Got it.", extract_fn=fake)
    assert len(ids) == 1
    assert (inbox.INBOX_DIR / f"{ids[0]}.md").exists()
    # nothing leaked into live memory
    assert mem.recall("1080p") == []


def test_run_dedups_on_second_pass(mem, inbox):
    fake = lambda _t: '[{"content": "User wants movies in 1080p, never 4K", "type": "preference"}]'
    first = inbox.run(_MSGS, "Got it.", extract_fn=fake)
    second = inbox.run(_MSGS, "Got it.", extract_fn=fake)
    assert len(first) == 1 and second == []      # the queued proposal blocks a re-propose


def test_run_skips_trivial_turns(mem, inbox):
    called = []
    inbox.run([{"role": "user", "content": "hi"}], "Hello!",
              extract_fn=lambda t: called.append(t) or "[]")
    assert called == []                          # too short to bother extracting


def test_run_autowrite_goes_straight_to_memory(mem, inbox, monkeypatch):
    monkeypatch.setattr(inbox, "AUTOWRITE", True)
    fake = lambda _t: '[{"content": "User wants movies in 1080p, never 4K", "type": "preference"}]'
    ids = inbox.run(_MSGS, "Got it.", extract_fn=fake)
    assert len(ids) == 1
    assert mem.recall("1080p")                   # written to live memory, not the inbox
    assert not list(inbox.INBOX_DIR.glob("*.md")) if inbox.INBOX_DIR.exists() else True


def test_run_never_raises_on_extract_failure(mem, inbox):
    def boom(_t):
        raise RuntimeError("ollama down")
    assert inbox.run(_MSGS, "Got it.", extract_fn=boom) == []
