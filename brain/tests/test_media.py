"""Media tool — pin _select_release (the size-aware movie picker, previously untested)
and the music-queue gap in action='status'.

Contract: hard 1080p ceiling, a release of UNKNOWN size is never the pick (size 0 from a
silent indexer is not 'small'), and the auto path grabs NOTHING when no decent 1080p
encode exists — the movie stays monitored in Radarr (owner decision: a blind fallback
grab of a giant REMUX or a sub-seed release is the failure mode this tool exists to
prevent). All network is stubbed."""
from __future__ import annotations

import httpx

import tools.media as media


def rel(title="R", qname="Bluray-1080p", size=6.0, seed=10,
        remux=False, uhd=False, is1080=True):
    """A release in _classify()'s output shape."""
    return {"title": title, "qname": qname, "size": size, "seed": seed,
            "guid": "g", "indexerId": 1, "junk": False,
            "remux": remux, "uhd": uhd, "is1080": is1080}


def test_unknown_size_is_never_the_small_pick():
    rels = [rel("known", size=6.0), rel("unknown", size=0.0)]
    pick, _ = media._select_release(rels, "small")
    assert pick["title"] == "known"


def test_auto_picks_sensible_encode():
    rels = [rel("remux", qname="Remux-1080p", size=30.0, seed=99, remux=True),
            rel("encode", size=5.0, seed=10)]
    pick, _ = media._select_release(rels, None)
    assert pick["title"] == "encode"


def test_auto_grabs_nothing_when_no_decent_encode():
    """Owner decision: no qualifying 1080p encode -> grab NOTHING, stay monitored."""
    pick, reason = media._select_release(
        [rel("remux", qname="Remux-1080p", size=30.0, seed=99, remux=True)], None)
    assert pick is None and "no grabbable" in reason


def test_auto_grabs_nothing_when_only_low_seed():
    pick, reason = media._select_release([rel("cold", seed=1)], None)
    assert pick is None


def test_best_prefers_remux():
    rels = [rel("remux", qname="Remux-1080p", size=30.0, seed=10, remux=True),
            rel("encode", size=5.0, seed=50)]
    pick, _ = media._select_release(rels, "best")
    assert pick["title"] == "remux"


def test_only_4k_returns_none():
    pick, reason = media._select_release(
        [rel("uhd", qname="Remux-2160p", size=60.0, uhd=True, is1080=False)], None)
    assert pick is None and "4K" in reason


def test_status_shows_music_queue_gap(monkeypatch):
    """A down Lidarr must appear in the status output, not silently omit music."""
    class R:
        def raise_for_status(self):
            return self

        def json(self):
            return {"records": []}

    def fake_get(url, **k):
        if "/api/v1/queue" in url:
            raise httpx.ConnectError("lidarr down")
        return R()
    monkeypatch.setattr(media.httpx, "get", fake_get)

    out = media.execute("status")
    assert "music queue unavailable" in out
