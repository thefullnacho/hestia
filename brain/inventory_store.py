"""Household inventory: what the house has, how much, how old, and where it was last noted.

Items are records entities (kind "thing", the same rows the roster already holds) and every
change is an event (kind "stock"), so nothing is overwritten and the history explains every
number. No model: amounts are read by code, the age warning is a table, and an amount nobody
gave stays unknown instead of being guessed (INVENTORY.md).

Amounts come in three kinds. A number ("2 bags", "1.5 lb"). A fuzzy count that is still a
number ("a couple" is 2, "a few" is 3, "a handful" about 5), kept as approximate so Hestia says
"about 3". And a vague amount ("some", "a bit") that is a real fact, the house has it, but no
count: stored as said, never turned into a number.

Location is "last noted", not "is": things move and nobody updates a system for that, so it is
kept with the date it was noted and read back that way, never required.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import records_store as store

KIND = "thing"
EVENT = "stock"

# --- reading an amount ---------------------------------------------------------------------------

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "twenty": 20,
                 "a": 1, "an": 1, "single": 1, "half": 0.5}
# Fuzzy, but people mean a number by them. Kept approximate.
_FUZZY = [("half a dozen", 6), ("a dozen", 12), ("dozen", 12), ("a couple of", 2), ("a couple", 2),
          ("couple of", 2), ("couple", 2), ("a pair of", 2), ("a pair", 2), ("a few", 3), ("few", 3),
          ("several", 4), ("a handful of", 5), ("a handful", 5), ("handful", 5)]
# A real fact (the house has it) with no count in it. Never turned into a number.
_VAGUE = ("some", "a bit", "a little bit", "a little", "a lot", "lots", "plenty", "a bunch",
          "bunch", "a ton", "a fair amount", "not much", "a smidge", "enough", "a few left")
_UNIT_ALIASES = {"bags": "bag", "boxes": "box", "bottles": "bottle", "cans": "can", "jars": "jar",
                 "packs": "pack", "packets": "packet", "rolls": "roll", "cartons": "carton",
                 "jugs": "jug", "tubes": "tube", "sacks": "sack", "bales": "bale", "cases": "case",
                 "containers": "container", "spools": "spool", "sheets": "sheet", "pairs": "pair",
                 "dozens": "dozen"}
_NUM = re.compile(r"^(\d+/\d+|\d+(?:\.\d+)?)\b\s*(.*)$")


def parse_amount(said: str | None) -> dict:
    """{qty, unit, approx, vague, said} from words as spoken. qty is None when no count was
    given (vague, or nothing at all); unit None means a plain count."""
    text = " ".join(str(said or "").lower().replace(",", " ").split())
    out = {"qty": None, "unit": None, "approx": False, "vague": False, "said": str(said or "").strip()}
    if not text:
        return out
    text = re.sub(r"^(?:about|around|roughly|maybe|approximately|approx\.?|like|~)\s+", "", text)
    out["approx"] = text != " ".join(str(said or "").lower().replace(",", " ").split())
    rest = text
    m = _NUM.match(text)
    if m:
        n = m.group(1)
        out["qty"] = float(n.split("/")[0]) / float(n.split("/")[1]) if "/" in n else float(n)
        rest = m.group(2)
    else:
        for phrase, n in _FUZZY:
            if text == phrase or text.startswith(phrase + " "):
                out["qty"], out["approx"], rest = float(n), True, text[len(phrase):].strip()
                break
        else:
            for phrase in sorted(_VAGUE, key=len, reverse=True):
                if text == phrase or text.startswith(phrase + " "):
                    out["vague"], rest = True, text[len(phrase):].strip()
                    break
            else:
                first, _, tail = text.partition(" ")
                if first in _NUMBER_WORDS:
                    out["qty"], rest = float(_NUMBER_WORDS[first]), tail
                else:
                    out["vague"] = True     # words with no count in them: keep them as said
                    return out
    rest = re.sub(r"^(?:of|in)\s+(?:a\s+|the\s+)?", "", rest).strip()
    unit = rest.split(" ")[0] if rest else ""
    if unit:
        cls, canon = store.normalize_unit(unit)
        out["unit"] = None if cls == "count" else (_UNIT_ALIASES.get(canon, canon) if cls == "other" else canon)
    return out


def _fmt_qty(q: float) -> str:
    return f"{q:g}"


_PLURAL = {u: plural for plural, u in _UNIT_ALIASES.items()}


def _amount(qty: float, unit: str | None) -> str:
    """'2 bags', '1 bag', '1.5 lb': container words take a plural, measures don't."""
    if not unit:
        return _fmt_qty(qty)
    return f"{_fmt_qty(qty)} {_PLURAL.get(unit, unit) if qty != 1 and unit in _PLURAL else unit}"


