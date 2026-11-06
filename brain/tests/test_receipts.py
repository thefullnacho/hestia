"""receipts: the phone reads a receipt, code reads its lines, a person keeps each item. No model.

Pinned: item lines are found and totals, tax and payment lines are not; a count or weight on the
line below belongs to the item above; the same item rung up twice is one proposal; the receipt's
own date dates the purchase; Keep adds to the item already on record; and nothing reaches the
inventory without a person.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import hestia
import inventory_store as inv
import phone
import receipts

FARM = """TRACTOR SUPPLY CO
Store #1234 Asheville NC
Tel 828-555-0100
11/03/2026 10:42 AM
NEEM OIL 16OZ 041234567890 12.99 T
MULCH BLACK 2CF
  3 @ 3.49            10.47 T
GARDEN TWINE 2PK      5.99 T
SUBTOTAL              37.45
TAX 7.00%              2.62
TOTAL                 40.07
VISA ************1234 40.07
ITEMS SOLD 5
THANK YOU FOR SHOPPING""".splitlines()

GROCERY = """Walmart
ST# 01234 OP# 00001 TE# 12
BNLS CHKN BRST 007874212345 F 8.97 N
BANANAS 000000004011KF
 2.31 lb @ 0.54 /lb   1.25 N
GV WHOLE MILK 007874235678 3.48 N
GV WHOLE MILK 007874235678 3.48 N
SUBTOTAL 17.18
TOTAL 18.30
DEBIT TEND 18.30
CHANGE DUE 0.00
10/28/26 14:22:05""".splitlines()
RID = "A1B2C3D4-0000-4000-8000-000000000001"
H = {"X-Hestia-Inventory": "1", "Content-Type": "application/json"}


def names(got):
    return [(i["name"], i["qty"], i["unit"]) for i in got["items"]]


def test_a_farm_store_receipt():
    got = receipts.parse(FARM)
    assert got["store"] == "Tractor Supply Co" and got["bought"] == "2026-11-03"
    assert names(got) == [("Neem Oil 16 oz", 1, None), ("Mulch Black 2 cf", 3, None),
                          ("Garden Twine 2 pk", 1, None)]


def test_a_grocery_receipt_with_weights_codes_and_a_repeat():
    got = receipts.parse(GROCERY)
    assert got["bought"] == "2026-10-28"
    assert names(got) == [("Bnls Chkn Brst", 1, None), ("Bananas", 2.31, "lb"), ("Gv Whole Milk", 2, None)]


def test_no_totals_tax_or_payment_lines_become_items():
    for r in (FARM, GROCERY):
        assert not any(w in i["name"].lower() for i in receipts.parse(r)["items"]
                       for w in ("total", "tax", "visa", "debit", "change", "thank"))


@pytest.fixture
def box(db):
    token = phone.add_device("sister-iphone")
    return {"client": TestClient(hestia.app), "auth": {"Authorization": f"Bearer {token}"}}


def file(box, lines=FARM, rid=RID, auth=True):
    return box["client"].post("/v1/phone/receipts", content=json.dumps({"id": rid, "lines": lines}),
                              headers=box["auth"] if auth else {})


def test_the_phone_files_it_and_nothing_reaches_the_inventory_yet(box):
    assert file(box, auth=False).status_code == 401
    r = file(box)
    assert r.status_code == 200 and len(r.json()["items"]) == 3
    assert inv.find_item("mulch black 2 cf") is None
    assert len(receipts.pending()) == 3
    assert file(box).status_code == 200 and len(receipts.pending()) == 3     # a resend replaces
    assert file(box, lines="one string").status_code == 400
    assert file(box, rid="../x").status_code == 400


def test_keep_adds_to_the_item_on_record_dated_by_the_receipt(box):
    inv.bought("neem oil", "1 bottle", when="2026-06-01")
    file(box)
    neem = next(p for p in receipts.pending() if p["name"].startswith("Neem"))
    assert neem["adds_to"] == "neem oil"
    r = box["client"].post(f"/api/inventory/receipt-items/{neem['id']}/keep", content="{}", headers=H)
    assert r.status_code == 200 and r.json()["left"] == 2
    assert "2 bottles of neem oil" in inv.lookup("neem oil")
    with __import__("records_store")._conn() as c:
        last = json.loads(c.execute("SELECT attrs FROM events WHERE kind='stock' ORDER BY id DESC").fetchone()[0])
    assert last["bought"] == "2026-11-03" and not last.get("estimated")


def test_edit_keeps_it_in_the_persons_words(box):
    file(box, GROCERY)
    chicken = next(p for p in receipts.pending() if p["name"].startswith("Bnls"))
    r = box["client"].post(f"/api/inventory/receipt-items/{chicken['id']}/keep",
                           content=json.dumps({"name": "chicken breast", "amount": "2 lb"}), headers=H)
    assert r.status_code == 200
    assert "2 lb of chicken breast" in inv.lookup("chicken breast")


def test_not_this_and_keep_all(box):
    file(box)
    first = receipts.pending()[0]
    assert box["client"].post(f"/api/inventory/receipt-items/{first['id']}/discard", content="{}", headers=H).json()["left"] == 2
    r = box["client"].post(f"/api/inventory/receipts/{RID}/keep-all", content="{}", headers=H)
    assert r.status_code == 200 and r.json()["result"] == 2 and r.json()["left"] == 0
    assert inv.find_item("mulch black 2 cf") and inv.find_item("garden twine 2 pk")


def test_the_guard_stops_writes_not_from_the_app(box):
    file(box)
    pid = receipts.pending()[0]["id"]
    for headers in ({"Content-Type": "application/json"}, {**H, "Origin": "http://elsewhere.example"}):
        assert box["client"].post(f"/api/inventory/receipt-items/{pid}/keep", content="{}", headers=headers).status_code == 409
    assert box["client"].post(f"/api/inventory/receipts/{RID}/keep-all", content="{}",
                              headers={"Content-Type": "application/json"}).status_code == 409
    assert len(receipts.pending()) == 3 and inv.find_item("garden twine 2 pk") is None
    assert box["client"].post("/api/inventory/receipt-items/999/keep", content="{}", headers=H).status_code == 404


def test_ocr_reading_an_o_as_a_zero_and_a_count_line_that_belongs_below():
    """From the app's first simulator run: Vision read 16OZ as 160Z, and a bare '2 @ 3.49' sat
    between two items. 2 x 3.49 is the twine's 6.98, not the neem oil's 12.99, so it is the twine's."""
    got = receipts.parse(["TRACTOR SUPPLY CO", "11/03/2026 10:42 AM", "NEEM OIL 160Z 12.99 T",
                          "2 @ 3.49", "GARDEN TWINE 6.98 T", "TOTAL 19.97"])
    assert names(got) == [("Neem Oil 16 oz", 1, None), ("Garden Twine", 2, None)]


