"""The iPhone app's native bridge (clients/bridge.js, contract in PHONE-APP.md "The web view").

In a browser it must be inert: no message posted, no DOM touched. Inside the app it says hello,
shows the model socket's state, and turns itself off on a contract version mismatch. The script
runs under node in a bare vm context with stubbed window and document; without node the
behavior checks skip and only the shell checks run."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

import config

NODE = shutil.which("node")

# Runs bridge.js in a fresh context. In "browser" mode window has no webkit and document is a
# proxy that records any access. In "app" mode a fake handler records posted messages and a fake
# #phoneTag records what the bridge shows, and a fake Hestia.ember what it was told; then each
# message in `incoming` is delivered.
HARNESS = r"""
const vm = require('vm');
const [src, mode, incoming] = [process.argv[1], process.argv[2], JSON.parse(process.argv[3])];
const touched = [], posted = [], embers = [], tag = { hidden: true, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
const document = mode === 'browser'
  ? new Proxy({}, { get(_, k) { touched.push(String(k)); return undefined; } })
  : { readyState: 'interactive', getElementById: (id) => id === 'phoneTag' ? tag : null,
      addEventListener() {} };
const window = mode === 'browser' ? {} : { webkit: { messageHandlers: { hestia: { postMessage: (m) => posted.push(m) } } } };
const Hestia = { ember: { set: (k, v) => embers.push([k, v]) } };
const ctx = vm.createContext(mode === 'browser' ? { window, document, JSON, Math, Object }
  : { window, document, JSON, Math, Object, Hestia });
vm.runInContext(src, ctx);
const b = window.hestiaBridge;
const seen = [];
b.on('transcript', (m) => seen.push(m));
const sentBefore = b.send({ type: 'speak', id: '1', text: 'hi' });
for (const m of incoming) b.receive(m);
console.log(JSON.stringify({
  active: b.active, speech: b.speech, mismatch: b.mismatch, v: b.v, sentBefore, posted, touched, seen, embers,
  tag: { hidden: tag.hidden, className: tag.className, text: tag.textContent },
}));
"""


def _run(mode: str, incoming: list | None = None) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    src = (config.CLIENTS_DIR / "bridge.js").read_text()
    out = subprocess.run([NODE, "-e", HARNESS, src, mode, json.dumps(incoming or [])],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_chat_html_loads_the_bridge_before_the_views():
    html = (config.CLIENTS_DIR / "chat.html").read_text()
    scripts = re.findall(r'<script defer src="/app/([a-z]+)\.js"></script>', html)
    assert "bridge" in scripts
    assert scripts.index("core") < scripts.index("bridge") < scripts.index("chat")
    assert 'id="phoneTag"' in html


def test_bridge_is_inert_in_a_browser():
    r = _run("browser", [{"v": 1, "type": "connection", "state": "connected"},
                         {"v": 1, "type": "transcript", "text": "hi", "final": True}])
    assert r["active"] is False
    assert r["sentBefore"] is False
    assert r["posted"] == [] and r["touched"] == [] and r["seen"] == []


def test_inside_the_app_it_says_hello_and_shows_the_socket():
    r = _run("app", [{"v": 1, "type": "hello", "speech": True, "app": "1.0"},
                     {"v": 1, "type": "connection", "state": "downloading", "progress": 0.42}])
    assert r["posted"][0] == {"v": 1, "type": "hello"}
    assert r["posted"][1] == {"v": 1, "type": "speak", "id": "1", "text": "hi"}
    assert r["active"] is True and r["speech"] is True
    assert r["tag"] == {"hidden": False, "className": "phone-tag warn", "text": "model 42%"}


def test_messages_reach_listeners_and_strings_are_accepted():
    r = _run("app", [json.dumps({"v": 1, "type": "transcript", "text": "lights off", "final": True}),
                     {"v": 1, "type": "connection", "state": "disconnected"}])
    assert r["seen"] == [{"v": 1, "type": "transcript", "text": "lights off", "final": True}]
    assert r["tag"]["hidden"] is True              # not connected is the ember's to show
    assert r["embers"] == [["phone", False]]


def test_connected_puts_the_ember_on_and_hides_the_tag():
    r = _run("app", [{"v": 1, "type": "connection", "state": "downloading", "progress": 0.5},
                     {"v": 1, "type": "connection", "state": "connected"}])
    assert r["embers"] == [["phone", False], ["phone", True]]
    assert r["tag"]["hidden"] is True


def test_a_version_mismatch_turns_the_bridge_off():
    r = _run("app", [{"v": 2, "type": "hello", "speech": True},
                     {"v": 2, "type": "transcript", "text": "x", "final": True}])
    assert r["active"] is False and r["mismatch"] == 2 and r["speech"] is None
    assert r["seen"] == []
    assert r["tag"]["text"] == "update app"
