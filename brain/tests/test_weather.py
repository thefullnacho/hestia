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
    assert weather.first_freeze([_row("2026-04-21", 40.0)]) is None
    frost = weather.first_freeze([_row("2026-04-21", weather.FROST_F)])
    assert frost is not None and frost["kind"] == "frost"
    freeze = weather.first_freeze([_row("2026-04-21", weather.FREEZE_F)])
    assert freeze is not None and freeze["kind"] == "freeze"


def test_first_freeze_returns_the_first_matching_day():
    ev = weather.first_freeze([_row("2026-04-21", 50.0), _row("2026-04-22", 31.0),
                               _row("2026-04-23", 20.0)])
    assert ev["date"] == "2026-04-22"


def test_rain_text_ignores_trace():
    trace = [_row("2026-04-21", 50.0, rain=weather.RAIN_MIN_IN - 0.01, pop=20)]
    assert "No meaningful rain" in weather._rain_text(trace)
    real = [_row("2026-04-21", 50.0, rain=0.4, pop=80)]
    assert "0.40 in" in weather._rain_text(real)
