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


def test_successful_mutation_invalidates_prompt_catalog(monkeypatch):
    from tools import home
    import httpx
    home._cache['t'] = 12345
    monkeypatch.setattr(home.httpx, 'post', lambda *a, **k: httpx.Response(200, json=[]))
    monkeypatch.setattr(home.httpx, 'get', lambda *a, **k: httpx.Response(200, json={'state': 'off'}))
    home.execute('turn_off', entity_id='light.kitchen')
    assert home._cache['t'] == 0


def test_receipt_uses_friendly_name_when_ha_supplies_one(monkeypatch):
    monkeypatch.setattr(home.httpx, "post", lambda *a, **k: _Resp())

    class State(_Resp):
        def json(self):
            return {"state": "off", "attributes": {"friendly_name": "Kitchen lights"}}
    monkeypatch.setattr(home.httpx, "get", lambda *a, **k: State())
    out = home.execute("turn_off", entity_id="light.light_kitchen_lights")
    assert out == "Done — Kitchen lights is now off."
    assert "light.light_kitchen_lights" not in out


# ----- actuation resolves before it sends (2026-10-29) --------------------------
# LFM3-1.7B wrote light.outside_lights for light.light_outside_lights on a quarter of held-out
# commands. HA accepted the call, changed nothing, and the tool replied "Done ... is now None".

LIGHTS = [
    {"entity_id": "light.light_outside_lights", "state": "off",
     "attributes": {"friendly_name": "Outside lights"}},
    {"entity_id": "light.light_kitchen_lights", "state": "on",
     "attributes": {"friendly_name": "Kitchen lights"}},
    {"entity_id": "light.porch_light", "state": "off",
     "attributes": {"friendly_name": "Porch light"}},
]


def test_resolve_a_tidied_id_to_the_real_one():
    assert home._resolve("light.outside_lights", LIGHTS) == "light.light_outside_lights"
    assert home._resolve("light.kitchen_lights", LIGHTS) == "light.light_kitchen_lights"
    assert home._resolve("light.porch_light", LIGHTS) == "light.porch_light"
    assert home._resolve("light.outside_lights".upper(), LIGHTS) == "light.light_outside_lights"


def _ha(monkeypatch, posted):
    def get(url, **_):
        if url.endswith("/api/states"):
            return httpx.Response(200, json=LIGHTS)
        eid = url.rsplit("/", 1)[1]
        hit = next((s for s in LIGHTS if s["entity_id"] == eid), None)
        return httpx.Response(200 if hit else 404, json=hit or {"message": "Entity not found."})
    monkeypatch.setattr(home.httpx, "get", get)
    monkeypatch.setattr(home.httpx, "post", lambda url, json=None, **_: posted.append(json) or _Resp())


def test_actuation_sends_the_resolved_id(monkeypatch):
    posted = []
    _ha(monkeypatch, posted)
    out = home.execute("turn_on", entity_id="light.outside_lights")
    assert posted == [{"entity_id": "light.light_outside_lights"}]
    assert out == "Done — Outside lights is now off."  # the stub doesn't flip state


def test_an_unknown_light_is_refused_and_nothing_is_sent(monkeypatch):
    posted = []
    _ha(monkeypatch, posted)
    out = home.execute("turn_off", entity_id="light.garage")
    assert posted == []
    assert "nothing was sent" in out


def test_a_missing_light_on_read_back_is_never_done(monkeypatch):
    monkeypatch.setattr(home.httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(home.httpx, "get", lambda url, **_: httpx.Response(
        404, json={"message": "Entity not found."}))
    out = home.execute("turn_on", entity_id="light.ghost")
    assert not out.startswith("Done")
    assert "no such light" in out
