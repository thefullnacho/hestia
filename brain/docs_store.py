"""House docs: the household's own pages, drafted by the model, kept by a person (LIBRARY-DESIGN.md).

Three kinds: a guide on a subject made for this house, an account of something that happened, and
a note on something the house owns (the troubleshooting the manual doesn't have). The model only
proposes. Code checks what it can't be trusted with: the kind, that `about` is a real records
entity, and that every source and page anchor is a passage the library returned this turn. A
proposal waits on Today; Keep files it into the library under `house:<id>` and writes a one-way
`.md` copy to data/house-docs/ for reading with the box down. A revision replaces the doc it
revises in the index; the old text stays in the table.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import config
import library_store
import records_store as store

KINDS = ("guide", "account", "note")
MAX_TITLE = 120
MAX_BODY = 12_000
PAGE_CHARS = 3_000          # a long doc is split into "pages" of about this size for citing
SOURCE = "House docs"       # one source, so the two-per-source cap keeps them from crowding the manuals
REVIEW_DAYS = 365           # a kept doc older than this is due a "still true?" look (not built yet)
_CITE = re.compile(r"^(?P<title>.+?)(?:, (?P<year>\d{4}))?, p\.(?P<page>\d+)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS house_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    about TEXT, about_id INTEGER,
    page_cite TEXT, page_file TEXT, page INTEGER,
    sources TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed',   -- proposed, kept, dropped, replaced
    revises INTEGER,
    created TEXT NOT NULL, decided TEXT);
CREATE INDEX IF NOT EXISTS house_docs_by_status ON house_docs(status, id);
"""
_ready: set[str] = set()


def export_dir() -> Path:
    return config.DATA_DIR / "house-docs"


def _conn():
    c = store._conn()
    key = str(store.DB_PATH)
    if key not in _ready:
        c.executescript(_SCHEMA)
        _ready.add(key)
    return c


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _clean(text, limit: int, what: str, multiline: bool = False) -> str:
    if not isinstance(text, str):
        raise ValueError(f"{what} must be text")
    text = text.strip() if multiline else " ".join(text.split())
    if not text:
        raise ValueError(f"{what} is required")
    if len(text) > limit:
        raise ValueError(f"{what} is too long ({len(text)} characters, the limit is {limit})")
    return text


def _row(r) -> dict:
    d = dict(r)
    d["sources"] = json.loads(d.get("sources") or "[]")
    return d


