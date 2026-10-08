"""`records` tool — Hestia's structured, relational memory.

For things you reference or track over time: people, pets (incl. breeding lineage),
places, species, assets, and a uniform timestamped event log (wildlife sightings,
chores, health records). Distinct from `memory`, which is for soft facts/preferences.

Actions:
  remember — create/update an entity (with aliases + attributes). "Momo is our oldest
             Lhasa Apso, born 2018." After this, 'Momo' resolves everywhere.
  log      — record a timestamped event about a subject (sighting/chore/health/breeding/note).
             A tie (kind breeding, did tied) also files the dam's day-28 pregnancy check and
             day-56 whelp-watch reminders, in code, and the reply says so.
  recent   — list recent events, optionally filtered by kind / subject / since.
  entity   — profile a named thing: its attributes, relations, and recent events.
  relate   — link two entities (e.g. a pup —sire→ Momo; a person —owns→ a pet).
  due      — service reminders: assets past their interval since last logged.
"""
from __future__ import annotations

import datetime as dt
import json

import breeding_followups
import records_store as store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "records",
        "description": ("Structured, relational memory for things tracked over time: people, "
                        "pets (and breeding lineage), places, species, assets, plus a timestamped "
                        "event log (wildlife sightings, chores, health records, service reminders). "
                        "Call this WHENEVER the user states a fact about such an entity or reports "
                        "that something happened — log it even when they don't explicitly say "
                        "'record this'; do not just reply conversationally. 'We got a new puppy "
                        "named Biscuit' -> remember (a pet entity); 'I vaccinated the dogs today' or "
                        "'mowed the north field' -> log (a dated event); 'when did I last see a "
                        "deer?' -> recent/entity. "
                        "But a loose standalone preference (a favorite coffee, a brand they like) is "
                        "plain `memory`, not records — records is for entities and dated events. "
                        "Use 'remember' to register an entity you'll refer to (so names like a pet's "
                        "resolve later), 'log' to record something that happened, 'recent' to review "
                        "logs, 'entity' to look someone/something up, 'relate' to link two entities, "
                        "'birth' to record a newborn puppy (creates the pup as a pet, links dam/sire, "
                        "groups it into the litter, derives the litter size), "
                        "'weigh' EVERY time a puppy is weighed ('Biscuit is 6.2 ounces', 'pup two "
                        "is up to 7 oz') — pass name, qty and unit; use this INSTEAD of a plain log "
                        "so the number lands on the growth curve the fading-pup watcher reads, "
                        "'harvest' whenever the user says they picked/pulled/got produce from a bed "
                        "('pulled four pounds of tomatoes from Bed 4', 'got a dozen cucumbers off "
                        "Bed 2') — pass bed, crop, qty and unit; use this INSTEAD of a plain log so "
                        "the amount is kept, 'yield' to answer 'how much X have we picked this "
                        "year / how did the beds do', "
                        "'due' for overdue service reminders. Prefer this over plain memory whenever "
                        "the thing is an entity or a dated record, not just a loose preference."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["remember", "log", "birth", "weigh", "harvest", "yield", "recent", "entity", "relate", "due"]},
                "bed": {"type": "string", "description": "for harvest/yield: the bed or zone picked from, e.g. 'Bed 4', 'Carrots Round Bed'"},
                "crop": {"type": "string", "description": "for harvest/yield: what was picked, e.g. 'Tomatoes'"},
                "qty": {"type": ["number", "string"], "description": "for harvest/weigh: how much — a plain number, or the full amount as text ('2 lb 7 oz') when it's a mixed/compound weight that doesn't reduce to one unit"},
                "unit": {"type": "string", "description": "for harvest: lb/oz/kg/g for weight, pint/quart for volume, or omit for a plain count. for weigh: oz/g/lb/kg — a weight unit is required"},
                "year": {"type": "integer", "description": "for yield: season year (default this year)"},
                "name": {"type": "string", "description": "entity name (remember/entity; the 'from' for relate; the puppy's name for birth)"},
                "dam": {"type": "string", "description": "for birth: the mother's name"},
                "sire": {"type": "string", "description": "for birth: the father's name"},
                "sex": {"type": "string", "description": "for birth: male/female"},
                "weight": {"type": "string", "description": "for birth: birth weight, e.g. '7.5 oz'"},
                "color": {"type": "string", "description": "for birth: coat color/markings"},
                "litter": {"type": "string", "description": "for birth: explicit litter name (optional; otherwise grouped by dam+sire+date)"},
                "kind": {"type": "string", "description": "for remember: person|pet|place|species|asset. for log: sighting|chore|health|breeding|note"},
                "aliases": {"type": "array", "items": {"type": "string"}, "description": "other names for the entity (remember)"},
                "attrs": {"type": "object", "description": "attributes — remember: e.g. breed, dob, relationship, interval_days (for an asset's service interval); log: e.g. count, species_specificity, confidence"},
                "subject": {"type": "string", "description": "for log: what the event is about (a species, asset, or pet name)"},
                "did": {"type": "string", "description": "for log: the action verb, e.g. observed, mowed, vaccinated"},
                "detail": {"type": "string", "description": "freeform detail (log/remember)"},
                "location": {"type": "string", "description": "for log: where it happened"},
                "ts": {"type": "string", "description": "for log: ISO timestamp when it happened, if not now"},
                "rel": {"type": "string", "description": "for relate: relationship type, e.g. sire, dam, owns, parent"},
                "to": {"type": "string", "description": "for relate: the other entity"},
                "since": {"type": "string", "description": "for recent: ISO date lower bound"},
                "limit": {"type": "integer", "description": "for recent: max rows (default 20, max 100)"},
            },
            "required": ["action"],
        },
    },
}


