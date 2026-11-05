"""Receipts into inventory: the phone reads the receipt, code reads the lines, a person keeps them.

The app reads a receipt with Apple's on-device text recognition and sends only its lines (no
image leaves the phone). Code here finds the item lines, their counts and weights and the
receipt's date, and files each item as a proposal. Nothing reaches the inventory until a person
taps Keep on the Today page (receipts_api.py), the same gate as the note-taker's memories. No
model: an abbreviation the store printed ("BNLS CHKN BRST") stays as printed for the person to
fix with Edit, rather than a guess at what it meant.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import inventory_store as inv
import records_store as store

MAX_LINES = 400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt TEXT NOT NULL,              -- the app's id for the receipt; a resend replaces it
    store TEXT, bought TEXT,            -- from the receipt's own header and date line, if found
    name TEXT NOT NULL, qty REAL, unit TEXT,
    line TEXT NOT NULL,                 -- the receipt line it came from, shown on the card
    created_at TEXT NOT NULL);
"""
_ready: set[str] = set()


def _conn():
    c = store._conn()
    if str(store.DB_PATH) not in _ready:
        c.executescript(_SCHEMA)
        _ready.add(str(store.DB_PATH))
    return c


# --- reading a receipt ------------------------------------------------------------------------------

_PRICE = re.compile(r"(?:^|\s)-?\$?\d{1,5}[.,]\d{2}\s*(?:[A-Z]{1,2}|\*)?\s*$")
_SKIP = re.compile(
    r"\b(?:sub ?total|total|tax|vat|balance|change due|change|cash|tender\w*|visa|mastercard|"
    r"amex|discover|debit|credit|card|auth\w*|approv\w*|ref(?:erence)?|acct|account|chip|contactless|"
    r"thank|thanks|welcome|survey|return|refund|policy|cashier|register|reg\b|trans\w*|store ?#|"
    r"tel|phone|www|\.com|receipt|items? sold|item count|savings|you saved|saved|coupon|discount|"
    r"member|rewards?|points|loyalty|tip|gratuity|signature|customer|copy|price override)\b", re.I)
_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_QTY_AT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(lb|lbs|kg|oz)?\s*@\s*\$?\d", re.I)
_QTY_IN = re.compile(r"(?:\bqty\s*:?\s*(\d+)|\b(\d+)\s*[xX]\s+|\s[xX]\s*(\d+)\b)")
_SKU = re.compile(r"\b\d{5,}[A-Z]{0,2}\b")          # product codes, with a trailing tax flag
_FLAG = re.compile(r"(?:\s+[A-Z]{1,2})+$")              # tax and department flags after the name
_SIZE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(OZ|FL ?OZ|LB|LBS|PK|CT|CF|GAL|QT|PT|FT|IN|ML|L|KG|G)\b", re.I)


def _receipt_date(lines: list[str]) -> str | None:
    for ln in lines:
        m = _DATE.search(ln)
        if not m:
            continue
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            day = dt.date(y, mo, d)
        except ValueError:
            continue
        if dt.date(2000, 1, 1) <= day <= dt.date.today():
            return day.isoformat()
    return None


def _price(line: str) -> float | None:
    m = re.search(r"(-?\d{1,5})[.,](\d{2})\s*(?:[A-Z]{1,2}|\*)?\s*$", line)
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def _ocr_fixes(line: str) -> str:
    """Misreads that are never real receipt text: "160Z" is 16 OZ (the O read as a zero; 0Z is
    never a unit)."""
    return re.sub(r"(\d)0Z\b", r"\1 OZ", line)


