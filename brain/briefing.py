"""Morning briefing — the house says good morning.

Runs daily via a systemd user timer. Code gathers the morning's facts
deterministically (weather, garden, due records, today's reminders, what the media
stack pulled overnight), then the resident model is asked only to *phrase* them as a
short spoken paragraph — judgment and voice, never arithmetic or recall. If the model
is down, the raw fact lines go out instead: the briefing must arrive either way.

Delivery is a phone push (same HA notify path as reminders/garden-watch) plus an
announcement on any Assist satellite that's online (the kitchen Voice PE). Unlike
garden-watch this always sends — an empty morning is itself news.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import httpx

import config  # puts brain/ on sys.path + owns paths

config.load_secrets()
import garden_watch                    # noqa: E402 — reuse soil_beds + build_alerts
import records_store                   # noqa: E402
import reminders_store                 # noqa: E402
from tools import weather              # noqa: E402

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
NOTIFY = os.environ.get("BRIEFING_NOTIFY",
                        os.environ.get("GARDEN_NOTIFY", "mobile_app_alexs_iphone"))
OLLAMA = os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("HESTIA_BRIEFING_MODEL") or os.environ.get("HESTIA_MODEL", "qwen3:14b")
ANNOUNCE = os.environ.get("HESTIA_BRIEFING_ANNOUNCE", "1") not in ("0", "", "false", "False")
TIMEOUT = float(os.environ.get("HESTIA_BRIEFING_TIMEOUT", "60"))
MEDIA_WINDOW_H = 24
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

NARRATE_PROMPT = """You are Hestia, the household's home assistant, giving the morning briefing. Below are today's facts, already verified. Turn them into ONE short spoken paragraph (3-6 plain sentences) that will be read aloud in the kitchen.

RULES:
- Use ONLY the facts given. Never invent, estimate, or add advice beyond them.
- Plain speech: no markdown, no emoji, no headers, no bullet points.
- Warm but brief — a person saying good morning, not a report.
- Skip nothing marked as an alert; compress or drop routine filler if it runs long.
- Numbers: round temperatures to whole degrees; say dates like "Tuesday".

