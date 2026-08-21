"""Shared scheduled-workflow collectors and delivery routing."""
from __future__ import annotations

import datetime as dt

import briefing
import daily_facts


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return self

    def json(self):
        return self.body


def test_media_arrivals_normalizes_and_deduplicates(monkeypatch):
    monkeypatch.setitem(daily_facts.media.APPS["tv"], "key", "tv-key")
    monkeypatch.setitem(daily_facts.media.APPS["movie"], "key", "movie-key")

    def get(url, **_):
        if url.startswith(daily_facts.media.APPS["tv"]["base"]):
            return _Response([{
                "eventType": "downloadFolderImported",
                "series": {"title": "Foundation"},
                "episode": {"seasonNumber": 2, "episodeNumber": 3},
            }])
        return _Response([{
            "eventType": "downloadFolderImported",
            "movie": {"title": "Sinners"},
        }, {
            "eventType": "downloadFolderImported",
            "movie": {"title": "Sinners"},
        }])

    monkeypatch.setattr(daily_facts.httpx, "get", get)
    assert daily_facts.media_arrivals(dt.datetime(2026, 8, 21, 8, 0)) == [
        "Foundation S02E03", "Sinners",
    ]


def test_briefing_delegates_announcements_to_shared_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(briefing, "build_facts", lambda: ["Date: Friday, August 21."])
    monkeypatch.setattr(briefing, "narrate", lambda _: "Good morning.")
    monkeypatch.setattr(briefing, "push", lambda _: calls.append("push"))
    monkeypatch.setattr(briefing.ha_announce, "announce", lambda text: calls.append(text) or ["assist_satellite.kitchen"])
    monkeypatch.setattr(briefing.sys, "argv", ["briefing.py"])
    assert briefing.main() == 0
    assert calls == ["push", "Good morning."]