def _when(ts: str) -> str:
    """The moment an event was stored, in words, with how long ago that is, so a wrong date
    reads as wrong. Spoken on the kitchen mic as well as shown, so no ISO strings."""
    try:
        t = dt.datetime.fromisoformat(ts)
    except ValueError:
        return ts
    if t.tzinfo:
        t = t.astimezone().replace(tzinfo=None)
    days = (dt.date.today() - t.date()).days
    rel = ("today" if days == 0 else "yesterday" if days == 1 else "tomorrow" if days == -1
           else f"{days} days ago" if days > 1 else f"in {-days} days")
    stamp = f"{t:%b} {t.day}, {t.year}"
    if len(ts) > 10:
        stamp += f" {t:%I:%M %p}".replace(" 0", " ", 1)
    return f"{stamp} ({rel})"


def _stored(r: dict, did: str | None, detail: str | None, location: str | None) -> str:
    """What was actually written, as the reply to a log. After a write the brain answers with
    this text rather than the model's own words, so it is what the operator sees and hears:
    the date the event landed on and the start of its detail are how a wrong date, a missing
    detail or a mistyped name gets caught on the spot. It must keep starting with "Logged "
    because that prefix is how tool_contract recognises a successful write."""
    bits = [f"Logged {r['kind']}"]
    if r["subject"]:
        bits.append(f"· {r['subject']}")
    if did:
        bits.append(f"· {did}")
    bits.append(f"· {_when(r['ts'])}")
    snippet = " ".join((detail or "").split())
    if snippet:
        bits.append('· "' + (snippet if len(snippet) <= 60 else snippet[:57].rstrip() + "...") + '"')
    if location:
        bits.append(f"@ {location}")
    reply = " ".join(bits) + "."
    if r["created"] and r["subject"]:
        reply += (f"  ⚠ '{r['subject']}' wasn't a known name, so a new record was created. "
                  "Correct me if that's a mishear.")
    return reply


def _follow_ups() -> str:
    """A tie just went on the books: file its pregnancy-check and whelp-watch reminders and say
    so. The event is already written, so nothing here may fail the log; the twice-daily puppy
    watch retries whatever this could not do."""
    try:
        made = breeding_followups.ensure()
    except Exception as e:  # noqa: BLE001
        return (f" The follow-up reminders could not be set ({type(e).__name__}); "
                "the daily watch will retry them.")
    if not made:
        return ""
    return " Reminders set: " + "; ".join(
        f"{m['dam']} {m['what']} on {m['due_at'][:10]}" for m in made) + "."


def _as_dict(x) -> dict | None:
    """Coerce model-supplied attrs to a dict. None means INVALID (a string that isn't a
    JSON object, incl. valid JSON that isn't an object like '[1,2]') — callers must refuse
    rather than silently drop the attributes and log the event without them."""
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
        except Exception:  # noqa: BLE001
            return None
        return v if isinstance(v, dict) else None
    return {}


