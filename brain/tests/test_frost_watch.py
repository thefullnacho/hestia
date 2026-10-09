"""Frost watch — the morning push for a cold night.

On 2026-10-07 the yard reached 36°F with no alert. The timer ran and pushed that morning;
the forecast the morning before had said 41°F, and a frost line compared straight against a
grid forecast never fired. So the alert now allows a margin for the yard running colder.
"""
from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

import garden_watch
from tools import weather


@pytest.fixture
def watch(tmp_path, monkeypatch):
    monkeypatch.setattr(garden_watch, "STATE_PATH", str(tmp_path / "garden_watch.json"))
    return garden_watch


def forecast(start, lows):
    d0 = dt.date.fromisoformat(start)
    return [{"date": (d0 + dt.timedelta(days=i)).isoformat(), "lo": lo, "hi": lo + 20,
             "rain": 0.0, "pop": 0} for i, lo in enumerate(lows)]


@pytest.fixture
def quiet_garden(watch, monkeypatch):
    """Everything but the forecast says nothing, so only the frost line can speak."""
    monkeypatch.setattr(watch, "soil_beds", list)
    monkeypatch.setattr(watch, "stale_sensors", list)
    monkeypatch.setitem(sys.modules, "pest_watch",
                        types.SimpleNamespace(build_alerts=lambda persist: []))
    return watch


def test_the_morning_before_october_7_now_pushes(quiet_garden, monkeypatch):
    monkeypatch.setattr(weather, "forecast_days", lambda days=7: forecast(
        "2026-10-06", [45, 41.3, 55, 53, 52, 54, 58]))
    alerts = quiet_garden.build_alerts(persist=False)
    assert len(alerts) == 1
    assert alerts[0].startswith("Frost possible Wed Oct 7: forecast low 41°F")
    assert "Protect tender crops" in alerts[0]


def test_a_real_frost_keeps_its_old_line(quiet_garden, monkeypatch):
    monkeypatch.setattr(weather, "forecast_days", lambda days=7: forecast(
        "2026-10-20", [50, 50, 50, 50, 34, 50, 50]))
    assert quiet_garden.build_alerts(persist=False) == [
        "Frost coming Sat Oct 24: low 34°F — protect tender crops."]


def test_a_mild_week_stays_quiet(quiet_garden, monkeypatch):
    band = weather.FROST_F + weather.FROST_MARGIN_F
    monkeypatch.setattr(weather, "forecast_days", lambda days=7: forecast(
        "2026-10-20", [band + 1] * 7))
    assert quiet_garden.build_alerts(persist=False) == []