# --- how long things keep: a table, not a judgment --------------------------------------------------

# (category, words that put an item in it, years before its age is worth a warning). The first
# match wins; an item that matches none gets no age warning rather than a guessed one.
SHELF_LIFE = [
    ("seeds", r"\bseeds?\b|\bseed packets?\b", 3),
    ("medicine", r"\b(?:medicine|ibuprofen|acetaminophen|tylenol|advil|aspirin|antibiotic\w*|"
                 r"pills?|tablets?|capsules?|ointment|antihistamine\w*|benadryl)\b", 2),
    ("garden chemical", r"\b(?:neem|insecticid\w*|pesticid\w*|fungicid\w*|herbicid\w*|weed killer|"
                        r"spinosad|bt\b|copper (?:spray|fungicide)|sulfur|sulphur|"
                        r"insecticidal soap|horticultural oil|dormant oil|diatomaceous)", 2),
    ("fertilizer", r"\b(?:fertili[sz]er|bone meal|blood meal|fish emulsion|kelp|potash|lime\b)", 4),
    ("batteries", r"\bbatter(?:y|ies)\b", 5),
    ("canned food", r"\b(?:canned|cans? of|tinned)\b", 2),
    ("pantry", r"\b(?:flour|rice|oats|beans|pasta|sugar|yeast|baking (?:soda|powder)|spices?|cereal|"
               r"coffee|tea|nuts|crackers)\b", 1),
]


def category_of(name: str) -> tuple[str | None, int | None]:
    for cat, pattern, years in SHELF_LIFE:
        if re.search(pattern, name, re.I):
            return cat, years
    return None, None


# --- the stock of one item, replayed from its events --------------------------------------------------

def _day(ts: str) -> dt.date:
    return dt.date.fromisoformat(ts[:10])


def stock(item: dict, events: list[dict]) -> dict:
    """Replay an item's events, oldest first, into what is on hand. Lots are used oldest first,
    so the age that matters is the oldest purchase still in stock."""
    lots: list[dict] = []      # {"qty": float | None, "date": date, "estimated": bool, "approx": bool}
    unit = item["attrs"].get("unit")
    unit_note = None
    for e in events:
        a = e["attrs"]
        op, qty, eunit = a.get("op"), a.get("qty"), a.get("unit")
        when = _day(a.get("bought") or e["ts"])
        mismatch = qty is not None and unit is not None and eunit is not None and eunit != unit
        if mismatch:
            unit_note = f"one entry was in {eunit}, not {unit}, so the count is uncertain"
        if op == "bought":
            lots.append({"qty": None if mismatch else qty, "date": when,
                         "estimated": bool(a.get("estimated")), "approx": bool(a.get("approx"))})
        elif op == "used_up":
            lots = []
        elif op == "used":
            if qty is None or mismatch:
                for lot in lots:            # an amount used that we can't subtract: counts go fuzzy
                    lot["qty"] = None
                continue
            left = qty
            for lot in lots:
                if lot["qty"] is None:
                    continue
                take = min(lot["qty"], left)
                lot["qty"] -= take
                left -= take
                lot["approx"] = lot["approx"] or bool(a.get("approx"))
            lots = [lot for lot in lots if lot["qty"] is None or lot["qty"] > 1e-9]
        elif op == "counted":
            oldest = min((lot["date"] for lot in lots), default=when)
            est = any(lot["estimated"] for lot in lots) if lots else True
            lots = [] if qty == 0 else [{"qty": None if mismatch else qty, "date": oldest,
                                         "estimated": est, "approx": bool(a.get("approx"))}]
    known = [lot["qty"] for lot in lots if lot["qty"] is not None]
    some = any(lot["qty"] is None for lot in lots)
    oldest = min(lots, key=lambda lot: lot["date"]) if lots else None
    return {"have": bool(lots), "qty": sum(known) if known else None, "unit": unit,
            "approx": any(lot["approx"] for lot in lots) or (some and bool(known)), "some": some,
            "oldest": oldest["date"] if oldest else None,
            "oldest_estimated": oldest["estimated"] if oldest else False, "unit_note": unit_note}