def execute(action: str, name: str | None = None, kind: str | None = None,
            aliases: list | None = None, attrs: dict | None = None,
            subject: str | None = None, did: str | None = None, detail: str | None = None,
            location: str | None = None, ts: str | None = None, rel: str | None = None,
            to: str | None = None, since: str | None = None, limit: int = 20,
            dam: str | None = None, sire: str | None = None, sex: str | None = None,
            weight: str | None = None, color: str | None = None, litter: str | None = None,
            bed: str | None = None, crop: str | None = None, qty: float | str | None = None,
            unit: str | None = None, year: int | None = None) -> str:
    try:
        # attrs validation is shared by the three actions that take it; a malformed string
        # is refused outright, never silently dropped (the log-kind defaulting lesson:
        # silent munging hides the model's mistake instead of letting it fix the call).
        if action in ("remember", "log", "birth"):
            attrs = _as_dict(attrs)
            if attrs is None:
                return "Error: attrs wasn't a valid JSON object — nothing was recorded."

        if action == "weigh":
            if not name or qty is None:
                return "Error: weigh needs the puppy's name and a weight (e.g. name='Biscuit', qty=6.2, unit='oz')."
            try:
                r = store.log_weight(name, qty, unit=unit, ts=ts, detail=detail)
            except (TypeError, ValueError) as e:
                return (f"Error: {e}. Give the weight as a number plus a weight unit "
                        f"(qty=6.2, unit='oz'), or the full amount as text ('1 lb 2 oz').")
            # A brand-new pup on a weighing is far more often a misheard name than a pup nobody
            # recorded being born — say so instead of quietly starting a second curve for one pup.
            warn = (f"  \u26a0 '{name}' wasn't a known puppy — created it. Correct me if that's a mishear."
                    if r.get("created") else "")
            days = store.weight_series(name)
            trend = ""
            if len(days) >= 2:
                delta = days[-1]["grams"] - days[-2]["grams"]
                word = "up" if delta > 0 else "down" if delta < 0 else "flat"
                trend = (f" That is {word}" + (f" {abs(delta) / 28.3495:.1f} oz" if delta else "")
                         + " from the previous weighing.")
            return f"Logged: {name} at {r['amount']}.{trend}{warn}"

        if action == "harvest":
            if not bed or not crop or qty is None:
                return "Error: harvest needs a bed, a crop, and a quantity (unit optional)."
            try:
                q, unit = store.parse_qty_unit(qty, unit)
            except (TypeError, ValueError):
                return ("Error: couldn't parse that quantity — give it as a number (4.8), or "
                        "the full amount as text ('2 lb 7 oz') for a mixed weight.")
            if not q > 0:  # also rejects NaN, which compares False to everything
                return "Error: quantity must be a positive number."
            r = store.log_harvest(bed, crop, q, unit=unit, ts=ts, detail=detail)
            cls, canon = store.normalize_unit(unit)
            amount = f"{q:g}" + (f" {canon}" if cls != "count" else "")
            # A brand-new bed on a harvest is almost always a misheard name, not a new bed —
            # say so rather than silently minting one (the photo-intake lesson).
            warn = (f"  ⚠ '{bed}' wasn't a known bed — created it. Correct me if that's a mishear."
                    if r.get("created") else "")
            season = store.harvest_totals(crop=crop)
            run = ""
            if season:
                s = season[0]
                run = (f" Season total: {s['lb']} lb" if s["unit_class"] == "weight"
                       else f" Season total: {s['qty']:g}")
                run += f" of {s['crop']} across {s['pickings']} picking(s)."
            return f"Logged {amount} of {crop} from {bed}.{run}{warn}"

        if action == "yield":
            rows = store.harvest_totals(year=year, crop=crop, bed=bed)
            if not rows:
                where = f" for {crop}" if crop else ""
                return f"No harvests logged{where} in {year or 'this season'} yet."
            head = f"Harvest {year or 'this season'}" + (f" — {bed}" if bed else "") + ":"
            lines = []
            for s in rows[:20]:
                amt = f"{s['lb']} lb" if s["unit_class"] == "weight" else f"{s['qty']:g}"
                beds = f" from {', '.join(s['beds'])}" if s["beds"] and not bed else ""
                lines.append(f"  {s['crop']}: {amt} over {s['pickings']} picking(s)"
                             f"{beds} ({s['first'][:10]} to {s['last'][:10]})")
            return head + "\n" + "\n".join(lines)

        if action == "remember":
            if not name or not kind:
                return "Error: remember needs a name and a kind (person/pet/place/species/asset)."
            e = store.upsert_entity(kind, name, aliases=aliases, attrs=attrs)
            a = e["attrs"]
            extra = f" — {', '.join(f'{k}: {v}' for k, v in a.items())}" if a else ""
            return f"Remembered {e['name']} ({e['kind']}){extra}."

        if action == "log":
            # A bare observation IS a note — default the kind rather than erroring, so a
            # small model that forgets `kind` (seen on garden observations) still records
            # the event instead of silently dropping it.
            kind = kind or "note"
            r = store.log_event(kind, subject=subject, action=did, detail=detail,
                                location=location, ts=ts, attrs=attrs)
            reply = _stored(r, did, detail, location)
            if kind == "breeding" or (did or "").lower() in breeding_followups.TIE_ACTIONS:
                reply += _follow_ups()
            return reply

        if action == "birth":
            if not name:
                return "Error: birth needs the puppy's name (and ideally dam + sire)."
            battrs = {**attrs}
            for k, v in (("sex", sex), ("weight", weight), ("color", color)):
                if v:
                    battrs[k] = v
            if detail:
                battrs["note"] = detail
            res = store.add_birth(name, dam=dam, sire=sire, born=ts, litter=litter, attrs=battrs)
            return (f"Recorded puppy {res['pup']}"
                    + (f" (dam {dam}, sire {sire})" if dam and sire else "")
                    + f" in {res['litter']} — litter now {res['litter_size']} pup(s).")

        if action == "recent":
            # Clamp the model-supplied limit: sqlite treats LIMIT -1 as 'no limit', so an
            # unvalidated value can dump the entire event log into context.
            try:
                limit = max(1, min(100, int(limit)))
            except (TypeError, ValueError):
                limit = 20
            evs = store.recent_events(kind=kind, subject=subject, since=since, limit=limit)
            if not evs:
                return "No matching records."
            lines = []
            for e in evs:
                when = e["ts"][:16].replace("T", " ")
                parts = [when, e["kind"]]
                if e["subject"]:
                    parts.append(e["subject"])
                if e["action"]:
                    parts.append(e["action"])
                if e["location"]:
                    parts.append(f"@{e['location']}")
                if e["detail"]:
                    parts.append(f"— {e['detail']}")
                if e["attrs"]:
                    parts.append(str(e["attrs"]))
                lines.append("  " + " · ".join(parts))
            return f"Recent records ({len(evs)}):\n" + "\n".join(lines)

        if action == "entity":
            if not name:
                return "Error: entity needs a name."
            p = store.entity_profile(name)
            if not p:
                return f"I don't have a record for '{name}'."
            out = [f"{p['name']} ({p['kind']})"]
            if p["attrs"]:
                out.append("  " + ", ".join(f"{k}: {v}" for k, v in p["attrs"].items()))
            if p.get("litters"):
                out.append(f"  progeny: {p['puppies_total']} puppies across {len(p['litters'])} litter(s) — "
                           + ", ".join(f"{x['whelp_date']} ({x['puppies']})" for x in p["litters"]))
            if p.get("pairings"):
                cap = p["attrs"].get("max_dams")
                tail = f" ({len(p['pairings'])} of {cap} capacity)" if cap else ""
                out.append("  paired with: " + ", ".join(p["pairings"]) + tail)
            if p["relations"]:
                out.append("  relations: " + "; ".join(f"{r['rel']} {r['other']}" for r in p["relations"]))
            if p["recent"]:
                out.append("  recent: " + "; ".join(
                    f"{e['ts'][:10]} {e['kind']}" + (f" {e['action']}" if e["action"] else "") for e in p["recent"]))
            return "\n".join(out)

        if action == "relate":
            if not name or not rel or not to:
                return "Error: relate needs name, rel, and to."
            return store.add_relation(name, rel, to)

        if action == "due":
            d = store.due_assets()
            if not d:
                return "Nothing is overdue."
            return "Overdue:\n" + "\n".join(
                f"  {x['name']}: {x.get('schedule') or str(x['interval_days']) + 'd'}, "
                + (f"last {x['last']} ({x['days_since']}d ago)" if x['days_since'] is not None else "never logged")
                for x in d)

        return f"Error: unknown action '{action}'."
    except Exception as e:  # noqa: BLE001
        return f"Error in records.{action}: {e}"
