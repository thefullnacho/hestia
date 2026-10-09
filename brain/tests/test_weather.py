"""`weather` tool — the parts that must not lie.

An NWS outage has to read as "unknown", never as an all-clear: this is the one
safety-relevant readout the tool has, and a gardener deciding whether to cover tender
crops cannot tell a silent failure from a real quiet night. The threshold and rain
helpers are pure, so they are pinned here too."""
from __future__ import annotations

import httpx
import pytest

from tools import weather


def _row(date: str, lo: float, hi: float = 70.0, rain: float = 0.0, pop: int = 0) -> dict:
    return {"date": date, "lo": lo, "hi": hi, "rain": rain, "pop": pop}


@pytest.fixture
def nws_down(monkeypatch):
    """Every outbound weather.gov call raises, as in a real outage."""
    def boom(*a, **k):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(weather.httpx, "get", boom)


def test_alerts_outage_is_reported_not_silent(nws_down):
    out = weather.execute("alerts")
    assert "No active" not in out
    assert "unknown" in out.lower()


def test_alerts_outage_returns_none_not_empty(nws_down):
    """The sentinel is the whole fix: [] means 'checked, nothing', None means 'no answer'."""
    assert weather.active_alerts() is None


def test_briefing_still_answers_when_nws_down(nws_down, monkeypatch):
    rows = [_row("2026-04-21", 30.0), _row("2026-04-22", 55.0, rain=0.5, pop=80)]
    monkeypatch.setattr(weather, "forecast_days", lambda days=7: rows)
    out = weather.execute("briefing")
    assert "Hard freeze" in out          # the forecast half still works
    assert "0.50 in" in out
    assert "unknown" in out.lower()      # and the alert half admits it doesn't know


def test_points_url_is_rounded_and_follows_redirects(monkeypatch):
    """NWS 301s coordinates longer than 4dp, and an unfollowed 301 raises out of
    raise_for_status — which is exactly how the alert layer sat dead reporting all-clear."""
    seen: list[tuple[str, dict]] = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"properties": {"forecastZone": "https://api.weather.gov/zones/forecast/CTZ012"},
                    "features": []}

    def fake_get(url, **kw):
        seen.append((url, kw))
        return _Resp()

    monkeypatch.setattr(weather.httpx, "get", fake_get)
    assert weather.active_alerts() == []
    points_url, points_kw = seen[0]
    assert f"/points/{weather.LAT:.4f},{weather.LON:.4f}" in points_url
    assert len(points_url.split("/points/")[1].split(",")[0].split(".")[1]) == 4
    assert points_kw["follow_redirects"] is True
    assert all(kw["follow_redirects"] is True for _, kw in seen)


def test_first_freeze_thresholds():
    band = weather.FROST_F + weather.FROST_MARGIN_F
    today = _row("2026-04-20", 60.0)
    assert weather.first_freeze([today, _row("2026-04-21", band + 1)]) is None
    near = weather.first_freeze([today, _row("2026-04-21", band)])
    assert near is not None and near["kind"] == "near"
    frost = weather.first_freeze([_row("2026-04-21", weather.FROST_F)])
    assert frost is not None and frost["kind"] == "frost"
    freeze = weather.first_freeze([_row("2026-04-21", weather.FREEZE_F)])
    assert freeze is not None and freeze["kind"] == "freeze"


def test_first_freeze_returns_the_first_matching_day():
    ev = weather.first_freeze([_row("2026-04-21", 50.0), _row("2026-04-22", 31.0),
                               _row("2026-04-23", 20.0)])
    assert ev["date"] == "2026-04-22"


def test_near_frost_is_only_said_for_the_next_two_nights():
    """A low just above the line five days out moves too much to act on. A real frost there
    still counts, because that one is worth planning for."""
    near = weather.FROST_F + 2
    mild = [_row(f"2026-10-{d:02d}", 55.0) for d in range(9, 10 + weather.NEAR_FROST_NIGHTS)]
    assert weather.first_freeze(mild[:-1] + [_row("2026-10-11", near)])["kind"] == "near"
    assert weather.first_freeze(mild + [_row("2026-10-12", near)]) is None
    assert weather.first_freeze(mild + [_row("2026-10-12", 30.0)])["kind"] == "freeze"


def test_the_dawn_that_already_happened_is_not_news():
    """At 7am today's low is the night just ended. Only a real frost there is still said,
    as it always was."""
    assert weather.first_freeze([_row("2026-10-07", 37.0), _row("2026-10-08", 55.0)]) is None


def test_near_frost_before_a_real_frost_names_the_first_night():
    """Covers have to go on the first night at risk, not the first certain one."""
    ev = weather.first_freeze([_row("2026-10-09", 50.0), _row("2026-10-10", 40.0),
                               _row("2026-10-11", 34.0)])
    assert ev["date"] == "2026-10-10" and ev["kind"] == "near"


def test_the_october_7_miss_is_caught_the_morning_before():
    """The night this exists for. The forecast the morning before said 41.3°F, the nearest
    station read 35.6°F, and a bare 36°F line said nothing."""
    ev = weather.first_freeze([_row("2026-10-06", 45.0), _row("2026-10-07", 41.3)])
    assert ev is not None and ev["date"] == "2026-10-07"
    assert "Possible frost" in weather._frost_text([_row("2026-10-06", 45.0),
                                                    _row("2026-10-07", 41.3)])


def test_a_close_night_past_the_window_is_named_not_waved_off():
    """'Will it frost this week' must not get a flat no when Thursday sits 1° off the band."""
    rows = [_row(f"2026-10-{d:02d}", 55.0) for d in range(9, 15)] + [_row("2026-10-15", 41.6)]
    text = weather._frost_text(rows)
    assert "No frost or freeze" in text
    assert "Thu Oct 15 at 42°F is within" in text and "close enough to watch" in text


def test_rain_text_ignores_trace():
    trace = [_row("2026-04-21", 50.0, rain=weather.RAIN_MIN_IN - 0.01, pop=20)]
    assert "No meaningful rain" in weather._rain_text(trace)
    real = [_row("2026-04-21", 50.0, rain=0.4, pop=80)]
    assert "0.40 in" in weather._rain_text(real)