def _clean_name(text: str) -> str:
    had_code = bool(_SKU.search(text))
    text = _SKU.sub(" ", text)
    text = _QTY_IN.sub(" ", text)
    text = re.sub(r"[#*]+", " ", text)
    text = " ".join(text.split()).strip(" -:.")
    if had_code:                         # "BNLS CHKN BRST 0078742 F": the F is a tax flag
        text = _FLAG.sub("", text)
    sizes = []
    text = _SIZE.sub(lambda m: sizes.append(f"{m.group(1)} {m.group(2).lower().replace(' ', '')}")
                     or f"\x00{len(sizes) - 1}\x00", text)
    text = text.title() if text.isupper() else text
    return re.sub("\x00(\\d+)\x00", lambda m: sizes[int(m.group(1))], text)


def parse(lines: list[str]) -> dict:
    """{store, bought, items: [{name, qty, unit, line}]} from a receipt's lines, top to bottom."""
    lines = [_ocr_fixes(" ".join(str(ln).split())) for ln in lines]
    lines = [ln for ln in lines if ln]
    store_name = next((ln for ln in lines[:4] if re.search(r"[A-Za-z]{3}", ln)
                       and not _DATE.search(ln) and not _PRICE.search(ln) and not _SKIP.search(ln)), None)
    items: list[dict] = []
    carry = None                         # a count line that the arithmetic gives to the item below
    for i, ln in enumerate(lines):
        at = _QTY_AT.match(ln)
        if at:                           # "3 @ 3.49" or "2.31 lb @ 0.54/lb", usually the line above's
            qty_at = float(at.group(1))
            unit_at = store.normalize_unit(at.group(2))[1] if at.group(2) else None
            each = re.search(r"@\s*\$?(\d+)[.,](\d{2})", ln)
            each = float(f"{each.group(1)}.{each.group(2)}") if each else None
            own_total = _price(ln.split("@", 1)[1])
            if each is not None and own_total == each:      # no total on this line: check the sums
                total = qty_at * each
                below = lines[i + 1] if i + 1 < len(lines) else ""
                below_price = _price(below) if below and not _QTY_AT.match(below) else None
                above_price = items[-1]["price"] if items else None
                # qty x price is the next line's price and not the last one's: it belongs below
                # ("2 @ 3.49" over "GARDEN TWINE 6.98", under "NEEM OIL 12.99").
                if below_price is not None and abs(below_price - total) < 0.015 \
                        and (above_price is None or abs(above_price - total) >= 0.015):
                    carry = (qty_at, unit_at, ln)
                    continue
            above = lines[i - 1] if i else ""
            if above and not _PRICE.search(above) and not _SKIP.search(above) \
                    and re.search(r"[A-Za-z]{2}", above) and above != store_name:
                items.append({"name": _clean_name(above), "qty": 1.0, "unit": None, "line": above, "price": None})
            if not items:
                continue
            items[-1]["qty"] = qty_at
            if unit_at:
                items[-1]["unit"] = unit_at
            items[-1]["line"] += " / " + ln
            continue
        if not _PRICE.search(ln) or _SKIP.search(ln) or ln == store_name:
            continue
        body = _PRICE.sub("", ln).strip()
        if not re.search(r"[A-Za-z]{2}", body):
            continue
        qty = 1.0
        m = _QTY_IN.search(body)
        if m:
            qty = float(next(g for g in m.groups() if g))
        at_inline = re.match(r"^\s*(\d+)\s*@\s*\$?\d+[.,]\d{2}\s*", body)
        if at_inline:
            qty, body = float(at_inline.group(1)), body[at_inline.end():]
        name = _clean_name(body)
        if len(name) < 2:
            continue
        item = {"name": name, "qty": qty, "unit": None, "line": ln, "price": _price(ln)}
        if carry:
            item["qty"], item["unit"] = carry[0], carry[1]
            item["line"] = carry[2] + " / " + ln
            carry = None
        items.append(item)
    merged: dict[str, dict] = {}         # the same item rung up twice is one proposal
    for it in items:
        key = (it["name"].lower(), it["unit"])
        if key in merged:
            merged[key]["qty"] += it["qty"]
            merged[key]["line"] += " / " + it["line"]
        else:
            merged[key] = dict(it)
    if store_name and store_name.isupper():
        store_name = store_name.title()
    return {"store": store_name, "bought": _receipt_date(lines), "items": list(merged.values())}


