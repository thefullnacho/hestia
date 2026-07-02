"""Nightly house journal — the house writes three sentences about its day.

The note-taker inverted: instead of extracting facts from conversations, a nightly timer
gathers what deterministically HAPPENED today (records events, fired reminders, weather,
soil, season GDD, media arrivals) and asks the resident model — idle at this hour — to
phrase it as a short diary entry. The entry is stored, never pushed: it's written for the
almanac and for reading back later, not for interrupting anyone at midnight.

Canonical copy = a `journal` event in records (rides the nightly DB backup, queryable by
subject/almanac). A per-day markdown file under data/journal/ is kept as the human-browsable
view of the same text. Model down => the raw fact lines are journaled instead — a thin day
beats a missing one.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import httpx

import config

config.load_secrets()
import pest_watch                      # noqa: E402 — season GDD for the daily stamp
import records_store                   # noqa: E402
import garden_watch                    # noqa: E402
from tools import weather              # noqa: E402

OLLAMA = os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("HESTIA_JOURNAL_MODEL") or os.environ.get("HESTIA_MODEL", "qwen3:14b")
TIMEOUT = float(os.environ.get("HESTIA_JOURNAL_TIMEOUT", "60"))
JOURNAL_DIR = config.DATA_DIR / "journal"

NARRATE_PROMPT = """You are Hestia, keeping the household's daily journal. Below is everything that verifiably happened today. Write ONE diary entry of 2-5 plain sentences, past tense, warm but factual — a homestead logbook, not a blog post.

RULES:
- Use ONLY the facts given; never invent, pad, or editorialize beyond them.
- Prefer the living things (garden, animals, harvests, sightings) over infrastructure.
- No markdown, no emoji, no headers, no date line (the entry is filed under its date).
- If the day was thin, say so in one honest sentence rather than inflating it.

TODAY'S FACTS:
"""


def _events_today(day: dt.date) -> list[str]:
    out = []
    for e in reversed(records_store.recent_events(since=day.isoformat(), limit=100)):
        if e["action"] == "journal":
            continue  # yesterday's entry written after midnight must not feed today's
        bits = [b for b in (e["subject"], e["action"], e["detail"]) if b]
        out.append(f"{e['kind']}: " + " — ".join(bits))
    return out


def _reminders_fired_today(day: dt.date) -> list[str]:
    with records_store._conn() as c:  # shared single-connection authority (see reminders_store)
        rows = c.execute("SELECT text, fired_at FROM reminders WHERE fired_at LIKE ?",
                         (f"{day.isoformat()}%",)).fetchall()
    return [f"reminder fired: {r['text']}" for r in rows]


def _weather_today() -> list[str]:
    t = weather.forecast_days(1)[0]
    return [f"weather: high {t['hi']:.0f}F, low {t['lo']:.0f}F, rain {t['rain']:.2f} in"]


def _garden_today() -> list[str]:
    out = []
    beds = garden_watch.soil_beds()
    if beds:
        low = min(beds, key=lambda b: b[1])
        hi = max(beds, key=lambda b: b[1])
        out.append(f"soil: driest bed {low[0]} {low[1]:.0f}%, wettest {hi[0]} {hi[1]:.0f}%")
    state = pest_watch._load_state()
    if state.get("cumulative_gdd"):
        out.append(f"season: {state['cumulative_gdd']:.0f} GDD since biofix {state['biofix']}")
    return out


def _media_today(now: dt.datetime) -> list[str]:
    import briefing
    return [f.replace("New on Plex overnight", "new on Plex") for f in briefing._media_facts(now)]


def build_facts(now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now()
    facts: list[str] = []
    for section in (lambda: _events_today(now.date()),
                    lambda: _reminders_fired_today(now.date()),
                    _weather_today,
                    _garden_today,
                    lambda: _media_today(now)):
        try:
            facts.extend(section())
        except Exception as e:  # noqa: BLE001 — a thin journal beats no journal
            print(f"journal: section failed: {e}", file=sys.stderr)
    return facts


def narrate(facts: list[str]) -> str:
    body = {"model": MODEL,
            "messages": [{"role": "user",
                          "content": NARRATE_PROMPT + "\n".join(f"- {f}" for f in facts)}],
            "stream": False, "think": False,
            "options": {"temperature": 0.4}}
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=TIMEOUT)
    r.raise_for_status()
    text = (r.json().get("message", {}).get("content", "") or "").strip()
    if not text:
        raise ValueError("empty narration")
    return text


def write_entry(day: dt.date, text: str, facts: list[str]) -> None:
    records_store.log_event("note", subject="Journal", action="journal", detail=text,
                            ts=f"{day.isoformat()}T23:59:00")
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    md = [f"# {day.strftime('%A, %B ')}{day.day}, {day.year}", "", text, ""]
    if facts:
        md += ["---", ""] + [f"- {f}" for f in facts]
    (JOURNAL_DIR / f"{day.isoformat()}.md").write_text("\n".join(md) + "\n")


def already_written(day: dt.date) -> bool:
    return any((e["ts"] or "").startswith(day.isoformat())
               for e in records_store.recent_events(subject="Journal", limit=3))


def main() -> int:
    dry = "--dry-run" in sys.argv
    now = dt.datetime.now()
    if not dry and already_written(now.date()):
        print(f"journal: {now.date().isoformat()} already written; skipping")
        return 0
    facts = build_facts(now)
    try:
        text = narrate(facts)
    except Exception as e:  # noqa: BLE001
        print(f"journal: narration failed ({e}); journaling raw facts", file=sys.stderr)
        text = "(model was down; raw log) " + "; ".join(facts)
    if dry:
        print("journal (dry-run) facts:\n" + "\n".join(f"- {f}" for f in facts))
        print("\njournal (dry-run) entry:\n" + text)
        return 0
    write_entry(now.date(), text, facts)
    print(f"journal: wrote {now.date().isoformat()} ({len(facts)} facts):\n{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