def test_a_count_line_with_its_own_total_stays_with_the_item_above():
    got = receipts.parse(["NEEM OIL 12.99 T", "2 @ 3.49 6.98 T", "GARDEN TWINE 6.98 T"])
    assert names(got) == [("Neem Oil", 2, None), ("Garden Twine", 1, None)]


def test_samples_are_kept_only_when_asked(box, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    pieces = [{"page": 0, "text": "NEEM OIL", "box": [0.1, 0.8, 0.3, 0.02]},
              {"page": 0, "text": "12.99", "box": [0.8, 0.79, 0.1, 0.02]}, {"text": 5}, "junk"]
    body = {"id": RID, "lines": FARM, "pieces": pieces, "source": "photo"}
    post = lambda: box["client"].post("/v1/phone/receipts", content=json.dumps(body), headers=box["auth"])
    monkeypatch.delenv("HESTIA_RECEIPT_SAMPLES", raising=False)
    assert post().status_code == 200
    assert not (tmp_path / "receipt-samples").exists()
    monkeypatch.setenv("HESTIA_RECEIPT_SAMPLES", "1")
    assert post().status_code == 200
    kept = json.loads((tmp_path / "receipt-samples" / f"{RID}.json").read_text())
    assert kept["source"] == "photo" and kept["lines"] == FARM
    assert [p["text"] for p in kept["pieces"]] == ["NEEM OIL", "12.99"]
    assert kept["filed"]["items"]
