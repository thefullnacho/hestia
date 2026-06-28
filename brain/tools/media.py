"""`media` tool — manage the media stack (Sonarr/Radarr/Lidarr → qBittorrent/slskd/Plex).

Makes "grab the new season of X" / "find me <song>" real: look up a title, add it to
Sonarr (TV), Radarr (movies), or Lidarr (music), and start a download. Torrents go through
the VPN kill-switch; music also pulls from Soulseek via slskd. Adding/searching is
non-destructive; deletion is deliberately NOT exposed (would need the safety gate).

Music (Lidarr, API v1) works at the ALBUM level: a song request grabs the album it's on.

Movies are size-aware with a HARD 1080p CEILING (the Plex box doesn't do >1080p, and
disk is tight): never grab 4K. Rather than letting Radarr grab the "best" (a 35 GB
1080p REMUX by default), the tool picks a sensible 1080p encode and only ASKS when the
best match is a big 1080p REMUX with a much smaller alternative. The `quality` arg
overrides within 1080p: 'small' | '1080p' | 'best' (best = 1080p REMUX/highest).
"""
from __future__ import annotations

import os
import queue
import sys
import threading

import httpx


def _mlog(msg: str) -> None:
    """Trace background grabs to the brain's journal (the async add can't return them)."""
    print(f"[media] {msg}", file=sys.stderr, flush=True)

APPS = {
    "tv": {
        "base": os.environ.get("SONARR_URL", "http://hl-relay:8989").rstrip("/"),
        "key": os.environ.get("SONARR_KEY", ""),
        "lookup": "/api/v3/series/lookup", "add": "/api/v3/series",
        "id_field": "tvdbId", "noun": "series",
    },
    "movie": {
        "base": os.environ.get("RADARR_URL", "http://hl-relay:7878").rstrip("/"),
        "key": os.environ.get("RADARR_KEY", ""),
        "lookup": "/api/v3/movie/lookup", "add": "/api/v3/movie",
        "id_field": "tmdbId", "noun": "movie",
    },
}

# Music lives in Lidarr (API v1, artist/album model — not the v3 single-title shape above),
# so it gets a dedicated path rather than an APPS entry.
LIDARR = {
    "base": os.environ.get("LIDARR_URL", "http://hl-relay:8686").rstrip("/"),
    "key": os.environ.get("LIDARR_KEY", ""),
}

# size heuristics (GB)
BIG_GB = 15.0          # above this a 1080p grab counts as "big"
ENCODE_MAX_GB = 12.0   # a normal 1080p encode ceiling
SPREAD_GB = 8.0        # min small-vs-big gap worth asking about
MIN_SEED = 3
JUNK = {"WORKPRINT", "CAM", "TELESYNC", "TELECINE", "REGIONAL", "DVDSCR", "SDTV", "BR-DISK"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "media",
        "description": ("Manage the media library. action='search' to find a title (returns candidates), "
                        "action='add' to add a show/movie/album and start downloading it (returns immediately; "
                        "the grab runs in the background — use action='status' to see progress), action='status' "
                        "for what's currently downloading. kind='music' is for songs/albums/artists (Lidarr) — it "
                        "works at the ALBUM level, so to get a single song add the album it's on (search first "
                        "if unsure which album). For movies, omit quality for a sensible 1080p default (hard 1080p "
                        "ceiling, never 4K); pass quality only if the user asks — 'small'/'1080p'/'best'. Torrents "
                        "run through the VPN; music also pulls from Soulseek."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "add", "status"]},
                "kind": {"type": "string", "enum": ["tv", "movie", "music"], "description": "required for search/add"},
                "query": {"type": "string", "description": "title to search/add; for music an album or 'song by artist'; include the year if ambiguous"},
                "quality": {"type": "string", "enum": ["small", "1080p", "best"],
                            "description": "movies only (hard 1080p ceiling, no 4K); omit for the sensible "
                                           "default. 'small'=smallest decent 1080p, '1080p'=best 1080p encode, "
                                           "'best'=1080p REMUX/highest fidelity."},
            },
            "required": ["action"],
        },
    },
}

_meta: dict = {}


def _hdr(app):
    return {"X-Api-Key": app["key"], "Content-Type": "application/json"}


def _profile_and_root(app) -> tuple[int, str]:
    k = app["base"]
    if k not in _meta:
        profs = httpx.get(f"{k}/api/v3/qualityprofile", headers=_hdr(app), timeout=10).json()
        qp = next((p["id"] for p in profs if "1080" in p["name"]), profs[0]["id"])
        roots = httpx.get(f"{k}/api/v3/rootfolder", headers=_hdr(app), timeout=10).json()
        _meta[k] = (qp, roots[0]["path"])
    return _meta[k]


