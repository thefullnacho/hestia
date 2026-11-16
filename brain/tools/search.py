"""`search` tool — private web lookup for the brain.

Grounds questions about the world (and Hestia's own software) instead of guessing.
Backed by a self-hosted SearXNG meta-search on the appliance box: no API keys and no account,
and the engines see SearXNG's requests rather than the house's. The query text itself does
reach those upstream engines, and `fetch` is a plain GET to the page's own site (SECURITY.md).
Two actions: `search` (results + snippets) and `fetch` (read one page as text).
Both are read-only — no safety-gate concerns. `fetch` is egress-restricted to public,
globally-routable hosts and never follows redirects, so a prompt-injected page can't
steer the model into reading loopback/LAN/tailnet services through it (see SECURITY.md).
"""
from __future__ import annotations

import html as htmllib
import ipaddress
import os
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

SEARX_URL = os.environ.get("SEARXNG_URL", "http://hl-relay:8095").rstrip("/")
# Names the software and where it lives, as sites like Wikipedia require; the same for every
# household, so it identifies no one.
_UA = "Hestia/0.4 (+https://github.com/thefullnacho/hestia)"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search",
        "description": ("Look something up on the web when the answer isn't about the house, "
                        "media library, or saved memory — current events, facts, prices, docs, "
                        "how-tos, anything outside your own knowledge or that may have changed. "
                        "action='search' returns top results with snippets; action='fetch' reads "
                        "the full text of one result URL when a snippet isn't enough. Prefer search "
                        "first, then fetch a promising URL only if you need detail."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "fetch"]},
                "query": {"type": "string", "description": "search terms (for action='search')"},
                "url": {"type": "string", "description": "page URL to read (for action='fetch')"},
            },
            "required": ["action"],
        },
    },
}

_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(script|style|noscript|svg|nav|header|footer|aside|form)\b[^>]*>.*?</\1>", re.S | re.I)
_WS = re.compile(r"[ \t]*\n\s*\n\s*", re.S)

# Tailscale's CGNAT range. is_global already excludes loopback/RFC1918/link-local, but
# 100.64/10 counts as global on older Pythons, so exclude it explicitly: an internal name
# resolved over MagicDNS must never be fetchable.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _public_only(url: str) -> bool:
    """True only if every address the URL's host resolves to is globally routable.

    fetch is model-directed, and the box runs unauthenticated internal services by design
    (Ollama, the brain's own endpoints, hl-relay). Without this guard a prompt-injected
    page could steer the model into reading those and speaking the contents. Unresolvable
    hosts (e.g. an internal name off-mesh) are refused too."""
    host = urlparse(url).hostname or ""
    if not host:
        return False
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None)]
        except (OSError, ValueError):
            return False
    return bool(addrs) and all(a.is_global and a not in _CGNAT for a in addrs)


def _search(query: str, n: int = 6) -> str:
    r = httpx.get(f"{SEARX_URL}/search", headers={"User-Agent": _UA},
                  params={"q": query, "format": "json", "safesearch": 1}, timeout=8)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return f"No web results for '{query}'."
    lines = [f"Top web results for '{query}':"]
    for i, res in enumerate(results[:n], 1):
        title = (res.get("title") or "").strip()
        url = res.get("url") or ""
        snippet = " ".join((res.get("content") or "").split())[:280]
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


MAX_HOPS = 5
MAX_PAGE_BYTES = 3_000_000
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_MAIN = re.compile(r"<(article|main)\b[^>]*>(.*)</\1>", re.I | re.S)


class Unreadable(Exception):
    """A page that can't be read, with the reason in words a person can act on."""


def _get_public(url: str) -> tuple[str, str]:
    """(final url, html) for a public page. Redirects are followed by hand, at most MAX_HOPS,
    and every hop is held to the public-only rule, so a public link can't bounce the brain onto
    loopback, the LAN or the tailnet. A plain GET: no cookies, no login, no identifiers."""
    def check(u: str) -> None:
        if not re.match(r"^https?://", u):
            raise Unreadable("it isn't a web address")
        if not _public_only(u):
            raise Unreadable("Hestia reads only public web pages, and that one points at a private or unknown host")

    check(url)                          # before any connection exists
    with httpx.Client(headers={"User-Agent": _UA}, timeout=12, follow_redirects=False) as client:
        for hop in range(MAX_HOPS + 1):
            if hop:
                check(url)
            with client.stream("GET", url) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers.get("location", ""))
                    continue
                if r.status_code != 200:
                    raise Unreadable(f"the site answered {r.status_code}")
                kind = r.headers.get("content-type", "")
                if kind and "html" not in kind and "text" not in kind:
                    raise Unreadable(f"it isn't a web page ({kind.split(';')[0]})")
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        raise Unreadable("the page is too large")
                return url, body.decode(r.encoding or "utf-8", errors="replace")
    raise Unreadable(f"it redirects more than {MAX_HOPS} times")


def page_text(html: str) -> tuple[str, str]:
    """(title, readable text): the <article> or <main> when the page has one, else the body."""
    t = _TITLE.search(html)
    title = " ".join(htmllib.unescape(t.group(1)).split()) if t else ""
    main = _MAIN.search(html)
    text = _DROP.sub(" ", main.group(2) if main else html)
    text = _TAG.sub(" ", text)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return title[:200], _WS.sub("\n\n", text).strip()


def read_link(url: str, limit: int = 5000) -> dict:
    """A page a person pasted, read once: {url, title, text} or raises Unreadable."""
    final, html = _get_public(url)
    title, text = page_text(html)
    if len(text) < 200:
        raise Unreadable("the page has almost no text (it may need a login or run on scripts)")
    return {"url": final, "title": title,
            "text": text if len(text) <= limit else text[:limit] + "\n…[truncated]"}


def _fetch(url: str, limit: int = 3500) -> str:
    try:
        final, html = _get_public(url)
    except Unreadable as e:
        return f"Error: couldn't read that page: {e}."
    _, text = page_text(html)
    if len(text) > limit:
        text = text[:limit] + "\n…[truncated]"
    return f"Readable text from {final}:\n{text}"


def execute(action: str, query: str | None = None, url: str | None = None) -> str:
    try:
        if action == "search":
            if not query:
                return "Error: a query is required for search."
            return _search(query)
        if action == "fetch":
            if not url:
                return "Error: a url is required for fetch."
            return _fetch(url)
        return f"Error: unknown action '{action}' (use 'search' or 'fetch')."
    except httpx.HTTPError as e:
        return f"Search backend error (is SearXNG up at {SEARX_URL}?): {e}"
    except Exception as e:  # noqa: BLE001
        return f"Error during {action}: {e}"
