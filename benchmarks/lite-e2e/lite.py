"""Hestia Lite end to end on the Pi 4: a spoken command in, an action and a spoken reply out.

    speech (WAV) -> Moonshine BASE -> router (EmbeddingGemma, nearest training example per
    domain, 0.08 gap gate) -> caller (FunctionGemma v4) -> said-words gate -> action -> reply
    text -> KittenTTS nano int8 (WAV)

Every stage is the one benchmarked alone (see ../small-router, ../lite-stt, ../lite-tts); this
run proves they hold together on one 4GB board and times each stage. Actions are a dry run:
the call is printed, never sent to Home Assistant. Replies are code-built from the call, so
what the device says is exactly what it did.

Runs in the Moonshine venv; KittenTTS runs as a warm worker in its own venv (say.py). The router
embeds through llama.cpp too (no Ollama needed). The caller is the v4 checkpoint as a Q8_0 GGUF whose tool-call
tokens are marked plain text (plain_tokens.py), served by llama.cpp with one cached slot per
domain (start it first; `--caller ollama` is the slower fallback):

    llama-server -m functiongemma-hestia-v4-q8_0.gguf --host 127.0.0.1 --port 8090 -np 7 -t 4 \
        -c 4480 -b 128 -ub 128 -ctk q8_0 -ctv q8_0 --cache-ram 0
    llama-server -m embeddinggemma-q8_0.gguf --embedding --host 127.0.0.1 --port 8095 -t 4 \
        -c 512 -b 128 -ub 128 --cache-ram 0

Slots sized to the prompts, an 8-bit cache and no RAM prompt cache took the caller server from
775 to 457 MB with identical calls (2026-10-26).

    ~/moon-venv/bin/python lite.py ~/clips             # every WAV in transcripts.txt
    ~/moon-venv/bin/python lite.py ~/clips/03.wav      # one file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import os

os.environ["HF_HUB_OFFLINE"] = "1"  # every model loads from the Pi's cache; nothing phones home

import numpy as np
import moonshine_voice as mv
from moonshine_voice.transcriber import Transcriber

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "small-router"))
import caller  # noqa: E402

OLLAMA = "http://localhost:11434"
EMBED = "embeddinggemma"
CALLER = "functiongemma-hestia-v4"
# llama.cpp's server keeps one slot per domain with that domain's tool prompt already read, so a
# call costs only the person's words and the answer. Ollama re-reads the whole prompt unless it
# is byte-identical (measured 2026-10-26), which made the caller most of the wait.
SERVER = "http://127.0.0.1:8090"
# EmbeddingGemma as Q8_0 in llama.cpp (retag_embeddinggemma.py --q8): 380 MB instead of Ollama's
# 609, and about five times faster per sentence on the Pi 4, with no Ollama running at all.
EMBED_SERVER = "http://127.0.0.1:8095"
EMBEDDER = "server"  # or "ollama"; set from --embedder
QUERY = "task: classification | query: "  # EmbeddingGemma's task prefix, as router_eval uses it
GAP = 0.08  # top-two gap under this asks which of the two (picked on val, router RESULTS)
STOPS = ["<start_function_response>", "<end_of_turn>"]
# Moonshine needs a little silence before the speech: a real recording cut 0.15 s before the
# first word came back as "10." for a whole dim command, and fine with this much (2026-10-26).
# A live mic's voice detector keeps this much pre-roll anyway.
PAD_S = 0.5
KITTEN_PY = Path.home() / "kitten-venv/bin/python"
CACHE = Path.home() / ".cache/lite-e2e-examples.npz"


def ollama(path: str, body: dict) -> dict:
    req = urllib.request.Request(f"{OLLAMA}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def embed(texts: list[str]) -> np.ndarray:
    vecs = []
    for i in range(0, len(texts), 64):
        chunk = [QUERY + t for t in texts[i:i + 64]]
        if EMBEDDER == "server":
            req = urllib.request.Request(f"{EMBED_SERVER}/v1/embeddings", data=json.dumps({"input": chunk}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                vecs += [d["embedding"] for d in sorted(json.load(r)["data"], key=lambda d: d["index"])]
        else:
            vecs += ollama("/api/embed", {"model": EMBED, "input": chunk, "keep_alive": -1})["embeddings"]
    v = np.array(vecs, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def examples() -> tuple[np.ndarray, list[str]]:
    """The router's examples: the training split, `none` a domain, as router_eval builds it."""
    rows = [json.loads(l) for l in (HERE.parent / "small-router/train.jsonl").read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r["split"] == "train"]
    gold = [caller.TOOL_DOMAIN[r["calls"][0]["tool"]] if r["calls"] else
            ("none" if r["expect"] == "none" else r["domain"]) for r in rows]
    texts = [r["text"] for r in rows]
    # the vectors depend on which copy of the model made them, so that is part of the key
    key = hashlib.sha256((EMBEDDER + "\n" + "\n".join(texts)).encode()).hexdigest()
    if CACHE.exists():
        z = np.load(CACHE)
        if str(z["key"]) == key:
            return z["vecs"], gold
    print(f"embedding {len(texts)} router examples once (cached after)...", file=sys.stderr)
    vecs = embed(texts)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, vecs=vecs, key=key)
    return vecs, gold


