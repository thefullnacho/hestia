"""Frost watch — the morning push for a cold night, and the log that keeps its margin honest.

On 2026-10-07 the yard reached 36°F with no alert. The timer ran and pushed that morning;
the forecast the morning before had said 41°F, and a frost line compared straight against a
grid forecast never fired. So the alert now allows a margin for the yard running colder, and
each morning run writes the forecast beside what the nearest station read, so the margin is
measured rather than guessed. The cases below pin both halves.
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


def test_log_keeps_tomorrows_forecast_and_fills_last_night(watch, monkeypatch):
    asked = []
    monkeypatch.setattr(weather, "station_low", lambda d: asked.append(d) or 35.6)
    watch.log_lows(forecast("2026-10-06", [45, 41.3, 55]), dt.date(2026, 10, 6))
    assert asked == []  # nothing has finished yet, so nothing to ask the station
    log = watch.log_lows(forecast("2026-10-08", [55, 50]), dt.date(2026, 10, 8))
    assert log["2026-10-07"] == {"forecast": 41.3, "observed": 35.6}
    assert log["2026-10-09"] == {"forecast": 50}
    assert asked == [dt.date(2026, 10, 7)]


def test_a_station_gap_is_retried_then_left_a_gap(watch, monkeypatch):
    monkeypatch.setattr(weather, "station_low", lambda d: None)
    watch.log_lows(forecast("2026-10-06", [45, 41.3]), dt.date(2026, 10, 6))
    watch.log_lows(forecast("2026-10-08", [55, 50]), dt.date(2026, 10, 8))
    assert "observed" not in watch._load_state()["_lows"]["2026-10-07"]

    asked = []
    monkeypatch.setattr(weather, "station_low", lambda d: asked.append(d) or 30.0)
    late = dt.date(2026, 10, 7) + dt.timedelta(days=watch.LOWS_FILL_DAYS + 1)
    watch.log_lows(forecast(late.isoformat(), [55, 50]), late)
    assert dt.date(2026, 10, 7) not in asked  # older than NWS keeps; never guessed


def test_dry_run_does_not_touch_the_log(quiet_garden, monkeypatch):
    monkeypatch.setattr(weather, "forecast_days", lambda days=7: forecast(
        "2026-10-06", [45, 41.3]))
    monkeypatch.setattr(weather, "station_low", lambda d: pytest.fail("dry run asked NWS"))
    quiet_garden.build_alerts(persist=False)
    assert "_lows" not in quiet_garden._load_state()


def test_a_log_failure_still_pushes_the_frost(quiet_garden, monkeypatch):
    monkeypatch.setattr(weather, "forecast_days", lambda days=7: forecast(
        "2026-10-06", [45, 41.3]))

    def boom(rows, today):
        raise OSError("disk full")

    monkeypatch.setattr(quiet_garden, "log_lows", boom)
    assert quiet_garden.build_alerts(persist=True)[0].startswith("Frost possible")


def test_report_names_the_worst_cold_night_miss(watch, monkeypatch):
    watch._save_state({"_lows": {
        "2026-10-05": {"forecast": 52.0, "observed": 50.0},   # warm night: not the margin's job
        "2026-10-07": {"forecast": 41.3, "observed": 35.6},
        "2026-10-09": {"forecast": 47.9}}})
    out = watch.lows_report()
    assert "2026-10-07      41.3      35.6       5.7" in out
    assert "Worst day-before miss" in out and "5.7° (1 night)" in out


def test_report_before_any_cold_night_says_the_margin_is_still_an_estimate(watch):
    assert "still the 2026-10-07 estimate" in watch.lows_report()
