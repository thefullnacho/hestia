"""The chat PWA's shell: every file the service worker pre-caches and every icon the manifest
names must actually be served. cache.addAll() is all-or-nothing, so a single 404 in that
list means the worker never installs, and a missing manifest icon breaks Android installs."""
from __future__ import annotations

import json
import re

import pytest

import config


@pytest.fixture
def client(db):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    return fastapi_testclient.TestClient(hestia.app)


def _sw_shell() -> list[str]:
    src = (config.CLIENTS_DIR / "sw.js").read_text()
    paths = []
    for name in ("PAGES", "STATIC"):
        m = re.search(rf"const {name} = \[(.*?)\];", src)
        assert m, f"sw.js no longer declares {name}"
        paths += re.findall(r"'([^']+)'", m.group(1))
    return paths


def test_every_precached_path_is_served(client):
    paths = _sw_shell()
    assert "/app" in paths
    for p in paths:
        assert client.get(p).status_code == 200, p


def test_every_manifest_icon_is_served(client):
    manifest = json.loads((config.CLIENTS_DIR / "manifest.json").read_text())
    for icon in manifest["icons"]:
        r = client.get(icon["src"])
        assert r.status_code == 200, icon["src"]
        assert r.headers["content-type"] == "image/png"


def test_sized_icons_are_distinct_files(client):
    assert client.get("/icon-192.png").content == (config.CLIENTS_DIR / "icon-192.png").read_bytes()
    assert client.get("/icon-512.png").content == (config.CLIENTS_DIR / "icon-512.png").read_bytes()