def clean(heard: str) -> str:
    """Moonshine BASE joins some words with hyphens ("to-morrow", "living-room"). The said-words
    gate compares the caller's values to these words, so join them back the way people write."""
    heard = re.sub(r"\b(to)-(day|morrow|night)\b", r"\1\2", heard, flags=re.I)
    return re.sub(r"(?<=[a-z])-(?=[a-z])", " ", heard)


def route(text: str, ex_vecs: np.ndarray, ex_dom: list[str]) -> tuple[str, list]:
    """act / ask / drop, and the domains ranked by their single closest example."""
    sims = ex_vecs @ embed([text])[0]
    best: dict[str, float] = {}
    for s, d in zip(sims.tolist(), ex_dom):
        if s > best.get(d, -1.0):
            best[d] = s
    ranked = sorted(((s, d) for d, s in best.items()), reverse=True)
    (s1, d1), (s2, d2) = ranked[0], ranked[1]
    if s1 - s2 < GAP:
        # "did you mean none or alerts?" is not a question: a near-tie with nothing to do stays quiet
        return ("drop" if "none" in (d1, d2) else "ask"), ranked
    return ("drop" if d1 == "none" else "act"), ranked


def _server(body: dict) -> dict:
    req = urllib.request.Request(f"{SERVER}/completion", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def warm_slots(prompts: dict) -> None:
    """Read every domain's tool prompt into its own slot once, at startup."""
    for slot, (before, _) in enumerate(prompts.values()):
        _server({"prompt": before, "id_slot": slot, "cache_prompt": True, "n_predict": 0})


def call(domain: str, text: str, prompts: dict, use_server: bool) -> tuple[list[dict], str, str]:
    before, after = prompts[domain]
    if use_server:
        out = _server({"prompt": before + text + after, "id_slot": list(prompts).index(domain),
                       "cache_prompt": True, "n_predict": 96, "temperature": 0, "stop": STOPS})
        response = out["content"]
    else:
        response = ollama("/api/generate", {"model": CALLER, "prompt": before + text + after, "raw": True,
                                            "stream": False, "keep_alive": -1,
                                            "options": {"temperature": 0, "num_predict": 96,
                                                        "stop": STOPS}})["response"]
    calls, said = caller.parse(response)
    return calls, said, response


def _list(items) -> str:
    items = items if isinstance(items, list) else [items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def reply(calls: list[dict]) -> str:
    """What the device says, built from the calls themselves."""
    parts = []
    for c in calls:
        t, a = c["tool"], c["args"]
        if t == "light_set":
            who = f"{a['target']} lights" if a.get("target") and a["target"] != "all" else (
                "All the lights" if a.get("target") == "all" else "Lights")
            if a.get("brightness") not in (None, ""):
                parts.append(f"{who} at {a['brightness']} percent.")
            else:
                parts.append(f"{who} {a.get('state', 'set')}.")
        elif t == "scene_activate":
            parts.append(f"{a.get('name', 'That')} scene on.")
        elif t in ("list_add", "list_remove"):
            verb, prep = ("Added", "to") if t == "list_add" else ("Took", "off")
            parts.append(f"{verb} {_list(a.get('items', []))} {prep} the {a.get('list', 'shopping')} list.")
        elif t == "reminder_add":
            when = str(a.get("when", "")).strip()
            # a bare duration ("ten minutes") reads as "in ten minutes"
            if re.fullmatch(r"[\w\s-]*\b(seconds?|minutes?|hours?)", when) and not when.startswith("in "):
                when = "in " + when
            parts.append(f"I'll remind you {when} to {a['text']}." if a.get("text") else f"Timer set for {when.removeprefix('in ')}.")
        elif t == "music_play":
            parts.append(f"Playing {a.get('query', 'music')}.")
        elif t == "music_control":
            done = {"pause": "Paused", "resume": "Resuming", "skip": "Skipped", "previous": "Going back",
                    "stop": "Stopped", "louder": "Louder", "quieter": "Quieter"}
            act = a.get("action", "")
            parts.append(f"Volume {a.get('level')}." if act == "set_volume" else f"{done.get(act, 'Done')}.")
        elif t in ("list_read", "calendar_read", "weather_get", "inventory_find", "alerts_read"):
            parts.append("Here's what I have.")  # a read: the real device would speak the data
        else:
            parts.append("Noted.")
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def handle(wav: Path, tr, ex, prompts, use_server: bool = False) -> dict:
    t = {}
    t0 = time.perf_counter()
    data, rate = mv.load_wav_file(str(wav))
    pad = np.zeros(int(rate * PAD_S), dtype=np.float32)
    data = np.concatenate([pad, np.asarray(data, dtype=np.float32), pad])
    heard = clean(" ".join(l.text for l in tr.transcribe_without_streaming(data, sample_rate=rate).lines).strip())
    t["stt"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    decision, ranked = route(heard, *ex)
    t["route"] = time.perf_counter() - t1
    top = [(d, round(s, 3)) for s, d in ranked[:2]]
    rec = {"wav": wav.name, "heard": heard, "route": decision, "top": top}

    if decision == "drop":
        say = ""
    elif decision == "ask":
        say = f"Did you mean {top[0][0]} or {top[1][0]}?"
    else:
        t2 = time.perf_counter()
        calls, said, raw = call(top[0][0], heard, prompts, use_server)
        t["call"] = time.perf_counter() - t2
        rec["calls"], rec["raw"] = calls, raw
        if calls and not caller.spans_said(heard, calls):
            rec["gate"] = "refused: a value the person did not say"
            say = "Sorry, say that again?"
        elif calls:
            rec["action"] = "dry run: " + "; ".join(f"{c['tool']}({c['args']})" for c in calls)
            say = reply(calls)
        else:
            say = said or "Sorry, I didn't catch that."
    rec["say"] = say
    rec["t"] = {k: round(v, 2) for k, v in t.items()}
    rec["t"]["to_reply_text"] = round(time.perf_counter() - t0, 2)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="a WAV, or a folder with transcripts.txt (file<TAB>text)")
    ap.add_argument("--out", default=str(Path.home() / "lite/out"))
    ap.add_argument("--embedder", choices=["server", "ollama"], default="server",
                    help="EmbeddingGemma Q8_0 in llama.cpp (start it first), or Ollama")
    ap.add_argument("--caller", choices=["server", "ollama"], default="server",
                    help="llama.cpp server with a cached slot per domain (start it first), or Ollama")
    a = ap.parse_args()
    src = Path(a.input).expanduser()
    if src.is_dir():
        rows = [l.split("\t") for l in (src / "transcripts.txt").read_text().splitlines() if l.strip()]
        jobs = [(src / name, text) for name, text in rows]
    else:
        jobs = [(src, None)]
    global EMBEDDER
    EMBEDDER = a.embedder
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    path, arch = mv.get_model_for_language("en", mv.ModelArch.BASE)
    tr = Transcriber(path, model_arch=arch)
    ex = examples()
    prompts = json.loads((HERE / "prompts.json").read_text())
    use_server = a.caller == "server"
    if use_server:
        warm_slots(prompts)
    tts = subprocess.Popen([str(KITTEN_PY), str(HERE / "say.py")], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True)
    json.loads(tts.stdout.readline())
    # warm every model once so the timings below are a device that is already listening
    handle(jobs[0][0], tr, ex, prompts, use_server)
    print(json.dumps({"startup_s": round(time.perf_counter() - t0, 1)}), flush=True)

    for wav, expected in jobs:
        rec = handle(wav, tr, ex, prompts, use_server)
        if rec["say"]:
            tts.stdin.write(json.dumps({"text": rec["say"], "out": str(out / wav.name)}) + "\n")
            tts.stdin.flush()
            done = json.loads(tts.stdout.readline())
            rec["t"]["tts"] = done["secs"]
            rec["t"]["to_first_audio"] = round(rec["t"]["to_reply_text"] + done["secs"], 2)
        if expected:
            rec["expected"] = expected
        print(json.dumps(rec), flush=True)
    tts.stdin.close()
    tts.wait()


if __name__ == "__main__":
    main()
