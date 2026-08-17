"""Home tool — the confirm-read after a successful command is a nicety, not the action.
A flaky read-back must report "sent, couldn't confirm", never "Error talking to Home
Assistant": the command already landed, and an error reading it invites the model to
retry, toggling a light the user wanted on. Also pins _resolve, the name-to-entity
matcher the whole moisture path depends on (it had zero coverage). HA is stubbed."""
from __future__ import annotations

import httpx

import tools.home as home


class _Resp:
    status_code = 200

    def json(self):
        return {}


def test_flaky_confirm_read_still_reports_success(monkeypatch):
    monkeypatch.setattr(home.httpx, "post", lambda *a, **k: _Resp())

    def boom(*a, **k):
        raise httpx.ConnectError("read-back flaked")
    monkeypatch.setattr(home.httpx, "get", boom)

    out = home.execute("turn_on", entity_id="light.tv")
    assert out.startswith("Sent turn_on to light.tv")
    assert "couldn't confirm" in out
    assert "Error talking" not in out


def test_confirmed_state_reports_done(monkeypatch):
    monkeypatch.setattr(home.httpx, "post", lambda *a, **k: _Resp())

    class State(_Resp):
        def json(self):
            return {"state": "on"}
    monkeypatch.setattr(home.httpx, "get", lambda *a, **k: State())
    assert home.execute("turn_on", entity_id="light.tv") == "Done — light.tv is now on."


# ----- _resolve (loose name -> exact entity_id) --------------------------------

STATES = [
    {"entity_id": "light.tv", "state": "off",
     "attributes": {"friendly_name": "TV"}},
    {"entity_id": "sensor.carrots_round_bed_soilmoisture", "state": "42",
     "attributes": {"friendly_name": "Carrots Round Bed Soil Moisture"}},
    {"entity_id": "sensor.beets_round_bed_soilmoisture", "state": "55",
     "attributes": {"friendly_name": "Beets Round Bed Soil Moisture"}},
]


def test_resolve_exact_id_wins():
    assert home._resolve("light.tv", STATES) == "light.tv"


def test_resolve_unique_friendly_name():
    assert home._resolve("carrots", STATES) == "sensor.carrots_round_bed_soilmoisture"


def test_resolve_ambiguous_returns_none():
    assert home._resolve("soil", STATES) is None
    assert home._resolve("bed", STATES) is None