def _events(c, entity_id: int) -> list[dict]:
    rows = c.execute("SELECT ts, attrs FROM events WHERE entity_id=? AND kind=? ORDER BY ts, id",
                     (entity_id, EVENT)).fetchall()
    return [{"ts": r["ts"], "attrs": json.loads(r["attrs"] or "{}")} for r in rows]


# --- finding an item ----------------------------------------------------------------------------------

def find_item(name: str) -> dict | None:
    """An item by its name or an alias, then by its words ("neem" finds "Neem oil"). None when
    nothing matches or several do: the caller asks rather than guessing."""
    return store.resolve(name, kind=KIND, fuzzy=True)


_PREPOSITION = re.compile(r"^(?:in|on|at|by|under|behind|beside|near|next to|inside|above|below|on top of)\b", re.I)


def _at(loc: str) -> str:
    """'in the shed', or 'by the shed' as said: never 'in by the shed'."""
    return loc if _PREPOSITION.match(loc) else f"in {loc}"


def _say(name: str, s: dict) -> str:
    if not s["have"]:
        return f"no {name} on hand"
    if s["qty"] is None:
        return f"some {name} (no count given)"
    about = "about " if s["approx"] else ""
    extra = ", and some more uncounted" if s["some"] and s["qty"] is not None else ""
    joint = " of " if s["unit"] else " "          # "2 bags of mulch", but "3 Mulch Black 2 cf"
    return f"{about}{_amount(s['qty'], s['unit'])}{joint}{name}{extra}"


def _age_note(name: str, s: dict, item: dict, today: dt.date) -> str:
    cat = item["attrs"].get("category")
    years = None
    if cat:
        years = next((y for c, _, y in SHELF_LIFE if c == cat), None)
    else:
        cat, years = category_of(name)
    if not s["oldest"] or not years:
        return ""
    age = (today - s["oldest"]).days / 365.25
    if age < years:
        return ""
    since = (f"dates from about {s['oldest'].year}" if s["oldest_estimated"]
             else f"was bought {s['oldest']:%B %Y}")
    return (f" Old stock: the oldest {since}, about {age:.0f} years ago, and {cat} usually keep about "
            f"{years} years. It may have lost strength.")


def _where(item: dict) -> str:
    loc, noted = item["attrs"].get("location"), item["attrs"].get("location_noted")
    if not loc:
        return ""
    return f" Last noted {_at(loc)}" + (f" on {dt.date.fromisoformat(noted):%b %-d, %Y}" if noted else "") + "."


# --- changes --------------------------------------------------------------------------------------------