def _lookup(app, query: str) -> list[dict]:
    r = httpx.get(f"{app['base']}{app['lookup']}", headers=_hdr(app),
                  params={"term": query}, timeout=20)
    return r.json()


# ---------- movie release selection (size-aware) ----------

def _classify(rel: dict) -> dict:
    title = rel.get("title", "")
    qname = rel.get("quality", {}).get("quality", {}).get("name", "")
    low = title.lower()
    return {
        "title": title, "qname": qname,
        "size": rel.get("size", 0) / 1e9, "seed": rel.get("seeders", 0),
        "guid": rel.get("guid"), "indexerId": rel.get("indexerId"),
        "junk": qname.upper() in JUNK,
        "remux": "Remux" in qname or "remux" in low,
        "uhd": "2160" in qname or "2160p" in low or " uhd" in low,
        "is1080": "1080" in qname or "1080p" in low,
    }


def _releases(app, movie_id: int) -> list[dict]:
    r = httpx.get(f"{app['base']}/api/v3/release", headers=_hdr(app),
                  params={"movieId": movie_id}, timeout=45)
    cands = [_classify(x) for x in r.json()]
    return [c for c in cands if not c["junk"] and c["guid"]]


def _grab(app, c: dict) -> None:
    httpx.post(f"{app['base']}/api/v3/release", headers=_hdr(app),
               json={"guid": c["guid"], "indexerId": c["indexerId"]}, timeout=30)


def _best(cands, key=lambda c: c["seed"]):
    return max(cands, default=None, key=key)


def _ensure_movie(app, m: dict) -> int:
    """Return Radarr movieId, adding the movie (monitored, NO auto-search) if needed."""
    if m.get("id"):
        return m["id"]
    qp, root = _profile_and_root(app)
    payload = {
        "title": m["title"], app["id_field"]: m[app["id_field"]],
        "qualityProfileId": qp, "rootFolderPath": root, "monitored": True,
        "minimumAvailability": "released",
        "addOptions": {"searchForMovie": False},
    }
    r = httpx.post(f"{app['base']}{app['add']}", headers=_hdr(app), json=payload, timeout=30)
    return r.json()["id"]


def _select_release(rels: list[dict], quality: str | None) -> tuple[dict | None, str]:
    """Pick the release to grab (or (None, reason)). Pure size-aware selection — HARD 1080p
    ceiling, prefer a sensible encode over a giant REMUX. No user interaction: the auto path
    defaults to the smallest decent 1080p encode (caller runs async and can't ask)."""
    pool = [c for c in rels if not c["uhd"]]  # hard 1080p ceiling — drop 4K entirely
    if not pool:
        return None, "only 4K releases exist, which we don't grab"
    enc1080 = [c for c in pool if c["is1080"] and not c["remux"] and c["seed"] >= MIN_SEED]
    remux1080 = [c for c in pool if c["remux"] and c["seed"] >= MIN_SEED]
    if quality == "small":
        pick = _best([c for c in enc1080 if c["size"] <= ENCODE_MAX_GB] or enc1080,
                     key=lambda c: -c["size"])  # smallest decent
        return (pick, "1080p (smallest)") if pick else (None, "no decent 1080p encode found")
    if quality == "1080p":
        pick = _best([c for c in enc1080 if c["size"] <= ENCODE_MAX_GB] or enc1080)
        return (pick, "1080p") if pick else (None, "no 1080p encode found")
    if quality == "best":
        pick = _best(remux1080) or _best(enc1080)
        return (pick, pick["qname"]) if pick else (None, "no 1080p release found")
    # auto -> the sensible small 1080p encode (no asking on the async path)
    pick = _best([c for c in enc1080 if c["size"] <= ENCODE_MAX_GB]) or _best(enc1080) or _best(pool)
    return (pick, pick["qname"]) if pick else (None, "no grabbable release found")


def _search_and_grab(app, m: dict, movie_id: int, quality: str | None) -> None:
    """The slow half of a movie add — manual indexer search + size-aware grab. If nothing
    grabbable, the movie stays monitored in Radarr."""
    title = m["title"]
    try:
        rels = _releases(app, movie_id)
        cand, label = _select_release(rels, quality) if rels else (None, "no releases yet")
        if not cand:
            _mlog(f"add '{title}': {label} — left monitored")
            return
        _grab(app, cand)
        _mlog(f"add '{title}': grabbed {label}, {cand['size']:.1f} GB ({cand['seed']} seeds)")
    except Exception as e:  # noqa: BLE001 — background; the movie is added + monitored regardless
        _mlog(f"add '{title}': background grab failed: {e}")