# --- proposals ------------------------------------------------------------------------------------------

def file_receipt(receipt_id: str, lines: list) -> dict:
    """Read a receipt and file its items as proposals. The same receipt id again replaces the
    proposals still waiting from it (a rescan). Returns what was read."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{7,63}", receipt_id or ""):
        raise ValueError("id must be the app's id for the receipt (8 to 64 letters, digits, dashes)")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES \
            or not all(isinstance(ln, str) for ln in lines):
        raise ValueError(f"lines must be a list of 1 to {MAX_LINES} strings")
    got = parse(lines)
    now = store._now()
    with _conn() as c:
        c.execute("DELETE FROM receipt_items WHERE receipt = ?", (receipt_id,))
        c.executemany(
            "INSERT INTO receipt_items (receipt, store, bought, name, qty, unit, line, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(receipt_id, got["store"], got["bought"], it["name"], it["qty"], it["unit"], it["line"], now)
             for it in got["items"]])
    return {"id": receipt_id, "store": got["store"], "bought": got["bought"],
            "items": [{"name": it["name"], "qty": it["qty"], "unit": it["unit"]} for it in got["items"]]}


def match_for(name: str) -> dict | None:
    """The item already on record that a receipt line names: one whose every word is in the line
    ("Neem oil" for "NEEM OIL 16OZ"), the longest such name if several. None if none."""
    words = set(re.findall(r"[a-z0-9]+", name.lower()))
    best = None
    with store._conn() as c:
        for r in c.execute("SELECT * FROM entities WHERE kind = ?", (inv.KIND,)):
            mine = set(re.findall(r"[a-z0-9]+", r["name"].lower()))
            if mine and mine <= words and (best is None or len(r["name"]) > len(best["name"])):
                best = store._row_entity(r)
    return best


def pending() -> list[dict]:
    """Every proposal waiting, receipt by receipt, newest receipt first, each with the item on
    record it would add to."""
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM receipt_items ORDER BY created_at DESC, id")]
    for r in rows:
        m = match_for(r["name"])
        r["adds_to"] = m["name"] if m else None
    return rows


def _amount(qty, unit) -> str:
    if qty is None:
        return "some"
    return f"{qty:g} {unit}" if unit else f"{qty:g}"


def keep(pid: int, name: str | None = None, amount: str | None = None) -> str:
    """Put one proposal in the inventory, as read or as the person edited it. It adds to the item
    on record it matches, dated by the receipt."""
    with _conn() as c:
        r = c.execute("SELECT * FROM receipt_items WHERE id = ?", (pid,)).fetchone()
    if not r:
        raise LookupError(f"no receipt item {pid} (already reviewed?)")
    if name is not None:
        name = " ".join(str(name).split())
        if not name or len(name) > 120:
            raise ValueError("Give the item a name (up to 120 characters), or use Not this.")
    target = name or r["name"]
    if name is None and (m := match_for(r["name"])):
        target = m["name"]
    said = amount if amount is not None and str(amount).strip() else _amount(r["qty"], r["unit"])
    out = inv.bought(target, said, when=r["bought"])
    if out.startswith("Error"):
        raise ValueError(out)
    with _conn() as c:
        c.execute("DELETE FROM receipt_items WHERE id = ?", (pid,))
    return out


def discard(pid: int) -> None:
    with _conn() as c:
        if not c.execute("DELETE FROM receipt_items WHERE id = ?", (pid,)).rowcount:
            raise LookupError(f"no receipt item {pid} (already reviewed?)")


def keep_receipt(receipt_id: str) -> int:
    """Keep every item still waiting from one receipt, as read. Returns how many."""
    with _conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM receipt_items WHERE receipt = ?", (receipt_id,))]
    for pid in ids:
        keep(pid)
    return len(ids)
