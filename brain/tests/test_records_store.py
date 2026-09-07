"""Records store — the intricate, expensive-to-retrofit logic: lineage/litter math,
the kind-scoped mint guards, alias resolution, attr merging, and service-due derivation.
These are the paths a silent regression would quietly corrupt the real records on."""
from __future__ import annotations

import datetime as dt


# ----- entities: resolve + alias + attr-merge ------------------------------

def test_alias_resolves_exact_and_case_insensitive(db):
    db.upsert_entity("pet", "Momo", aliases=["the oldest dog"], attrs={"breed": "Lhasa Apso"})
    assert db.resolve("momo")["name"] == "Momo"
    assert db.resolve("THE OLDEST DOG")["name"] == "Momo"


def test_upsert_merges_attrs_not_replaces(db):
    db.upsert_entity("pet", "Momo", attrs={"breed": "Lhasa Apso"})
    e = db.upsert_entity("pet", "Momo", attrs={"dob": "2018-01-01"})
    assert e["attrs"] == {"breed": "Lhasa Apso", "dob": "2018-01-01"}
    # one canonical entity, not two
    assert db.resolve("momo")["id"] == e["id"]


def test_resolve_loose_is_substring(db):
    db.upsert_entity("place", "Beets Bed", attrs={})
    assert db.resolve("beets")["name"] == "Beets Bed"


def test_resolve_strict_kind_scoped_wont_cross_attach(db):
    # A real place whose name contains "Park"; a new wildlife "Park" must NOT grab it.
    db.upsert_entity("place", "Park/Orchard Zone", attrs={})
    assert db.resolve("Park", kind="species") is None          # strict: no cross-kind substring
    assert db.resolve("Park")["name"] == "Park/Orchard Zone"   # loose: still finds it


# ----- photo intake mint guard (strict_subject path) -----------------------

def test_attach_photo_mints_new_subject_not_false_attach(db):
    db.upsert_entity("place", "Park/Orchard Zone", attrs={})
    rec = db.attach_photo("Park", "/tmp/p.jpg", caption="a deer", domain="wildlife")
    park = db.resolve("Park", kind="species")
    assert park is not None and park["kind"] == "species"      # minted fresh as a species
    assert park["name"] == "Park"
    assert rec["kind"] == "photo"


# ----- lineage / litter math ----------------------------------------------

def test_add_birth_creates_pup_with_lineage(db):
    res = db.add_birth("Bo", dam="Momo", sire="Rex")
    assert res["pup"] == "Bo" and res["litter_size"] == 1
    prof = db.entity_profile("Bo")
    rels = {(r["rel"], r["other"]) for r in prof["relations"]}
    assert ("dam", "Momo") in rels and ("sire", "Rex") in rels


def test_same_day_same_parents_share_litter_size_derived(db):
    db.add_birth("Bo", dam="Momo", sire="Rex")
    db.add_birth("Lily", dam="Momo", sire="Rex")
    res = db.add_birth("Ziggy", dam="Momo", sire="Rex")
    assert res["litter_size"] == 3                              # derived from actual pups
    # dam's progeny total rolls up across her litter(s)
    assert db.entity_profile("Momo")["puppies_total"] == 3


def test_different_day_is_a_separate_litter(db):
    db.add_birth("Bo", dam="Momo", sire="Rex", born="2020-01-01T08:00:00")
    res = db.add_birth("Cleo", dam="Momo", sire="Rex", born="2021-05-05T08:00:00")
    assert res["litter_size"] == 1                              # new date -> new litter
    assert len(db.entity_profile("Momo")["litters"]) == 2


# ----- event log + service-due derivation ----------------------------------

def test_recent_events_filters_by_kind_and_subject(db):
    db.log_event("sighting", subject="Deer", action="observed", location="orchard")
    db.log_event("chore", subject="Mower", action="mowed")
    sightings = db.recent_events(kind="sighting")
    assert len(sightings) == 1 and sightings[0]["subject"] == "Deer"
    assert db.recent_events(subject="Mower")[0]["kind"] == "chore"