def propose(kind: str, title: str, body: str, about: str | None = None, page: str | None = None,
            sources: list | None = None, allowed: list | None = None) -> dict:
    """Store a proposal. `allowed` is this turn's library citations: a source or page anchor the
    library didn't return is refused, so a doc can't cite a page it never saw."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    title = _clean(title, MAX_TITLE, "title")
    body = _clean(body, MAX_BODY, "body", multiline=True)
    allowed = [str(a) for a in (allowed or [])]
    sources = [" ".join(str(s).split()) for s in (sources or [])]
    for s in sources:
        if s not in allowed:
            raise ValueError(f"'{s}' is not a passage the library returned in this conversation turn; "
                             "cite only passages you were shown, or leave sources out")
    ent = None
    if about:
        ent = store.resolve(about)
        if not ent:
            raise ValueError(f"there's no record named '{about}'. Register it with records first, "
                             "or leave about out")
    if kind == "note" and not ent:
        # A plain name match, not a judgment: exactly one records name in the note's own words.
        # The person sees "about <name>" on the Today card before anything is kept.
        named = _entities_named(f"{title}\n{body}")
        if len(named) == 1:
            ent = named[0]
        else:
            hint = f" Candidates: {', '.join(e['name'] for e in named[:5])}." if named else ""
            raise ValueError("a note must be about something in the records: pass `about` with its "
                             f"name.{hint}")
    page_file = page_no = None
    if page:
        page = " ".join(str(page).split())
        m = _CITE.match(page)
        if page not in allowed or not m:
            raise ValueError(f"page '{page}' must be one of the library passages returned this turn, "
                             "as cited (title, year, p.N)")
        page_file, page_no = library_store.file_for_title(m["title"]), int(m["page"])
        if not page_file:
            raise ValueError(f"no library document is titled '{m['title']}'")
    with _conn() as c:
        prior = None
        if ent and kind == "note":
            prior = c.execute("SELECT id, title FROM house_docs WHERE status='kept' AND kind='note' "
                              "AND about_id=? ORDER BY id DESC LIMIT 1", (ent["id"],)).fetchone()
        if not prior:
            prior = c.execute("SELECT id, title FROM house_docs WHERE status='kept' AND kind=? "
                              "AND lower(title)=lower(?) ORDER BY id DESC LIMIT 1", (kind, title)).fetchone()
        pid = c.execute(
            "INSERT INTO house_docs (kind, title, body, about, about_id, page_cite, page_file, page, "
            "sources, revises, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, title, body, ent["name"] if ent else None, ent["id"] if ent else None,
             page, page_file, page_no, json.dumps(sources), prior["id"] if prior else None,
             _now())).lastrowid
    return {"id": pid, "revises": prior["title"] if prior else None}


def _entities_named(text: str) -> list[dict]:
    """Records entities whose name appears as whole words in the text, longest names first."""
    low = text.lower()
    rows = store._conn().execute("SELECT id, name, kind FROM entities").fetchall()
    hits = [dict(r) for r in rows if r["name"] and re.search(
        rf"(?<![\w]){re.escape(r['name'].lower())}(?![\w])", low)]
    return sorted(hits, key=lambda e: -len(e["name"]))


def pending() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM house_docs WHERE status='proposed' ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = _row(r)
            if d["revises"]:
                old = c.execute("SELECT title FROM house_docs WHERE id=?", (d["revises"],)).fetchone()
                d["revises_title"] = old["title"] if old else None
            out.append(d)
    return out


def get(doc_id: int) -> dict:
    r = _conn().execute("SELECT * FROM house_docs WHERE id=?", (int(doc_id),)).fetchone()
    if not r:
        raise LookupError(f"no house doc #{doc_id}")
    return _row(r)


def pages_of(body: str) -> list[str]:
    """Paragraphs gathered into pages of about PAGE_CHARS, so a long doc cites by page."""
    pages, cur = [], ""
    for para in re.split(r"\n\s*\n", body):
        if cur and len(cur) + len(para) > PAGE_CHARS:
            pages.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    return pages + ([cur] if cur else [])


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "doc"


def _export_path(d: dict) -> Path:
    return export_dir() / f"{_slug(d['title'])}-{d['id']}.md"


def _markdown(d: dict) -> str:
    head = [f"# {d['title']}", "",
            f"{d['kind'].capitalize()} · kept {d['decided'][:10]}"
            + (f" · about {d['about']}" if d.get("about") else "")
            + (f" · for {d['page_cite']}" if d.get("page_cite") else ""), ""]
    tail = ["", "Sources:"] + [f"- {s}" for s in d["sources"]] if d["sources"] else []
    note = ["", "_A copy written by Hestia when this doc was kept. Editing it changes nothing; "
                "the next version overwrites it._"]
    return "\n".join(head + [d["body"]] + tail + note) + "\n"


def keep(doc_id: int, title: str | None = None, body: str | None = None) -> str:
    """File a proposal into the library, as written or with the person's edits."""
    d = get(doc_id)
    if d["status"] != "proposed":
        raise ValueError(f"house doc #{doc_id} is already {d['status']}")
    title = _clean(title, MAX_TITLE, "title") if title is not None else d["title"]
    body = _clean(body, MAX_BODY, "body", multiline=True) if body is not None else d["body"]
    now = _now()
    with _conn() as c:
        c.execute("UPDATE house_docs SET title=?, body=?, status='kept', decided=? WHERE id=?",
                  (title, body, now, d["id"]))
        old = None
        if d["revises"]:
            old = c.execute("SELECT * FROM house_docs WHERE id=? AND status='kept'",
                            (d["revises"],)).fetchone()
            if old:
                c.execute("UPDATE house_docs SET status='replaced' WHERE id=?", (old["id"],))
    if old:
        library_store.remove_house_doc(old["id"])
        _export_path(_row(old)).unlink(missing_ok=True)
    d = get(d["id"])
    library_store.index_doc(f"{library_store.HOUSE_PREFIX}{d['id']}", pages_of(body),
                            fix={"title": f"House doc: {title}", "year": int(now[:4]), "source": SOURCE})
    note = ""
    try:
        library_store.embed_missing()
    except Exception as e:  # noqa: BLE001 — keyword search still finds it; the next ingest embeds it
        note = f" Meaning search waits for the embedder ({type(e).__name__}); keyword search finds it now."
    export_dir().mkdir(parents=True, exist_ok=True)
    _export_path(d).write_text(_markdown(d), encoding="utf-8")
    return f"Kept '{title}' into the library." + (f" It replaces '{old['title']}'." if old else "") + note


def discard(doc_id: int) -> None:
    d = get(doc_id)
    if d["status"] != "proposed":
        raise ValueError(f"house doc #{doc_id} is already {d['status']}")
    with _conn() as c:
        c.execute("UPDATE house_docs SET status='dropped', decided=? WHERE id=?", (_now(), d["id"]))


def notes_for_pages(anchors: list[tuple[str, int]]) -> list[dict]:
    """Kept notes anchored to any of these (file, page) pairs, for a library answer."""
    if not anchors:
        return []
    with _conn() as c:
        out = []
        for file, page in set(anchors):
            out += [_row(r) for r in c.execute(
                "SELECT * FROM house_docs WHERE status='kept' AND page_file=? AND page=? ORDER BY id",
                (file, page)).fetchall()]
    return out


def docs_about(entity_id: int) -> list[dict]:
    """Kept docs about one records entity, newest first, for a records lookup."""
    return [_row(r) for r in _conn().execute(
        "SELECT * FROM house_docs WHERE status='kept' AND about_id=? ORDER BY id DESC", (int(entity_id),))]
