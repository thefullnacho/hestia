"""`search` tool — private web lookup for the brain.

Grounds questions about the world (and Hestia's own software) instead of guessing.
Backed by a self-hosted SearXNG meta-search on the appliance box, so the brain's
queries never leave the user's hardware — no API keys, no third party in the loop.
Two actions: `search` (results + snippets) and `fetch` (read one page as text).
Both are read-only — no safety-gate concerns.
"""
from __future__ import annotations

import os
import re

import httpx

SEARX_URL = os.environ.get("SEARXNG_URL", "http://hl-relay:8095").rstrip("/")
_UA = "Hestia/0.4 (+local agent)"

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
_DROP = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_WS = re.compile(r"[ \t]*\n\s*\n\s*", re.S)


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


def _fetch(url: str, limit: int = 3500) -> str:
    if not re.match(r"^https?://", url):
        return "Error: fetch needs a full http(s) URL (get one from a search result first)."
    r = httpx.get(url, headers={"User-Agent": _UA}, timeout=12, follow_redirects=True)
    r.raise_for_status()
    html = r.text
    text = _DROP.sub(" ", html)
    text = _TAG.sub(" ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = _WS.sub("\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit] + "\n…[truncated]"
    return f"Readable text from {url}:\n{text}"


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
