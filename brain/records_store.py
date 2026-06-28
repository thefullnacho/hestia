"""Structured records — Hestia's relational substrate (SQLite, local on the box).

The markdown memory (memory_store) holds soft facts and preferences. THIS holds the
structured, relational, time-stamped stuff that freeform notes can't serve: entities
(people, pets, places, species, assets) with aliases + relations, and a single events
log that powers wildlife sightings, chore logging, health records, and — read
backwards — service reminders.

The whole design rests on two ideas:
  1. ENTITIES are canonical, with aliases — so "Momo", "the oldest dog" resolve to one
     record everything else points at. (The thing that's expensive to retrofit.)
  2. EVENTS are uniform: (when, kind, subject-entity, action, detail, location, attrs).
     A sighting, a mowed lawn, and a vaccination are the same shape; a reminder is just
     "how long since the last event of this kind for this asset?".

Zero-ops: one file, created on first use. Env: HESTIA_DB.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3

import config

DB_PATH = config.DB_PATH

# default entity kind to mint when a log's subject doesn't exist yet, per event kind
_SUBJECT_KIND = {"sighting": "species", "chore": "asset", "health": "pet"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  attrs TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
  alias TEXT PRIMARY KEY,          -- lowercased; resolves to an entity
  entity_id INTEGER NOT NULL REFERENCES entities(id)
);
CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY,
  from_id INTEGER NOT NULL REFERENCES entities(id),
  rel TEXT NOT NULL,               -- sire | dam | owns | parent | ...
  to_id INTEGER NOT NULL REFERENCES entities(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,                -- when it happened (provenance)
  kind TEXT NOT NULL,             -- sighting | chore | health | note | ...
  entity_id INTEGER REFERENCES entities(id),
  action TEXT,                     -- mowed | observed | vaccinated | ...
  detail TEXT,
  location TEXT,
  attrs TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL         -- when logged
);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  due_at TEXT NOT NULL,            -- ISO local time to fire the push
  text TEXT NOT NULL,             -- what to remind the user about
  created_at TEXT NOT NULL,
  fired_at TEXT                    -- null until the timer pushes it (then provenance)
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    # The records DB holds people, pets, and locations — keep it owner-only (audit #9).
    # Enforce on creation and self-heal if a restore/copy ever widened the mode.
    if new or (DB_PATH.stat().st_mode & 0o777) != 0o600:
        os.chmod(DB_PATH, 0o600)
    return c


def _row_entity(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "attrs": json.loads(r["attrs"] or "{}")}


# ----- entities ------------------------------------------------------------

def upsert_entity(kind: str, name: str, aliases: list[str] | None = None,
                  attrs: dict | None = None) -> dict:
    """Create or update an entity (merging attrs); register its name + aliases."""
    with _conn() as c:
        existing = resolve(name, conn=c)
        if existing and existing["kind"] == kind:
            merged = {**existing["attrs"], **(attrs or {})}
            c.execute("UPDATE entities SET attrs=? WHERE id=?", (json.dumps(merged), existing["id"]))
            eid = existing["id"]
        else:
            cur = c.execute("INSERT INTO entities(kind,name,attrs,created_at) VALUES(?,?,?,?)",
                            (kind, name, json.dumps(attrs or {}), _now()))
            eid = cur.lastrowid
        for a in {name, *(aliases or [])}:
            a = a.strip().lower()
            if a:
                c.execute("INSERT OR IGNORE INTO aliases(alias,entity_id) VALUES(?,?)", (a, eid))
        row = c.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
        return _row_entity(row)


def resolve(name: str, conn: sqlite3.Connection | None = None,
            kind: str | None = None) -> dict | None:
    """Find an entity by alias (exact, case-insensitive) or fuzzy name match.

    Pass `kind` to scope the lookup to one entity kind and match ONLY on an exact handle — an
    alias of that kind, or an exact (case-insensitive) name. This is the auto-mint guard: a name
    typed into a Shortcut is deliberate, so a brand-new subject mints cleanly instead of
    false-attaching by substring — a new wildlife 'Park' won't grab the 'Park/Orchard Zone' place,
    a new pup 'Lil'/'Bo' won't grab 'Lily'/'Bodhi'. Called without `kind` (the conversational
    path), behaviour is unchanged: alias-exact, then loosely fuzzy so 'the beets bed' still matches."""
    own = conn is None
    c = conn or _conn()
    try:
        key = (name or "").strip().lower()
        if not key:
            return None
        a = c.execute("SELECT entity_id FROM aliases WHERE alias=?", (key,)).fetchone()
        if a:
            ent = _row_entity(c.execute("SELECT * FROM entities WHERE id=?", (a["entity_id"],)).fetchone())
            # An alias is an intentional handle — honor it, unless we're minting a different kind
            # (then fall through to a kind-scoped match so we don't cross-attach domains).
            if kind is None or (ent and ent["kind"] == kind):
                return ent
        if kind is None:
            r = c.execute("SELECT * FROM entities WHERE name LIKE ? ORDER BY length(name) LIMIT 1",
                          (f"%{name}%",)).fetchone()
            return _row_entity(r) if r else None
        # kind-scoped mint guard: exact name within the kind only — no substring match, so a new
        # subject mints rather than gluing onto a longer existing name.
        r = c.execute("SELECT * FROM entities WHERE kind=? AND name=? COLLATE NOCASE LIMIT 1",
                      (kind, name)).fetchone()
        return _row_entity(r) if r else None
    finally:
        if own:
            c.close()


def _resolve_or_create(c: sqlite3.Connection, name: str, kind: str,
                       strict: bool = False) -> int:
    """Find an entity by name, or mint it with `kind`. When `strict`, the lookup is scoped to
    `kind` and prefers an exact name match (see resolve()), so the auto-mint path won't
    cross-attach a new subject to an unrelated entity. Loose by default for the other callers."""
    e = resolve(name, conn=c, kind=kind if strict else None)
    if e:
        return e["id"]
    cur = c.execute("INSERT INTO entities(kind,name,attrs,created_at) VALUES(?,?,?,?)",
                    (kind, name, "{}", _now()))
    c.execute("INSERT OR IGNORE INTO aliases(alias,entity_id) VALUES(?,?)", (name.strip().lower(), cur.lastrowid))
    return cur.lastrowid


def add_relation(from_name: str, rel: str, to_name: str) -> str:
    with _conn() as c:
        fid = _resolve_or_create(c, from_name, "thing")
        tid = _resolve_or_create(c, to_name, "thing")
        c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,?,?,?)",
                  (fid, rel, tid, _now()))
    return f"Linked {from_name} —{rel}→ {to_name}."


# ----- events --------------------------------------------------------------

def log_event(kind: str, subject: str | None = None, action: str | None = None,
              detail: str | None = None, location: str | None = None,
              ts: str | None = None, attrs: dict | None = None,
              subject_kind: str | None = None, strict_subject: bool = False) -> dict:
    """Record one timestamped event. Auto-creates the subject entity if new. Set `strict_subject`
    to scope that auto-create to `subject_kind` and prefer an exact match (used by the photo
    intake so a new subject mints cleanly instead of false-attaching across kinds)."""
    with _conn() as c:
        eid = None
        if subject:
            eid = _resolve_or_create(c, subject, subject_kind or _SUBJECT_KIND.get(kind, "thing"),
                                     strict=strict_subject)
        cur = c.execute(
            "INSERT INTO events(ts,kind,entity_id,action,detail,location,attrs,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ts or _now(), kind, eid, action, detail, location, json.dumps(attrs or {}), _now()))
        return {"id": cur.lastrowid, "subject": subject, "kind": kind}


# domain (from the photo Shortcut) -> the entity kind to mint the subject as if it's new.
# pet=puppy/dog, garden=a bed/zone/plant (place), wildlife=a species sighting, asset=a thing.
_PHOTO_DOMAIN_KIND = {"pet": "pet", "garden": "place", "wildlife": "species", "asset": "asset"}


def attach_photo(subject: str, path: str, caption: str | None = None,
                 domain: str = "pet") -> dict:
    """Record a photo against a named entity. Reuses log_event, so the subject resolves to an
    existing entity (the pup, the bed) or is minted with the domain's kind if new. The image
    lives on disk at `path`; the event carries the reference. Returns {id, subject, kind}."""
    kind = _PHOTO_DOMAIN_KIND.get(domain, "thing")
    return log_event("photo", subject=subject, action="photographed", detail=caption,
                     attrs={"path": path, "domain": domain}, subject_kind=kind,
                     strict_subject=True)


def add_birth(name: str, dam: str | None = None, sire: str | None = None,
              born: str | None = None, litter: str | None = None,
              attrs: dict | None = None) -> dict:
    """Record a newborn properly: pup = a pet entity, linked to dam/sire + its litter.

    The litter is identified by (dam, sire, whelp-date): the first pup of the day creates
    it, later pups the same day join it. Litter puppy count is derived from the actual
    pups, so progeny totals stay correct without manual counting.
    """
    born = born or _now()
    dob = born[:10]
    attrs = attrs or {}
    with _conn() as c:
        dam_id = _resolve_or_create(c, dam, "pet") if dam else None
        sire_id = _resolve_or_create(c, sire, "pet") if sire else None
        # find-or-create the litter
        if litter:
            lit_id = _resolve_or_create(c, litter, "litter")
        else:
            lit_id = None
            for r in c.execute("SELECT id,attrs FROM entities WHERE kind='litter'").fetchall():
                la = json.loads(r["attrs"] or "{}")
                if (la.get("whelp_date") == dob
                        and (la.get("dam") or "").lower() == (dam or "").lower()
                        and (la.get("sire") or "").lower() == (sire or "").lower()):
                    lit_id = r["id"]; break
            if lit_id is None:
                lname = f"Litter {dob} ({dam} x {sire})" if dam and sire else f"Litter {dob}"
                lit_id = c.execute("INSERT INTO entities(kind,name,attrs,created_at) VALUES(?,?,?,?)",
                                   ("litter", lname, json.dumps({"whelp_date": dob, "dam": dam, "sire": sire, "puppies": 0}), _now())).lastrowid
                c.execute("INSERT OR IGNORE INTO aliases(alias,entity_id) VALUES(?,?)", (lname.lower(), lit_id))
                if dam_id:
                    c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,'dam',?,?)", (lit_id, dam_id, _now()))
                if sire_id:
                    c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,'sire',?,?)", (lit_id, sire_id, _now()))
        # create the pup
        pup_id = c.execute("INSERT INTO entities(kind,name,attrs,created_at) VALUES(?,?,?,?)",
                           ("pet", name, json.dumps({"role": "puppy", "dob": dob, **attrs}), _now())).lastrowid
        c.execute("INSERT OR IGNORE INTO aliases(alias,entity_id) VALUES(?,?)", (name.strip().lower(), pup_id))
        if dam_id:
            c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,'dam',?,?)", (pup_id, dam_id, _now()))
        if sire_id:
            c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,'sire',?,?)", (pup_id, sire_id, _now()))
        c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,'pup',?,?)", (lit_id, pup_id, _now()))
        # derive litter size from actual pups
        cnt = c.execute("SELECT COUNT(*) n FROM relations WHERE from_id=? AND rel='pup'", (lit_id,)).fetchone()["n"]
        lrow = c.execute("SELECT name,attrs FROM entities WHERE id=?", (lit_id,)).fetchone()
        la = json.loads(lrow["attrs"] or "{}"); la["puppies"] = cnt
        c.execute("UPDATE entities SET attrs=? WHERE id=?", (json.dumps(la), lit_id))
        c.execute("INSERT INTO events(ts,kind,entity_id,action,detail,attrs,created_at) VALUES(?,?,?,?,?,?,?)",
                  (born, "birth", pup_id, "born", (f"{dam} x {sire}" if dam and sire else None), json.dumps(attrs), _now()))
        return {"pup": name, "litter": lrow["name"], "litter_size": cnt}


def recent_events(kind: str | None = None, subject: str | None = None,
                  since: str | None = None, limit: int = 20) -> list[dict]:
    with _conn() as c:
        q = ("SELECT e.*, en.name AS subject FROM events e "
             "LEFT JOIN entities en ON en.id=e.entity_id WHERE 1=1")
        args: list = []
        if kind:
            q += " AND e.kind=?"; args.append(kind)
        if subject:
            ent = resolve(subject, conn=c)
            q += " AND e.entity_id=?"; args.append(ent["id"] if ent else -1)
        if since:
            q += " AND e.ts>=?"; args.append(since)
        q += " ORDER BY e.ts DESC LIMIT ?"; args.append(max(1, min(200, limit)))
        rows = c.execute(q, args).fetchall()
        return [{"ts": r["ts"], "kind": r["kind"], "subject": r["subject"],
                 "action": r["action"], "detail": r["detail"], "location": r["location"],
                 "attrs": json.loads(r["attrs"] or "{}")} for r in rows]


def entity_profile(name: str) -> dict | None:
    with _conn() as c:
        ent = resolve(name, conn=c)
        if not ent:
            return None
        rels = c.execute(
            "SELECT r.rel AS rel, e2.name AS other, e2.kind AS okind, e2.attrs AS oattrs "
            "FROM relations r JOIN entities e2 ON e2.id=r.to_id WHERE r.from_id=? UNION ALL "
            "SELECT r.rel||' (of)' AS rel, e1.name AS other, e1.kind AS okind, e1.attrs AS oattrs "
            "FROM relations r JOIN entities e1 ON e1.id=r.from_id WHERE r.to_id=?",
            (ent["id"], ent["id"])).fetchall()
        evs = c.execute("SELECT ts,kind,action,detail FROM events WHERE entity_id=? ORDER BY ts DESC LIMIT 5",
                        (ent["id"],)).fetchall()
        # Surface progeny directly: litters this entity is dam/sire of, with puppy counts.
        litters = []
        for r in rels:
            # only litters this entity PARENTED (dam/sire of), not the litter it was born in
            if (r["okind"] == "litter" and r["rel"] in ("dam (of)", "sire (of)")
                    and r["other"] not in {x["name"] for x in litters}):
                a = json.loads(r["oattrs"] or "{}")
                litters.append({"name": r["other"], "whelp_date": a.get("whelp_date"),
                                "puppies": a.get("puppies", 0)})
        pairings = [r["other"] for r in rels if r["rel"].startswith("paired_with")]
        return {**ent,
                "relations": [{"rel": r["rel"], "other": r["other"]} for r in rels],
                "litters": sorted(litters, key=lambda x: x["whelp_date"] or ""),
                "puppies_total": sum(x["puppies"] or 0 for x in litters),
                "pairings": sorted(set(pairings)),
                "recent": [dict(e) for e in evs]}


def due_assets() -> list[dict]:
    """Assets with attrs.interval_days whose last logged event is older than the interval."""
    now = dt.datetime.now()
    out = []
    with _conn() as c:
        for r in c.execute("SELECT * FROM entities WHERE kind='asset'").fetchall():
            attrs = json.loads(r["attrs"] or "{}")
            interval = attrs.get("interval_days")
            if not interval:
                continue
            last = c.execute("SELECT ts FROM events WHERE entity_id=? ORDER BY ts DESC LIMIT 1",
                             (r["id"],)).fetchone()
            if last:
                age = (now - dt.datetime.fromisoformat(last["ts"])).days
                last_str = last["ts"][:10]
            else:
                age, last_str = 10**6, "never"
            if age >= interval:
                out.append({"name": r["name"], "interval_days": interval,
                            "days_since": age if last_str != "never" else None, "last": last_str})
    return out


def roster(limit: int = 40) -> str:
    """Compact people + pets list for the system prompt, so names resolve without a tool call."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM entities WHERE kind IN ('person','pet') ORDER BY kind,name LIMIT ?",
                         (limit,)).fetchall()
    if not rows:
        return ""
    lines = []
    for r in rows:
        attrs = json.loads(r["attrs"] or "{}")
        extra = ", ".join(f"{k} {v}" for k, v in attrs.items() if k in ("relationship", "breed", "dob", "role"))
        lines.append(f"  {r['name']} ({r['kind']}{': ' + extra if extra else ''})")
    return "People & pets you know:\n" + "\n".join(lines)


