"""Seed the garden inventory into the records substrate (idempotent).

The hestia.db is gitignored, so THIS FILE is the versioned backup-of-record for the
garden layout (like the breeding roster lives in memory). Re-runnable: places upsert by
name, relations are de-duped, the demo note is only logged once.

Model: yard-half -> zone/area -> bed (all `place` entities, linked with `in`). Each
place carries its plantings as a `plantings` attr [{plant, count}], plus dims/sensor
where known. Individual plant entities are NOT pre-created — log_event mints them lazily
the first time you take a note about one (e.g. "thin the hot peppers").

Run:  cd brain && .venv/bin/python seed_garden.py
"""
from __future__ import annotations

import json
import sqlite3

import records_store as store

SENSOR = "sensor.unknown_device_soilmoisture"  # + channel number

# (plant, count) — count None means "mixed / uncounted". Names kept verbatim as given.
INVENTORY: dict = {
    "Backyard": {
        "Garden Zone": {"beds": {
            "Carrots Round Bed":   {"type": "round bed", "dims": "3'x3'x2'", "sensor": SENSOR + "2", "plantings": [("Carrots", 40)]},
            "Beets Round Bed":     {"type": "round bed", "dims": "3'x3'x2'", "sensor": SENSOR + "1", "plantings": [("Beets", 25)]},
            "Hot Peppers Round Bed": {"type": "round bed", "dims": "3'x3'x2'", "sensor": SENSOR + "7", "plantings": [("Hot Peppers", 23)], "note": "Might need to thin."},
            "Bed 1": {"type": "rectangle bed", "dims": "8'x4'x2'", "sensor": SENSOR + "8", "plantings": [("Purple Artichokes", 12), ("Sweet Peppers", 7), ("Tomatoes", 8)]},
            "Bed 2": {"type": "rectangle bed", "dims": "8'x4'x2'", "plantings": [("Cucumber Divas", 7), ("Buttercrunch Squash", 4), ("Zucchini", 6)]},
            "Bed 3": {"type": "rectangle bed", "dims": "8'x4'x2'", "sensor": SENSOR + "3", "plantings": [("Potatoes", 20), ("Skirret", 1), ("Snowpeas", 12)]},
            "Bed 4": {"type": "rectangle bed", "dims": "8'x4'x2'", "sensor": SENSOR + "6", "plantings": [("Tomatoes", 25)]},
            "In-Ground Bed": {"type": "in-ground bed", "plantings": [("Garlic", 12)]},
        }},
        "Back Porch Zone": {"plantings": [("Brown Turkey Fig", 1), ("Hostas", 5), ("Dahlia Mixed", 3), ("Hollyhocks", 5), ("Creeping Thyme", 3), ("Chicago Hardy Fig", 1)]},
        "Blueberry Guild": {"type": "guild", "plantings": [("Low Bush Blueberry", 1), ("High Bush Blueberry", 2), ("Honeyberry", 3), ("June-bearing Strawberries", 2), ("Sage", 3)]},
        "Pond Zone": {"note": "TBA", "plantings": []},
        "Strawberry Pyramids": {"plantings": [("Ever-bearing Strawberries", 23), ("Borage", 6)]},
        "Staging Zone": {"plantings": [("Sunchokes", 6), ("Tea Plant", 1), ("Bonfire Peaches", 2), ("Meyers Lemon", 1), ("Raspberry", 2), ("Mimosa", 1), ("Agrimoni", 1)],
                          "note": "Sunchokes are in a metal trough."},
        "Meadow Zone": {"beds": {
            "Bean Teepee": {"type": "structure", "plantings": [("Runner Beans", 22), ("Corn", 12), ("Snap Peas", 10)]},
            "Meadow Main": {"plantings": [("Asparagus Crowns", 3), ("Rhubarb", 1), ("Walking Onions", 6), ("Portulaca", 1), ("Rapunzel", 4)],
                             "note": "Portulaca is a germination experiment."},
        }},
    },
    "Frontyard": {
        "Park/Orchard Zone": {"beds": {
            "Apple Guild":      {"type": "guild", "plantings": [("Honeycrisp", 1), ("Gala", 1), ("Yarrow", 2), ("Comfrey", 3)]},
            "Peach/Plum Guild": {"type": "guild", "plantings": [("Alberta Peach", 2), ("Yarrow", 3), ("Plum", 1), ("Superior Plum", 1), ("Comfrey", 3), ("Dwarf Alberta Spruce", 1)],
                                  "note": "Dwarf Alberta Spruce is the living Christmas tree."},
            "Mulberry Guild":   {"type": "guild", "plantings": [("Mulberry", 1), ("Ever-bearing Strawberries", 12)]},
        }},
        "L-Shape Perennial Pollinator Bed": {"plantings": [("Russian Sage", 3), ("Hydrangea", 1), ("Lilac", 1), ("Rose of Sharon", 1), ("Butterfly Bush", 1), ("Fox Glove", 3), ("Tulips", 7), ("Hollyhocks", 5), ("Clematis", 1), ("German Chamomile", 7), ("Coneflowers", 7), ("Gladiolus Mixed", 7), ("Purple Crepe Myrtle", 1), ("Ornamental Grasses", 3), ("Hostas", 15)]},
        "Front Right Border Bed": {"plantings": [("Clematis", 2), ("Candytuft", 1), ("Peony", 1), ("Dahlia", 2), ("Hydrangea", 1), ("Climbing Rose", 1), ("Ornamental Grasses", 2), ("Blooming Lavender", 1), ("Sugar Plum Dianthus", 2), ("Boxwood Shrub", 1)]},
        "Front Right Side Path Bed": {"plantings": [("Hostas", 2), ("Iris", 12), ("Blue Aster", 1), ("Dwarf Mt. Laurel", 1), ("Red Hydrangea", 2), ("Blue Hydrangea", 1), ("Rosemary", 1), ("Dahlia", 4)]},
        "Sidewalk Border Beds": {"plantings": [("Popstar Hydrangea", 2), ("Heather", 6), ("Dwarf Butterfly Bush", 1), ("Euonymus Palamo Blanco", 2), ("Allium", 8)]},
        "Rose Bed": {"plantings": [("Roses", 4), ("Stonecrop", 3), ("Peony", 1), ("Scallions", 5), ("Clematis", 1)]},
        "Arch Beds": {"plantings": [("Runner Beans", 8), ("Snowpeas", 8), ("Mixed Flowers", None)]},
        "Rain Bed": {"plantings": [("Cherry Tree", 1), ("Iris", 10), ("Bearded Iris", 4), ("Alliums", 3), ("Beebalm", 1), ("Bleeding Hearts", 2), ("Creeping Juniper", 1)],
                      "note": "Creeping Juniper cultivar name TBD."},
        "Sideyard Guild": {"type": "guild", "plantings": [("Juliette Cherry Bush", 1), ("Hazelnut", 1), ("Gojiberry", 2), ("Lapins Cherry", 1), ("Mixed Native Pollinators", None), ("Thornless Blackberry", 1), ("Thorny Blackberry", 1)]},
        "Woodland Edge Guild": {"type": "guild", "plantings": [("Hazelnut", 1), ("Verbana", 1), ("Elderberry", 1), ("Serviceberry", 1), ("Chokeberry", 1)]},
    },
}


