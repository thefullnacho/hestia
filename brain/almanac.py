"""The household almanac — the season, written down where next year can find it.

Deterministic generator (no LLM): reads the pest-watch season state (observed biofix +
GDD spine), the NOAA 1991-2020 frost normals (vendored from the Homesteader Labs site:
    cp <site>/content/frost-zones.json data/frost-zones.json
zone via HESTIA_USDA_ZONE), and the records event log, and renders one page per season:

    data/almanac/<year>.md      — the human page (frost dates vs normal, GDD, garden
                                  timeline, wildlife, journal coverage)
    data/almanac/<year>.json    — the machine snapshot; prior years' snapshots are what
                                  make the year-over-year table light up next season

Runs nightly right after the journal (same timer, second ExecStart) so the page is never
stale, and regenerating is idempotent. This is the artifact "what cloud assistant could
ever answer 'when did we plant tomatoes last year'" is about.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import defaultdict

import config

config.load_secrets()
import pest_watch                      # noqa: E402
import records_store                   # noqa: E402
from tools import weather              # noqa: E402

ZONE = os.environ.get("HESTIA_USDA_ZONE", "7a")
ALMANAC_DIR = config.DATA_DIR / "almanac"
FROST_ZONES = config.DATA_DIR / "frost-zones.json"

# Event actions that belong on the garden timeline (records carries plenty else: health
# logs, chores, photo attachments — the almanac wants the agricultural spine only).
_TIMELINE_ACTIONS = {"planted", "harvested", "observed", "pest_alert"}


def _normals() -> dict | None:
    try:
        return json.loads(FROST_ZONES.read_text())["zones"].get(ZONE)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _season_events(year: int) -> list[dict]:
    evs = records_store.recent_events(since=f"{year}-01-01", limit=200)
    return [e for e in reversed(evs) if (e["ts"] or "")[:4] == str(year)]


def _fmt_day(iso: str) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return d.strftime("%b ") + str(d.day)


def _delta_vs_normal(observed_iso: str, normal_mmdd: str) -> str:
    o = dt.date.fromisoformat(observed_iso)
    n = dt.date(o.year, int(normal_mmdd[:2]), int(normal_mmdd[3:]))
    days = (o - n).days
    if days == 0:
        return "right on the zone normal"
    return f"{abs(days)} days {'later' if days > 0 else 'earlier'} than the zone normal ({_fmt_day(n.isoformat())})"


def snapshot(year: int, today: dt.date) -> dict:
    """The machine-readable season summary — the unit of year-over-year comparison."""
    state = pest_watch._load_state()
    events = _season_events(year)
    timeline = [e for e in events if e["action"] in _TIMELINE_ACTIONS
                and e["subject"] != "Journal" and e["kind"] != "sighting"]  # wildlife has its own section
    sightings = defaultdict(list)
    for e in events:
        if e["kind"] == "sighting" and e["subject"]:
            sightings[e["subject"]].append(e["ts"][:10])
    journal_days = sorted({e["ts"][:10] for e in events if e["action"] == "journal"})
    # The NOAA normals are 32°F at 50% probability; the pest biofix is <=FROST_F (36°F).
    # Compare like with like: find the season's last observed HARD-32 morning ourselves.
    frost32 = None
    try:
        rows = weather.history_days(f"{year}-01-01", f"{year}-06-30")
        freezes = [r["date"] for r in rows if r["lo"] <= 32]
        frost32 = freezes[-1] if freezes else None
    except Exception as e:  # noqa: BLE001 — the page renders without the comparison
        print(f"almanac: frost32 lookup failed: {e}", file=sys.stderr)
    return {
        "year": year, "zone": ZONE, "as_of": today.isoformat(),
        "biofix": state.get("biofix") if state.get("season") == str(year) else None,
        "last_frost32": frost32,
        "gdd": round(state.get("cumulative_gdd", 0)) if state.get("season") == str(year) else None,
        "pest_windows_opened": sorted(k for k, v in (state.get("alerted") or {}).items()
                                      if v == str(year)),
        "timeline": [{"date": e["ts"][:10], "subject": e["subject"],
                      "action": e["action"], "detail": e["detail"]} for e in timeline],
        "species_seen": {s: {"first": min(d), "sightings": len(d)} for s, d in sightings.items()},
        "journal_days": len(journal_days),
        "journal_span": [journal_days[0], journal_days[-1]] if journal_days else None,
    }


def _prior_snapshots(year: int) -> list[dict]:
    out = []
    for p in sorted(ALMANAC_DIR.glob("20*.json")):
        try:
            s = json.loads(p.read_text())
            if s.get("year", year) < year:
                out.append(s)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def render(snap: dict) -> str:
    normals = _normals()
    L = [f"# Almanac {snap['year']} — zone {snap['zone']}",
         f"*Generated {snap['as_of']}; regenerates nightly.*", "", "## Season & frost"]
    if snap.get("last_frost32"):
        line = f"- **Last spring freeze (observed, 32°F):** {_fmt_day(snap['last_frost32'])}"
        if normals:
            line += f" — {_delta_vs_normal(snap['last_frost32'], normals['lastSpringFrost'])}"
        L.append(line)
    if snap["biofix"]:
        L.append(f"- **Growing-season biofix (last ≤{weather.FROST_F:.0f}°F morning):** "
                 f"{_fmt_day(snap['biofix'])} — GDD accumulates from here")
    if normals:
        ff = dt.date(snap["year"], int(normals["firstFallFrost"][:2]), int(normals["firstFallFrost"][3:]))
        L.append(f"- **First fall frost (zone normal):** around {ff.strftime('%b ')}{ff.day} "
                 f"(±{normals['firstFrostVarianceDays']}d); ~{normals['frostFreeDays']} frost-free days normal")
    if snap["gdd"] is not None:
        L.append(f"- **Growing degree-days (base 50):** {snap['gdd']} accumulated as of {snap['as_of']}")
    if snap["pest_windows_opened"]:
        names = sorted({p.split(":")[1] for p in snap["pest_windows_opened"]})
        L.append(f"- **Pest windows opened:** {len(snap['pest_windows_opened'])} across "
                 f"{len(names)} pests ({', '.join(names)})")

    L += ["", "## Garden timeline"]
    if snap["timeline"]:
        L += [f"- **{_fmt_day(t['date'])}** — {t['subject']}: {t['action']}"
              + (f" — {t['detail']}" if t["detail"] else "") for t in snap["timeline"]]
    else:
        L.append("*(no garden events logged yet)*")

    L += ["", "## Wildlife"]
    if snap["species_seen"]:
        L += [f"- **{s}** — first logged {_fmt_day(v['first'])}, {v['sightings']} sighting(s)"
              for s, v in sorted(snap["species_seen"].items())]
    else:
        L.append("*(no sightings logged yet)*")

    if snap["journal_span"]:
        L += ["", f"*Journal: {snap['journal_days']} entries, "
                  f"{_fmt_day(snap['journal_span'][0])} → {_fmt_day(snap['journal_span'][1])}.*"]

    priors = _prior_snapshots(snap["year"])
    if priors:
        L += ["", "## Year over year"]
        for p in priors:
            bits = []
            po = p.get("last_frost32") or p.get("biofix")
            so = snap.get("last_frost32") or snap.get("biofix")
            if po and so:
                bits.append(f"last frost {_fmt_day(po)} vs {_fmt_day(so)}")
            if p.get("gdd") is not None and snap.get("gdd") is not None:
                bits.append(f"GDD {p['gdd']} vs {snap['gdd']} (as-of dates differ)")
            firsts = {s: v["first"] for s, v in (p.get("species_seen") or {}).items()
                      if s in snap["species_seen"]}
            if firsts:
                bits.append("returning species: " + ", ".join(sorted(firsts)))
            L.append(f"- **{p['year']}:** " + ("; ".join(bits) if bits else "(snapshot on file)"))
    return "\n".join(L) + "\n"


def generate(year: int | None = None, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    year = year or today.year
    snap = snapshot(year, today)
    ALMANAC_DIR.mkdir(parents=True, exist_ok=True)
    (ALMANAC_DIR / f"{year}.json").write_text(json.dumps(snap, indent=2))
    page = render(snap)
    (ALMANAC_DIR / f"{year}.md").write_text(page)
    return page


def main() -> int:
    year = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    page = generate(year)
    print(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