def _log(name: str, op: str, amount: dict | None, location: str | None, bought: str | None = None,
         estimated: bool = False, create: bool = False) -> tuple[dict, dict]:
    item = find_item(name)
    if item is None:
        if not create:
            raise LookupError(name)
        attrs = {}
        if amount and amount.get("unit"):
            attrs["unit"] = amount["unit"]
        cat, _ = category_of(name)
        if cat:
            attrs["category"] = cat
        item = store.upsert_entity(KIND, name.strip(), attrs=attrs)
    elif amount and amount.get("unit") and not item["attrs"].get("unit") and op in ("bought", "counted"):
        item = store.upsert_entity(KIND, item["name"], attrs={"unit": amount["unit"]})
    if amount and amount.get("qty") is not None and not amount.get("unit") and item["attrs"].get("unit"):
        amount = {**amount, "unit": item["attrs"]["unit"]}   # "used a couple" of a thing kept in bags
    attrs = {"op": op}
    if amount:
        attrs.update({k: amount[k] for k in ("qty", "unit", "approx", "said")})
    if bought:
        attrs["bought"] = bought
    if estimated:
        attrs["estimated"] = True
    store.log_event(EVENT, subject=item["name"], action=op, detail=(amount or {}).get("said"),
                    attrs=attrs, subject_kind=KIND, strict_subject=True)
    if location:
        item = store.upsert_entity(KIND, item["name"], attrs={
            "location": location.strip(), "location_noted": dt.date.today().isoformat()})
    return item, attrs


