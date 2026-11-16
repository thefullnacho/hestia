"""Search tool — the fetch path must never read internal services. The brain is
unauthenticated because the network boundary is the auth boundary, and the box runs
unauthenticated internal endpoints (Ollama, the brain's own /memory/inbox, hl-relay
services). A model-directed fetcher with no host restriction turns a prompt-injected web
page into a reader of all of them, so fetch is pinned to public hosts, every redirect hop
included.
Network is stubbed everywhere; httpx.get is monkeypatched and asserts it is NOT called
on the blocked paths."""
from __future__ import annotations

import httpx

import tools.search as search


class _Resp:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        pass


def _no_http(monkeypatch):
    """Fail the test if any real HTTP call is attempted."""
    def boom(*a, **k):
        raise AssertionError("httpx.get was called for a URL that must be blocked")
    monkeypatch.setattr(search.httpx, "get", boom)

    class NoClient:                     # fetch opens a client per page; it must never get one
        def __init__(self, *a, **k):
            raise AssertionError("an HTTP client was opened for a URL that must be blocked")
    monkeypatch.setattr(search.httpx, "Client", NoClient)


def _public_dns(monkeypatch):
    """Every hostname resolves to a globally-routable address (example.com's)."""
    monkeypatch.setattr(search.socket, "getaddrinfo",
                        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 80))])


def test_fetch_blocks_loopback(monkeypatch):
    _no_http(monkeypatch)
    out = search.execute("fetch", url="http://127.0.0.1:11434/api/tags")
    assert "public" in out.lower()


def test_fetch_blocks_link_local_and_internal_names(monkeypatch):
    _no_http(monkeypatch)
    assert "public" in search.execute("fetch", url="http://169.254.169.254/latest/meta-data").lower()
    # An internal name: either unresolvable off-mesh or a private/tailnet address on it.
    assert "public" in search.execute("fetch", url="http://hl-relay:8124/api/").lower()


def test_fetch_blocks_tailnet_addresses(monkeypatch):
    """100.64.0.0/10 (Tailscale CGNAT) is not is_private on older Pythons — pin it here."""
    _no_http(monkeypatch)
    assert "public" in search.execute("fetch", url="http://100.64.1.2:8730/memory/inbox").lower()


def _serve(monkeypatch, routes):
    def handler(request):
        status, headers, body = routes[str(request.url)]
        return httpx.Response(status, headers=headers, content=body)
    real = httpx.Client
    monkeypatch.setattr(search.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_fetch_never_follows_a_redirect_onto_a_private_host(monkeypatch):
    """Redirects are followed one hop at a time, each held to the public-only rule: a public
    page can't bounce the brain onto loopback (2026-11-16; before, redirects were refused)."""
    _public_dns(monkeypatch)
    _serve(monkeypatch, {"https://example.com/redirect": (302, {"location": "http://127.0.0.1/x"}, b"")})
    out = search.execute("fetch", url="https://example.com/redirect")
    assert out.startswith("Error:") and "private" in out


def test_fetch_public_page_extracts_text(monkeypatch):
    """The guard must not break the intended use: a public page still reads as text."""
    _public_dns(monkeypatch)
    html = (b"<html><head><style>body{color:red}</style></head><body>"
            b"<h1>Banana Bread</h1><script>var x=1;</script><p>Bake at 350F.</p></body></html>")
    _serve(monkeypatch, {"https://example.com/banana-bread": (200, {"content-type": "text/html"}, html)})
    out = search.execute("fetch", url="https://example.com/banana-bread")
    assert "Banana Bread" in out and "Bake at 350F" in out
    assert "var x=1" not in out and "color:red" not in out


def test_search_action_untouched(monkeypatch):
    """The query path goes to the self-hosted SearXNG only; it is not host-restricted."""
    class SearxResp(_Resp):
        def json(self):
            return {"results": [{"title": "T", "url": "https://example.com", "content": "c"}]}
    monkeypatch.setattr(search.httpx, "get", lambda *a, **k: SearxResp())
    assert "Top web results" in search.execute("search", query="banana bread")