# Background grabs run through ONE serialized worker, not a thread per add: a bulk request
# ("download these 8") otherwise fires N concurrent interactive indexer searches that swamp
# Prowlarr/FlareSolverr and time out. One worker = each search gets the pipeline to itself.
_grab_q: queue.Queue = queue.Queue()
_worker_lock = threading.Lock()
_worker_up = False


def _grab_worker() -> None:
    while True:
        app, m, movie_id, quality = _grab_q.get()
        try:
            _search_and_grab(app, m, movie_id, quality)
        except Exception as e:  # noqa: BLE001 — never let one job kill the worker
            _mlog(f"grab worker error: {e}")
        finally:
            _grab_q.task_done()


def _ensure_worker() -> None:
    global _worker_up
    with _worker_lock:
        if not _worker_up:
            threading.Thread(target=_grab_worker, daemon=True, name="media-grab").start()
            _worker_up = True


def _add_movie(app, m: dict, quality: str | None) -> str:
    """Add the movie (fast), then ENQUEUE the release search + grab on the serial worker so we
    return now and bulk adds don't stampede the indexers."""
    movie_id = _ensure_movie(app, m)
    _ensure_worker()
    _grab_q.put((app, m, movie_id, quality))
    q = f" ({quality})" if quality else ""
    depth = _grab_q.qsize()
    queued = f" (#{depth} in the grab queue)" if depth > 1 else ""
    return (f"Added '{m['title']}'{q} — queued for a 1080p release; downloading in the "
            f"background{queued}. Ask what's downloading in a moment to check on it.")


# ---------- music (Lidarr, API v1: artists/albums) ----------

_lidarr_meta: dict = {}


def _lidarr_hdr():
    return {"X-Api-Key": LIDARR["key"], "Content-Type": "application/json"}


def _lidarr_profiles() -> dict:
    """Cache Lidarr's quality profile (prefer 'Any'), metadata profile (skip 'None'), and
    root folder — all required (>0 / non-empty) to add an artist/album."""
    if not _lidarr_meta:
        b = LIDARR["base"]
        qps = httpx.get(f"{b}/api/v1/qualityprofile", headers=_lidarr_hdr(), timeout=10).json()
        qp = next((p["id"] for p in qps if p["name"].lower() == "any"), qps[0]["id"])
        mps = httpx.get(f"{b}/api/v1/metadataprofile", headers=_lidarr_hdr(), timeout=10).json()
        mp = next((p["id"] for p in mps if p["name"].lower() != "none"), mps[0]["id"])
        root = httpx.get(f"{b}/api/v1/rootfolder", headers=_lidarr_hdr(), timeout=10).json()[0]["path"]
        _lidarr_meta.update(qp=qp, mp=mp, root=root)
    return _lidarr_meta


def _album_lookup(query: str) -> list[dict]:
    r = httpx.get(f"{LIDARR['base']}/api/v1/album/lookup", headers=_lidarr_hdr(),
                  params={"term": query}, timeout=20)
    return r.json()


def _album_label(a: dict) -> str:
    yr = (a.get("releaseDate") or "")[:4]
    typ = a.get("albumType", "")
    artist = a.get("artist", {}).get("artistName", "?")
    return (f"{a.get('title')} — {artist}" + (f" ({yr})" if yr else "")
            + (f" [{typ}]" if typ and typ != "Album" else ""))


def _pick_album(results: list[dict], query: str) -> dict:
    """Album lookup is fuzzy (parodies/soundfont remixes can rank first). Prefer a real
    'Album' whose artist is actually named in the query; fall back to the first result."""
    q = query.lower()
    named = [a for a in results if a.get("artist", {}).get("artistName", "").lower() in q]
    pool = named or results
    return next((a for a in pool if a.get("albumType") == "Album"), pool[0])