def test_due_assets_flags_overdue_only(db):
    db.upsert_entity("asset", "Furnace Filter", attrs={"interval_days": 30})
    db.upsert_entity("asset", "Smoke Alarm", attrs={"interval_days": 365})
    old = (dt.datetime.now() - dt.timedelta(days=40)).isoformat(timespec="seconds")
    recent = (dt.datetime.now() - dt.timedelta(days=5)).isoformat(timespec="seconds")
    db.log_event("chore", subject="Furnace Filter", action="replaced", ts=old)
    db.log_event("chore", subject="Smoke Alarm", action="tested", ts=recent)
    due = {d["name"] for d in db.due_assets()}
    assert "Furnace Filter" in due and "Smoke Alarm" not in due


def test_only_service_events_reset_the_clock(db):
    """A photo is not an oil change. The asset-domain photo intake files a `photo` event against
    the asset itself, so counting any event as service would let photographing the mower mark it
    maintained — and the same for an NFC use-tap, which is the whole point of tracking uses."""
    db.upsert_entity("asset", "Mower", attrs={"interval_days": 30})
    old = (dt.datetime.now() - dt.timedelta(days=40)).isoformat(timespec="seconds")
    db.log_event("chore", subject="Mower", action="changed the oil", ts=old)
    db.attach_photo("Mower", "/photos/asset/mower/today.jpg", domain="asset")
    db.log_event("note", subject="Mower", action="noted", detail="starts on the second pull")
    due = {d["name"]: d for d in db.due_assets()}
    assert "Mower" in due, "a photo and a note must not count as servicing the mower"
    assert due["Mower"]["days_since"] == 40   # the clock still reads from the oil change


def test_asset_never_serviced_is_due(db):
    db.upsert_entity("asset", "Gutters", attrs={"interval_days": 180})
    due = {d["name"]: d for d in db.due_assets()}
    assert "Gutters" in due and due["Gutters"]["last"] == "never"


def test_annual_service_stays_on_calendar_date(db):
    db.upsert_entity('asset', 'Seasonal machine', attrs={'annual_service_date': '10-01', 'annual_service_start_year': 2026})
    assert db.due_assets(dt.datetime(2026, 9, 30)) == []
    due = db.due_assets(dt.datetime(2026, 10, 1))
    assert due[0]['schedule'] == 'annually on October 1'
    assert db.due_assets(dt.datetime(2027, 1, 1))[0]['due_date'] == '2026-10-01'
    db.log_event('service', subject='Seasonal machine', ts='2026-10-03T12:00:00', subject_kind='asset')
    assert db.due_assets(dt.datetime(2026, 10, 4)) == []
    assert db.due_assets(dt.datetime(2027, 9, 30)) == []
    assert db.due_assets(dt.datetime(2027, 10, 1))[0]['due_date'] == '2027-10-01'


def test_annual_service_ignores_usage_and_accepts_early_service(db):
    db.upsert_entity('asset', 'Seasonal machine', attrs={'annual_service_date': '10-01', 'annual_service_start_year': 2026})
    db.log_event('use', subject='Seasonal machine', ts='2026-09-25T12:00:00', subject_kind='asset')
    assert db.due_assets(dt.datetime(2026, 10, 1))
    db.log_event('service', subject='Seasonal machine', ts='2026-09-28T12:00:00', subject_kind='asset')
    assert db.due_assets(dt.datetime(2026, 10, 1)) == []



def test_multiple_service_dates_require_separate_completions(db):
    db.upsert_entity('asset', 'Washer', attrs={
        'service_dates': ['01-04', '06-01'], 'service_schedule_start': '2026-09-07'})
    assert db.due_assets(dt.datetime(2027, 1, 3)) == []
    assert db.due_assets(dt.datetime(2027, 1, 4))[0]['due_date'] == '2027-01-04'
    db.log_event('service', subject='Washer', ts='2027-01-04T12:00:00', subject_kind='asset')
    assert db.due_assets(dt.datetime(2027, 5, 31)) == []
    assert db.due_assets(dt.datetime(2027, 6, 1))[0]['due_date'] == '2027-06-01'
    db.log_event('use', subject='Washer', ts='2027-06-02T12:00:00', subject_kind='asset')
    assert db.due_assets(dt.datetime(2028, 1, 3))[0]['due_date'] == '2027-06-01'
    db.log_event('service', subject='Washer', ts='2028-01-03T12:00:00', subject_kind='asset')
    assert db.due_assets(dt.datetime(2028, 1, 3)) == []
    assert db.due_assets(dt.datetime(2028, 1, 4))[0]['due_date'] == '2028-01-04'