def status_of(item: dict, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    with store._conn() as c:
        s = stock(item, _events(c, item["id"]))
    return _say(item["name"], s) + "." + (s["unit_note"] and f" ({s['unit_note']})." or "") \
        + (_age_note(item["name"], s, item, today) if s["have"] else "") + _where(item)


def bought(name: str, amount: str | None = None, location: str | None = None,
           when: str | None = None) -> str:
    a = parse_amount(amount)
    day, estimated = (dt.date.today().isoformat(), True)
    if when:
        when = when.strip()
        if re.fullmatch(r"(?:19|20)\d\d", when):          # "seeds from 2021": the year, mid-year, rough
            day = f"{when}-07-01"
        elif re.fullmatch(r"(?:19|20)\d\d-\d\d", when):
            day = f"{when}-15"
        else:
            try:
                dt.date.fromisoformat(when)
            except ValueError:
                return f"Error: I couldn't read the date '{when}'; use YYYY-MM-DD or a year, or leave it out."
            day, estimated = when, False
    item, attrs = _log(name, "bought", a, location, bought=day, estimated=estimated, create=True)
    if a["qty"] is None:
        said = f", amount as said: '{a['said']}'" if a["said"] else ", no amount given"
        return f"Logged {item['name']}{said}. Now: {status_of(item)}"
    got = f"{'about ' if a['approx'] else ''}{_amount(a['qty'], attrs.get('unit'))}"
    return f"Logged {got} of {item['name']}. Now: {status_of(item)}"


def used(name: str, amount: str | None = None) -> str:
    said = (amount or "").strip().lower()
    all_of_it = said in ("all", "all of it", "the last", "the last of it", "last", "everything", "up")
    a = None if all_of_it else parse_amount(amount)
    try:
        item, attrs = _log(name, "used_up" if all_of_it else "used", a, None)
    except LookupError:
        return (f"I have no {name} on record, so there is nothing to take it from. If the house has "
                f"some, say how much and I'll add it.")
    if all_of_it:
        return f"Logged the last of {item['name']} as used up. Now: {status_of(item)}"
    if a["qty"] is None:
        return f"Logged some {item['name']} used. Now: {status_of(item)}"
    got = f"{'about ' if a['approx'] else ''}{_amount(a['qty'], attrs.get('unit'))}"
    return f"Logged {got} of {item['name']} used. Now: {status_of(item)}"


def counted(name: str, amount: str) -> str:
    a = parse_amount(amount)
    if a["qty"] is None and not a["vague"]:
        return "Error: say how much is there, for example '3 bags', 'a couple', 'some' or 'none'."
    if (amount or "").strip().lower() in ("none", "zero", "0", "nothing"):
        a = {**a, "qty": 0.0, "vague": False}
    item, _ = _log(name, "counted", a, None, create=True)
    return f"Logged the count for {item['name']}. Now: {status_of(item)}"


def moved(name: str, location: str) -> str:
    item = find_item(name)
    if item is None:
        return f"I have no {name} on record. Add it first, with where it is."
    store.upsert_entity(KIND, item["name"], attrs={"location": location.strip(),
                                                   "location_noted": dt.date.today().isoformat()})
    return f"Noted: {item['name']} is {_at(location.strip())} now."


def lookup(name: str) -> str:
    item = find_item(name)
    if item is None:
        return f"Nothing called {name} is on record."
    with store._conn() as c:
        if not _events(c, item["id"]):
            return f"{item['name']} is on record, but no amount was ever logged.{_where(item)}"
    return status_of(item)


def overview(location: str | None = None, old_only: bool = False, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    with store._conn() as c:
        rows = c.execute("SELECT * FROM entities WHERE kind=? ORDER BY name COLLATE NOCASE", (KIND,)).fetchall()
        items = [store._row_entity(r) for r in rows]
        lines = []
        for item in items:
            evs = _events(c, item["id"])
            if not evs:
                continue
            s = stock(item, evs)
            if not s["have"]:
                continue
            loc = (item["attrs"].get("location") or "").lower()
            if location and location.lower() not in loc:
                continue
            age = _age_note(item["name"], s, item, today)
            if old_only and not age:
                continue
            lines.append("- " + _say(item["name"], s) + (f" (last noted {_at(item['attrs']['location'])})"
                                                          if item["attrs"].get("location") else "")
                         + (" [old stock]" if age else ""))
    if not lines:
        return ("Nothing old on hand." if old_only else
                f"Nothing on record{' in ' + location if location else ''}.")
    return "\n".join(lines)


# --- what is on hand for a question (the library's grounded answers) ----------------------------------

_SIZE_WORDS = {"oz", "fl", "lb", "lbs", "pk", "ct", "cf", "gal", "qt", "pt", "ft", "in", "ml", "l", "kg", "g",
               "pack", "count", "bag", "bottle", "jar", "box", "can", "the", "and", "of", "for", "with"}


def _stem(word: str) -> str:
    return re.sub(r"(?:es|s)$", "", word) if len(word) > 3 else word


def _name_words(name: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", name.lower()) if w not in _SIZE_WORDS and len(w) > 1}


def on_hand_for(texts: list[str], today: dt.date | None = None) -> list[str]:
    """Items on hand whose every name word appears in `texts` (passages a search returned), one
    line each with how much, how old and where last noted. Strict on purpose: telling someone
    they have a thing they don't is worse than missing one ("Neem Oil 16 oz" matches a passage
    saying "neem oil"; "insect killing soap" does not match "insecticidal soap")."""
    today = today or dt.date.today()
    words = {_stem(w) for w in re.findall(r"[a-z]+", " ".join(texts).lower())}
    out = []
    with store._conn() as c:
        for r in c.execute("SELECT * FROM entities WHERE kind=? ORDER BY name COLLATE NOCASE", (KIND,)):
            item = store._row_entity(r)
            mine = _name_words(item["name"])
            if not mine or not mine <= words or (len(mine) == 1 and len(next(iter(mine))) < 4):
                continue
            evs = _events(c, item["id"])
            if not evs:
                continue
            s = stock(item, evs)
            if not s["have"]:
                continue
            out.append("- " + _say(item["name"], s) + "." + _age_note(item["name"], s, item, today)
                       + _where(item))
    return out


def has_any() -> bool:
    """Whether this house keeps an inventory at all (any amount ever logged)."""
    with store._conn() as c:
        return c.execute("SELECT 1 FROM events WHERE kind=? LIMIT 1", (EVENT,)).fetchone() is not None
