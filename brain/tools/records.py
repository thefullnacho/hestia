"""`records` tool — Hestia's structured, relational memory.

For things you reference or track over time: people, pets (incl. breeding lineage),
places, species, assets, and a uniform timestamped event log (wildlife sightings,
chores, health records). Distinct from `memory`, which is for soft facts/preferences.

Actions:
  remember — create/update an entity (with aliases + attributes). "Momo is our oldest
             Lhasa Apso, born 2018." After this, 'Momo' resolves everywhere.
  log      — record a timestamped event about a subject (sighting/chore/health/note).
  recent   — list recent events, optionally filtered by kind / subject / since.
  entity   — profile a named thing: its attributes, relations, and recent events.
  relate   — link two entities (e.g. a pup —sire→ Momo; a person —owns→ a pet).
  due      — service reminders: assets past their interval since last logged.
"""
from __future__ import annotations

import json

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
                "action": {"type": "string", "enum": ["remember", "log", "birth", "harvest", "yield", "recent", "entity", "relate", "due"]},
                "bed": {"type": "string", "description": "for harvest/yield: the bed or zone picked from, e.g. 'Bed 4', 'Carrots Round Bed'"},
                "crop": {"type": "string", "description": "for harvest/yield: what was picked, e.g. 'Tomatoes'"},
                "qty": {"type": "number", "description": "for harvest: how much — the number only"},
                "unit": {"type": "string", "description": "for harvest: lb/oz/kg/g for weight, pint/quart for volume, or omit for a plain count"},
                "year": {"type": "integer", "description": "for yield: season year (default this year)"},
                "name": {"type": "string", "description": "entity name (remember/entity; the 'from' for relate; the puppy's name for birth)"},
                "dam": {"type": "string", "description": "for birth: the mother's name"},
                "sire": {"type": "string", "description": "for birth: the father's name"},
                "sex": {"type": "string", "description": "for birth: male/female"},
                "weight": {"type": "string", "description": "for birth: birth weight, e.g. '7.5 oz'"},
                "color": {"type": "string", "description": "for birth: coat color/markings"},
                "litter": {"type": "string", "description": "for birth: explicit litter name (optional; otherwise grouped by dam+sire+date)"},
                "kind": {"type": "string", "description": "for remember: person|pet|place|species|asset. for log: sighting|chore|health|note"},
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
                "limit": {"type": "integer", "description": "for recent: max rows (default 20)"},
            },
            "required": ["action"],
        },
    },
}


def _as_dict(x) -> dict:
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def execute(action: str, name: str | None = None, kind: str | None = None,
            aliases: list | None = None, attrs: dict | None = None,
            subject: str | None = None, did: str | None = None, detail: str | None = None,
            location: str | None = None, ts: str | None = None, rel: str | None = None,
            to: str | None = None, since: str | None = None, limit: int = 20,
            dam: str | None = None, sire: str | None = None, sex: str | None = None,
            weight: str | None = None, color: str | None = None, litter: str | None = None,
            bed: str | None = None, crop: str | None = None, qty: float | None = None,
            unit: str | None = None, year: int | None = None) -> str:
    try:
        if action == "harvest":
            if not bed or not crop or qty is None:
                return "Error: harvest needs a bed, a crop, and a quantity (unit optional)."
            r = store.log_harvest(bed, crop, qty, unit=unit, ts=ts, detail=detail)
            cls, canon = store.normalize_unit(unit)
            amount = f"{float(qty):g}" + (f" {canon}" if cls != "count" else "")
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
            e = store.upsert_entity(kind, name, aliases=aliases, attrs=_as_dict(attrs))
            a = e["attrs"]
            extra = f" — {', '.join(f'{k}: {v}' for k, v in a.items())}" if a else ""
            return f"Remembered {e['name']} ({e['kind']}){extra}."

        if action == "log":
            # A bare observation IS a note — default the kind rather than erroring, so a
            # small model that forgets `kind` (seen on garden observations) still records
            # the event instead of silently dropping it.
            kind = kind or "note"
            store.log_event(kind, subject=subject, action=did, detail=detail,
                            location=location, ts=ts, attrs=_as_dict(attrs))
            bits = [f"Logged {kind}"]
            if subject:
                bits.append(f"· {subject}")
            if did:
                bits.append(f"· {did}")
            if location:
                bits.append(f"@ {location}")
            return " ".join(bits) + "."

        if action == "birth":
            if not name:
                return "Error: birth needs the puppy's name (and ideally dam + sire)."
            battrs = {**_as_dict(attrs)}
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
                f"  {x['name']}: every {x['interval_days']}d, "
                + (f"last {x['last']} ({x['days_since']}d ago)" if x['days_since'] is not None else "never logged")
                for x in d)

        return f"Error: unknown action '{action}'."
    except Exception as e:  # noqa: BLE001
        return f"Error in records.{action}: {e}"