# Physical adjacency between areas (each chain = "progressively right next to each other").
ADJACENCY: list[list[str]] = [
    ["Garden Zone", "Meadow Zone"],
    ["Staging Zone", "Strawberry Pyramids", "Back Porch Zone", "Pond Zone", "Blueberry Guild"],
]


def _plantings(p: list) -> list[dict]:
    return [{"plant": name, "count": cnt} for name, cnt in p]


def relate_once(c: sqlite3.Connection, from_id: int, rel: str, to_id: int) -> None:
    exists = c.execute("SELECT 1 FROM relations WHERE from_id=? AND rel=? AND to_id=?",
                       (from_id, rel, to_id)).fetchone()
    if not exists:
        c.execute("INSERT INTO relations(from_id,rel,to_id,created_at) VALUES(?,?,?,?)",
                  (from_id, rel, to_id, store._now()))


def make_place(name: str, attrs: dict) -> int:
    e = store.upsert_entity("place", name, attrs=attrs)
    return e["id"]


def load() -> dict:
    n_zone = n_bed = n_plant = total_count = 0
    notes: list[tuple[str, str]] = []

    for yard, zones in INVENTORY.items():
        yard_id = make_place(yard, {"area_type": "yard"})
        for zone, zdata in zones.items():
            n_zone += 1
            beds = zdata.get("beds")
            zone_attrs = {"area_type": zdata.get("type", "zone"), "yard": yard}
            if "plantings" in zdata:
                zone_attrs["plantings"] = _plantings(zdata["plantings"])
            for k in ("dims", "sensor", "note"):
                if k in zdata:
                    zone_attrs[k] = zdata[k]
            zone_id = make_place(zone, zone_attrs)
            with store._conn() as c:
                relate_once(c, zone_id, "in", yard_id)
            if zdata.get("note"):
                notes.append((zone, zdata["note"]))
            if "plantings" in zdata:
                for pl in zdata["plantings"]:
                    n_plant += 1
                    total_count += pl[1] or 0

            for bed, bdata in (beds or {}).items():
                n_bed += 1
                battrs = {"area_type": bdata.get("type", "bed"), "yard": yard, "zone": zone,
                          "plantings": _plantings(bdata.get("plantings", []))}
                for k in ("dims", "sensor", "note"):
                    if k in bdata:
                        battrs[k] = bdata[k]
                bed_id = make_place(bed, battrs)
                with store._conn() as c:
                    relate_once(c, bed_id, "in", zone_id)
                if bdata.get("note"):
                    notes.append((bed, bdata["note"]))
                for pl in bdata.get("plantings", []):
                    n_plant += 1
                    total_count += pl[1] or 0

    # Physical adjacency between areas (one direction; profiles surface the reverse too).
    n_adj = 0
    with store._conn() as c:
        for chain in ADJACENCY:
            for a, b in zip(chain, chain[1:]):
                ea, eb = store.resolve(a, conn=c), store.resolve(b, conn=c)
                if ea and eb:
                    relate_once(c, ea["id"], "next_to", eb["id"]); n_adj += 1

    # Demo the note-taking the user asked for: log the Hot Peppers thinning note once.
    for subject, text in notes:
        with store._conn() as c:
            ent = store.resolve(subject, conn=c)
            dup = None
            if ent:
                dup = c.execute("SELECT 1 FROM events WHERE entity_id=? AND kind='note' AND detail=?",
                                (ent["id"], text)).fetchone()
        if not dup:
            store.log_event("note", subject=subject, action="noted", detail=text, subject_kind="place")

    return {"zones": n_zone, "beds": n_bed, "plantings": n_plant,
            "total_plants": total_count, "notes": len(notes), "adjacencies": n_adj}


if __name__ == "__main__":
    summary = load()
    print("Loaded garden inventory:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