def garden_overview() -> str:
    """Compact 'what's planted where' from the seeded garden `place` entities, for the
    system prompt when the garden is the topic. Hierarchy: yard -> zone -> bed, each
    carrying its plantings (+ a note when a bed has a bound soil-moisture sensor).
    Places are excluded from roster() by design (they'd bloat every prompt); this is
    injected only on garden-topic requests via the garden_bed skill match."""
    with _conn() as c:
        rows = c.execute("SELECT name, attrs FROM entities WHERE kind='place'").fetchall()
    places = [(r["name"], json.loads(r["attrs"] or "{}")) for r in rows]
    if not places:
        return ""

    def plantings(a: dict) -> str:
        out = []
        for p in a.get("plantings") or []:
            if isinstance(p, dict):
                nm = p.get("plant") or p.get("name") or "?"
                ct = p.get("count")
                out.append(f"{nm} x{ct}" if ct else nm)
            else:
                out.append(str(p))
        return ", ".join(out)

    def stag(a: dict) -> str:
        return " [has soil sensor]" if a.get("sensor") else ""

    yards = sorted(n for n, a in places if a.get("area_type") == "yard") or ["Backyard"]
    zones = [(n, a) for n, a in places if a.get("area_type") == "zone"]
    others = [(n, a) for n, a in places if a.get("area_type") not in ("yard", "zone")]

    lines = ["The property garden — what's planted where (live from records; x-numbers are plant counts):"]
    for yard in yards:
        lines.append(f"{yard}:")
        for zn, za in sorted(zones):
            if za.get("yard") != yard:
                continue
            zp = plantings(za)
            lines.append(f"  {zn}:" + (f" {zp}" if zp else ""))
            for bn, ba in sorted(others):
                if ba.get("zone") == zn:
                    bp = plantings(ba)
                    lines.append(f"    - {bn}{stag(ba)}" + (f": {bp}" if bp else ""))
        for bn, ba in sorted(others):
            if not ba.get("zone") and ba.get("yard") == yard:
                bp = plantings(ba)
                lines.append(f"  - {bn}{stag(ba)}" + (f": {bp}" if bp else ""))
    return "\n".join(lines)


