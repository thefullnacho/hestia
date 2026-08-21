"""Shared, read-only fact collectors for scheduled household summaries.

Briefing and journal deliberately apply different policy and wording to the facts they
collect. This module owns only sources that are identical in both workflows, so callers
do not depend on one timer script's private helper.
"""
from __future__ import annotations

import datetime as dt
import sys

import httpx

from tools import media


def media_arrivals(now: dt.datetime, window_hours: int = 24) -> list[str]:
    """Titles imported by Sonarr/Radarr within the trailing window, de-duplicated.

    A single unavailable *arr service only loses its own titles. This is read-only and
    intentionally returns raw titles; briefing and journal choose their own prose.
    """
    since = (now - dt.timedelta(hours=window_hours)).astimezone().isoformat()
    titles: list[str] = []
    for kind, api in (("tv", "v3"), ("movie", "v3")):
        app = media.APPS[kind]
        if not app["key"]:
            continue
        try:
            history = httpx.get(f"{app['base']}/api/{api}/history/since",
                                headers={"X-Api-Key": app["key"]},
                                params={"date": since}, timeout=15).raise_for_status().json()
        except Exception as e:  # noqa: BLE001 — one arr down must not lose the whole summary
            print(f"daily-facts: {kind} history failed: {e}", file=sys.stderr)
            continue
        for item in history:
            if item.get("eventType") != "downloadFolderImported":
                continue
            if kind == "tv":
                episode = item.get("episode") or {}
                season, number = episode.get("seasonNumber"), episode.get("episodeNumber")
                title = (item.get("series") or {}).get("title", "?")
                titles.append(f"{title} S{season:02d}E{number:02d}" if season is not None else title)
            else:
                titles.append((item.get("movie") or {}).get("title", "?"))
    return sorted(set(titles))
