"""Pest-watch spine tests (PEST_WATCH.md checklist): GDD accumulation + back-fill,
idempotent daily advance, once-per-season dedupe, and the quiet first run mid-season.
Weather is stubbed; no network."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import pest_watch
import records_store
from tools import weather

TODAY = dt.date(2026, 7, 1)

# Two synthetic archive days: mean 70 -> 20 GDD, mean 60 -> 10 GDD (base 50).
ROWS = [
    {"date": "2026-06-29", "hi": 80, "lo": 60, "rain": 0.0},
    {"date": "2026-06-30", "hi": 70, "lo": 50, "rain": 0.0},
]

TABLE = [{
    "cropId": "tomato",
    "pests": [
        {"name": "hornworm", "soilTempThreshold": 60, "gddThreshold": 25,
         "companions": [{"companion": "Basil", "placement": "interplant",
                         "reason": "masks scent", "evidenceLevel": "moderate"}]},
        {"name": "nematode", "soilTempThreshold": 95, "companions": []},  # never opens here
    ],
}]


@pytest.fixture
def pw(tmp_path, monkeypatch):
    monkeypatch.setattr(pest_watch, "STATE_PATH", tmp_path / "pest_state.json")
    data = tmp_path / "pest-companions.json"
    data.write_text(json.dumps(TABLE))
    monkeypatch.setattr(pest_watch, "DATA_PATH", data)
    monkeypatch.setattr(pest_watch, "BIOFIX_OVERRIDE", "2026-06-29")
    monkeypatch.setattr(weather, "history_days", lambda s, e: [r for r in ROWS if s <= r["date"] <= e])
    monkeypatch.setattr(records_store, "DB_PATH", tmp_path / "hestia.db")  # pest_alert log isolation
    return pest_watch


def test_gdd_backfill_and_soil_estimate(pw):
    state = pw.advance({}, TODAY)
    assert state["biofix"] == "2026-06-29"
    assert state["cumulative_gdd"] == pytest.approx(30.0)   # 20 + 10
    assert state["last_gdd_date"] == "2026-06-30"
    assert pw.est_soil_temp(state) == pytest.approx(65.0)   # mean of 70, 60


def test_daily_advance_is_idempotent(pw):
    state = pw.advance({}, TODAY)
    again = pw.advance(state, TODAY)  # same day, nothing new to accumulate
    assert again["cumulative_gdd"] == pytest.approx(30.0)


def test_first_run_marks_quietly_then_dedupes(pw, capsys):
    # First real run mid-season: hornworm already in window -> marked, NOT alerted.
    assert pw.build_alerts(persist=True) == []
    assert "already in window" in capsys.readouterr().err
    # Next day: still in window but consumed for the season -> silence, not a re-alert.
    assert pw.build_alerts(persist=True, today=TODAY + dt.timedelta(days=1)) == []
    state = json.loads(pw.STATE_PATH.read_text())
    assert state["alerted"] == {"tomato:hornworm": "2026"}
    assert "tomato:nematode" not in state["alerted"]        # 95°F gate never met


def test_window_opening_after_first_run_alerts_once(pw):
    # Prime a season where nothing is in window yet (soil estimate too cold).
    cold = {"season": "2026", "biofix": "2026-06-29", "cumulative_gdd": 30.0,
            "last_gdd_date": "2026-06-30", "recent_mean_temps": [50.0], "alerted": {}}
    pw._save_state(cold)
    assert pw.build_alerts(persist=True) == []              # cold soil: no alert, no consume
    # Warmth arrives (fresh archive rows lift the trailing mean) -> exactly one alert.
    warm_state = json.loads(pw.STATE_PATH.read_text())
    warm_state["recent_mean_temps"] = [70.0]
    pw._save_state(warm_state)
    alerts = pw.build_alerts(persist=True)
    assert len(alerts) == 1 and "hornworm" in alerts[0] and "Basil" in alerts[0]
    assert pw.build_alerts(persist=True) == []              # once per season


def test_dry_run_consumes_nothing(pw):
    assert pw.build_alerts(persist=False) == []             # first-run marking, unpersisted
    assert not pw.STATE_PATH.exists()


def test_alertable_false_never_opens_a_window(pw, tmp_path, monkeypatch):
    """Aphids and nematodes have no emergence event, so no gate predicts them.

    Before the table carried alertable:false these fell through to the
    soil-temp fallback and sat open from spring onward. In 2026 that produced
    seven aphid windows across the season while the lot saw no aphids at all.
    """
    table = [{
        "cropId": "cabbage",
        "pests": [
            # Continuous pest: warm soil, no GDD gate. Must stay shut anyway.
            {"name": "aphid", "soilTempThreshold": 55, "alertable": False,
             "notAlertableReason": "continuous, multi-generational, no emergence event",
             "companions": []},
            # Same shape without the flag: still uses the soil-temp fallback.
            {"name": "cabbage-worm", "soilTempThreshold": 55, "companions": []},
        ],
    }]
    data = tmp_path / "table2.json"
    data.write_text(json.dumps(table))
    monkeypatch.setattr(pest_watch, "DATA_PATH", data)

    warm = {"season": "2026", "biofix": "2026-06-29", "cumulative_gdd": 900.0,
            "last_gdd_date": "2026-06-30", "recent_mean_temps": [75.0], "alerted": {}}
    pw._save_state(warm)

    alerts = pw.build_alerts(persist=True)
    joined = " ".join(alerts)
    assert "aphid" not in joined, "alertable:false pest opened a window"
    assert "cabbage-worm" in joined, "soil-temp fallback should still work for others"