# Structural/generic words that must NOT drive a garden match (else "bed"/"garden"
# would hit everything). Plant nouns are deliberately NOT in here.
_GARDEN_STOP = {
    "bed", "beds", "zone", "zones", "guild", "guilds", "garden", "yard", "yards",
    "backyard", "frontyard", "round", "rectangle", "ground", "inground", "main",
    "border", "perennial", "pollinator", "mixed", "native", "plant", "plants",
    "planted", "planting", "grow", "growing", "tree", "trees", "bush", "bushes",
    "the", "and", "what", "where", "are", "is", "my", "in", "on", "have", "has",
    "any", "some", "much", "many", "how", "this", "that", "its", "you", "know",
    "list", "tell", "about", "exactly", "word", "records", "record",
}


def _depluralize(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and len(w) > 3:
        return w[:-1]
    return w


def garden_lookup(user_text: str) -> str:
    """Deterministic focused lookup: find the beds / zones / plants the user actually
    named and return their EXACT records — small, specific, and injected last (most
    recent) so the model relays them rather than confabulating a generic garden.
    Returns '' when nothing specific is named (the broad overview then carries it)."""
    text = " " + user_text.lower() + " "
    words = {_depluralize(w) for w in re.findall(r"[a-z]+", text)
             if len(w) >= 3 and w not in _GARDEN_STOP}
    with _conn() as c:
        rows = c.execute("SELECT name, attrs FROM entities WHERE kind='place'").fetchall()
    places = [(r["name"], json.loads(r["attrs"] or "{}")) for r in rows]

    def pname(p):
        return ((p.get("plant") if isinstance(p, dict) else str(p)) or "?")

    def pcount(p):
        ct = p.get("count") if isinstance(p, dict) else None
        return f" x{ct}" if ct else ""

    def plant_words(p):
        return {_depluralize(w) for w in re.findall(r"[a-z]+", pname(p).lower())
                if len(w) >= 3 and w not in _GARDEN_STOP}

    hits = []
    for name, a in places:
        place_named = name.lower() in text  # 'bed 1', 'blueberry guild', 'garden zone'
        matched_plants = [p for p in (a.get("plantings") or []) if plant_words(p) & words]
        if not (place_named or matched_plants):
            continue
        shown = (a.get("plantings") or []) if place_named else matched_plants
        loc = ", ".join(b for b in (a.get("zone"), a.get("yard")) if b)
        sensor = " (has soil sensor)" if a.get("sensor") else ""
        pl = ", ".join(pname(p) + pcount(p) for p in shown)
        hits.append(f"- {name}" + (f" [{loc}]" if loc else "") + sensor + (f": {pl}" if pl else ""))
        if len(hits) >= 14:
            break
    return "\n".join(hits)