FACTS:
"""


# ---------- fact gathering (deterministic; every section is guarded) ----------

def _weather_facts() -> list[str]:
    rows = weather.forecast_days(7)
    today = rows[0]
    facts = [f"Today: high {today['hi']:.0f}F, low {today['lo']:.0f}F, "
             f"rain {today['rain']:.2f} in ({today['pop']}% chance)."]
    ev = weather.first_freeze(rows)
    if ev:
        label = "hard freeze" if ev["kind"] == "freeze" else "frost"
        facts.append(f"ALERT: {label} {weather._nice_date(ev['date'])}, "
                     f"low {ev['lo']:.0f}F.")
    wet = [r for r in rows[1:] if r["rain"] >= weather.RAIN_MIN_IN]
    if wet:
        facts.append("Rain ahead: " + "; ".join(
            f"{weather._nice_date(r['date'])} {r['rain']:.2f} in" for r in wet[:3]) + ".")
    alerts = weather.active_alerts()
    if alerts is None:
        facts.append("ALERT: NWS alerts unavailable, alert status unknown. Check a weather app.")
    else:
        for a in alerts:
            facts.append(f"ALERT: NWS {a['event']} — {a['headline']}.")
    return facts


def _garden_facts() -> list[str]:
    alerts = garden_watch.build_alerts(persist=False)  # timer owns the streak state
    if alerts:
        return [f"ALERT (garden): {a}" for a in alerts]
    beds = garden_watch.soil_beds()
    if not beds:
        return []
    low = min(beds, key=lambda b: b[1])
    return [f"Garden: all {len(beds)} beds fine; driest is {low[0]} at {low[1]:.0f}%."]


def _records_facts() -> list[str]:
    out = []
    for a in records_store.due_assets():
        since = f"{a['days_since']} days ago" if a["days_since"] is not None else "never"
        out.append(f"Due: {a['name']} (every {a['interval_days']} days, last done {since}).")
    return out


def _reminder_facts(now: dt.datetime) -> list[str]:
    today = now.date().isoformat()
    out = []
    for r in reminders_store.pending():
        if r["due_at"][:10] != today:
            continue
        t = dt.datetime.fromisoformat(r["due_at"]).strftime("%-I:%M %p").lower()
        out.append(f"Reminder today at {t}: {r['text']}.")
    return out


def _media_facts(now: dt.datetime) -> list[str]:
    """Titles the *arr stack imported in the last MEDIA_WINDOW_H hours — i.e. new on Plex."""
    from tools import media
    since = (now - dt.timedelta(hours=MEDIA_WINDOW_H)).astimezone().isoformat()
    titles: list[str] = []
    for kind, api in (("tv", "v3"), ("movie", "v3")):
        app = media.APPS[kind]
        if not app["key"]:
            continue
        try:
            hist = httpx.get(f"{app['base']}/api/{api}/history/since",
                             headers={"X-Api-Key": app["key"]},
                             params={"date": since}, timeout=15).json()
        except Exception as e:  # noqa: BLE001 — one arr down shouldn't kill the briefing
            print(f"briefing: {kind} history failed: {e}", file=sys.stderr)
            continue
        for h in hist:
            if h.get("eventType") != "downloadFolderImported":
                continue
            if kind == "tv":
                ep = h.get("episode") or {}
                s, e_ = ep.get("seasonNumber"), ep.get("episodeNumber")
                t = (h.get("series") or {}).get("title", "?")
                titles.append(f"{t} S{s:02d}E{e_:02d}" if s is not None else t)
            else:
                titles.append((h.get("movie") or {}).get("title", "?"))
    titles = sorted(set(titles))
    if not titles:
        return []
    shown = ", ".join(titles[:5]) + (f" and {len(titles) - 5} more" if len(titles) > 5 else "")
    return [f"New on Plex overnight: {shown}."]


def build_facts(now: dt.datetime | None = None) -> list[str]:
    """All fact lines for this morning. Each section is independent — one backend being
    down costs its section, never the briefing."""
    now = now or dt.datetime.now()
    facts = [f"Date: {now.strftime('%A, %B ')}{now.day}."]
    for section in (_weather_facts,
                    _garden_facts,
                    _records_facts,
                    lambda: _reminder_facts(now),
                    lambda: _media_facts(now)):
        try:
            facts.extend(section())
        except Exception as e:  # noqa: BLE001
            print(f"briefing: section {getattr(section, '__name__', 'lambda')} failed: {e}",
                  file=sys.stderr)
    return facts


# ---------- narration (the model phrases; it never computes) ----------

def narrate(facts: list[str]) -> str:
    body = {"model": MODEL,
            "messages": [{"role": "user",
                          "content": NARRATE_PROMPT + "\n".join(f"- {f}" for f in facts)}],
            "stream": False, "think": False,
            "options": {"temperature": 0.3}}
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=TIMEOUT)
    r.raise_for_status()
    text = (r.json().get("message", {}).get("content", "") or "").strip()
    if not text:
        raise ValueError("empty narration")
    return text


def fallback_text(facts: list[str]) -> str:
    return "\n".join(f"• {f}" for f in facts)


# ---------- delivery ----------

def push(message: str) -> None:
    httpx.post(f"{HA_URL}/api/services/notify/{NOTIFY}", headers=_HDRS,
               json={"title": "☀️ Morning briefing", "message": message},
               timeout=15).raise_for_status()


def satellites() -> list[str]:
    """Assist satellites currently reachable (the kitchen Voice PE, and any added later)."""
    r = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=15)
    r.raise_for_status()
    return [s["entity_id"] for s in r.json()
            if s["entity_id"].startswith("assist_satellite.")
            and s["state"] not in ("unavailable", "unknown")]


def announce(message: str) -> list[str]:
    done = []
    for ent in satellites():
        try:
            httpx.post(f"{HA_URL}/api/services/assist_satellite/announce", headers=_HDRS,
                       json={"entity_id": ent, "message": message},
                       timeout=120).raise_for_status()  # HA blocks until playback ends
            done.append(ent)
        except Exception as e:  # noqa: BLE001
            print(f"briefing: announce on {ent} failed: {e}", file=sys.stderr)
    return done


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    raw = "--raw" in sys.argv
    do_announce = ANNOUNCE and "--no-announce" not in sys.argv
    do_push = "--no-push" not in sys.argv

    facts = build_facts()
    if raw:
        text = fallback_text(facts)
    else:
        try:
            text = narrate(facts)
        except Exception as e:  # noqa: BLE001 — the briefing arrives even with the brain down
            print(f"briefing: narration failed ({e}); sending raw facts", file=sys.stderr)
            text = fallback_text(facts)

    if dry_run:
        print("briefing (dry-run) facts:\n" + fallback_text(facts))
        print("\nbriefing (dry-run) would send:\n" + text)
        return 0
    if do_push:
        push(text)
    spoke = announce(text) if do_announce else []
    print(f"briefing: sent (push={do_push}, spoke on {spoke or 'none'}):\n{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