def _add_album(a: dict) -> str:
    """Add one album (cascading-add its artist if new), monitor just that album, and search.
    The artist is added with monitor='none' so we don't grab the whole discography."""
    artist_name = a.get("artist", {}).get("artistName", "?")
    if a.get("id"):  # already in the library — just re-search it
        httpx.post(f"{LIDARR['base']}/api/v1/command", headers=_lidarr_hdr(),
                   json={"name": "AlbumSearch", "albumIds": [a["id"]]}, timeout=20)
        return f"'{a['title']}' by {artist_name} is already in the library — kicked off a fresh search."
    m = _lidarr_profiles()
    ar = a["artist"]
    ar.update({"qualityProfileId": m["qp"], "metadataProfileId": m["mp"], "rootFolderPath": m["root"],
               "monitored": True, "addOptions": {"monitor": "none", "searchForMissingAlbums": False}})
    a.update({"monitored": True, "profileId": m["qp"],
              "addOptions": {"searchForNewAlbum": True}, "artist": ar})
    r = httpx.post(f"{LIDARR['base']}/api/v1/album", headers=_lidarr_hdr(), json=a, timeout=45)
    if r.status_code >= 400:
        return f"Couldn't add it ({r.status_code}): {r.text[:160]}"
    return f"Added '{a['title']}' by {artist_name} and started searching (Soulseek + torrents)."


# ---------- entrypoint ----------

def execute(action: str, kind: str | None = None, query: str | None = None,
            quality: str | None = None) -> str:
    try:
        if action == "status":
            out = []
            for app in APPS.values():
                q = httpx.get(f"{app['base']}/api/v3/queue", headers=_hdr(app),
                              params={"pageSize": 50}, timeout=15).json()
                for rec in q.get("records", []):
                    left = rec.get("timeleft", "?")
                    out.append(f"  {rec.get('title', '?')} — {rec.get('status', '?')}/"
                               f"{rec.get('trackedDownloadState', '')} ({left})")
            try:  # music queue (Lidarr v1)
                q = httpx.get(f"{LIDARR['base']}/api/v1/queue", headers=_lidarr_hdr(),
                              params={"pageSize": 50}, timeout=15).json()
                for rec in q.get("records", []):
                    out.append(f"  {rec.get('title', '?')} — {rec.get('status', '?')}/"
                               f"{rec.get('trackedDownloadState', '')} ({rec.get('timeleft', '?')})")
            except Exception:  # noqa: BLE001
                pass
            return "Downloading now:\n" + "\n".join(out) if out else "Nothing is downloading right now."

        # --- music (Lidarr, album-level) ---
        if kind == "music":
            if not query:
                return "Error: a query is required (an album, or 'song by artist')."
            results = _album_lookup(query)
            if not results:
                return f"No music found matching '{query}'."
            if action == "search":
                lines = [f"  {_album_label(a)}" + (" [in library]" if a.get("id") else "")
                         for a in results[:5]]
                return f"Top album matches for '{query}':\n" + "\n".join(lines)
            if action == "add":
                return _add_album(_pick_album(results, query))
            return f"Error: unknown action '{action}'."

        if kind not in APPS:
            return "Error: kind must be 'tv', 'movie', or 'music'."
        app = APPS[kind]
        if not query:
            return "Error: a query (title) is required."
        results = _lookup(app, query)
        if not results:
            return f"No {kind} found matching '{query}'."

        if action == "search":
            lines = []
            for m in results[:5]:
                here = " [in library]" if m.get("id") else ""
                lines.append(f"  {m.get('title')} ({m.get('year', '?')}){here}")
            return f"Top matches for '{query}':\n" + "\n".join(lines)

        if action == "add":
            m = results[0]
            if kind == "movie":
                return _add_movie(app, m, quality)
            # --- TV: add series + search all monitored episodes (existing behavior) ---
            qp, root = _profile_and_root(app)
            if m.get("id"):
                httpx.post(f"{app['base']}/api/v3/command", headers=_hdr(app),
                           json={"name": "SeriesSearch", "seriesId": m["id"]}, timeout=20)
                return f"'{m.get('title')}' is already in the library — kicked off a fresh download search."
            payload = {
                "title": m["title"], app["id_field"]: m[app["id_field"]],
                "qualityProfileId": qp, "rootFolderPath": root, "monitored": True,
                "seasonFolder": True,
                "addOptions": {"searchForMissingEpisodes": True, "monitor": "all"},
            }
            r = httpx.post(f"{app['base']}{app['add']}", headers=_hdr(app), json=payload, timeout=30)
            if r.status_code >= 400:
                return f"Couldn't add it ({r.status_code}): {r.text[:160]}"
            return f"Added '{m.get('title')}' ({m.get('year', '?')}) and started searching for downloads."
        return f"Error: unknown action '{action}'."
    except Exception as e:  # noqa: BLE001
        return f"Error talking to the media stack: {e}"
