#!/usr/bin/env python3
"""Hestia model benchmark — tool-calling first, reasoning second, single vs dual GPU.

Serves each GGUF with the vendored llama.cpp Vulkan build (no Ollama, no sudo),
hits /v1/chat/completions with a prompted tool-calling contract, and scores:

  format    — emitted exactly one valid JSON action object
  tool      — picked the right tool
  args      — key args present and correct (loose substring match)
  reasoning — multi-step cases: sensible first action
  speed     — completion tokens/sec (decode), and time-to-first-response

Every fittable model runs in BOTH single-GPU (Vulkan1) and dual-GPU
(Vulkan1,Vulkan2 tensor-split) configs, so the GPU question (decision #5) and the
model question (#2) fall out of the same sweep.

Usage:
  python bench.py                       # all models in models.json that are downloaded
  python bench.py --only qwen2.5-14b    # one model
  python bench.py --configs single      # restrict layouts
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS_DIR = HERE.parent / "models"
RESULTS_DIR = HERE / "results"
LLAMA_SERVER = Path(os.environ.get("LLAMA_SERVER") or Path.home() / "odysseus/vendor/llama.cpp/llama-server")
PORT = 8089
HOST = "127.0.0.1"


# ── serving ────────────────────────────────────────────────────────────────
def launch(model_path: Path, devices: str, tensor_split: str | None) -> subprocess.Popen:
    cmd = [
        str(LLAMA_SERVER), "--model", str(model_path),
        "--host", HOST, "--port", str(PORT),
        "-ngl", "99", "-c", "8192", "--no-warmup",
        "--device", devices,
    ]
    if tensor_split and "," in devices:
        cmd += ["--split-mode", "layer", "--tensor-split", tensor_split]
    log = open(RESULTS_DIR / "_server.log", "w")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)


def wait_ready(timeout: int = 240) -> bool:
    """Poll /health until the model is loaded (big models take a while)."""
    url = f"http://{HOST}:{PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    return False


def chat(system: str, user: str, timeout: int = 120, max_tokens: int = 256) -> tuple[str, dict]:
    body = json.dumps({
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.0, "max_tokens": max_tokens, "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    elapsed = time.time() - t0
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return text, {"elapsed": elapsed, "completion_tokens": usage.get("completion_tokens", 0)}


# ── scoring ─────────────────────────────────────────────────────────────────
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_action(text: str) -> dict | None:
    """Extract the model's JSON action, tolerating code fences / stray prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def score(case: dict, text: str) -> dict:
    obj = parse_action(text)
    strict_format = False
    try:
        strict_format = json.loads(text.strip()) == obj and obj is not None
    except Exception:
        strict_format = False
    r = {"format": obj is not None, "strict_format": strict_format,
         "tool": False, "args": False}
    if not obj or "tool" not in obj:
        return r
    exp = case["expect"]
    r["tool"] = obj.get("tool") == exp["tool"]
    args = obj.get("args", {}) or {}
    flat = " ".join(f"{k}={v}" for k, v in args.items()).lower()
    want = exp.get("args_any", {})
    if not want:
        r["args"] = True  # e.g. final(): any args ok
    else:
        ok = True
        for _key, subs in want.items():
            if not any(s.lower() in flat for s in subs):
                ok = False
                break
        r["args"] = ok and r["tool"]
    return r


# ── run one (model, config) ─────────────────────────────────────────────────
def run_combo(model: dict, config: str, vk: dict, suite: dict,
              device_override: str | None = None, max_tokens: int = 256) -> dict:
    if config == "single":
        devices = device_override or vk["single"]
    else:
        devices = vk["dual"]
    tsplit = None if config == "single" else vk["tensor_split"]
    # Per-model prompt suffix (e.g. Qwen3 '/no_think' so the JSON contract isn't eaten by a
    # reasoning block). Empty for everything else.
    sys_prompt = suite["tools_prompt"] + model.get("prompt_suffix", "")
    path = MODELS_DIR / model["file"]
    print(f"\n=== {model['name']} [{config}] on {devices} ===", flush=True)
    proc = launch(path, devices, tsplit)
    try:
        if not wait_ready():
            print("  ! server never became ready (likely OOM or load failure)", flush=True)
            return {"model": model["name"], "config": config, "loaded": False}
        rows, tok_s = [], []
        for case in suite["cases"]:
            try:
                text, meta = chat(sys_prompt, case["user"], max_tokens=max_tokens)
            except Exception as e:
                rows.append({"id": case["id"], "kind": case["kind"], "error": str(e)[:80]})
                continue
            s = score(case, text)
            if meta["elapsed"] > 0 and meta["completion_tokens"]:
                tok_s.append(meta["completion_tokens"] / meta["elapsed"])
            rows.append({"id": case["id"], "kind": case["kind"], **s,
                         "raw": text[:160]})
            mark = "ok " if s["args"] else ("~  " if s["tool"] else "X  ")
            print(f"  {mark}{case['id']:<18} {text[:70]!r}", flush=True)
        return {"model": model["name"], "config": config, "loaded": True,
                "tok_per_s": round(sum(tok_s) / len(tok_s), 1) if tok_s else 0.0,
                "rows": rows}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        time.sleep(2)


def summarize(results: list[dict]) -> None:
    print("\n" + "=" * 72)
    print(f"{'model':<20}{'cfg':<8}{'format':>8}{'tool':>7}{'args':>7}{'reason':>8}{'tok/s':>8}")
    print("-" * 72)
    for res in results:
        if not res.get("loaded"):
            print(f"{res['model']:<20}{res['config']:<8}{'— failed to load —':>40}")
            continue
        rows = [r for r in res["rows"] if "format" in r]
        n = len(rows) or 1
        fmt = sum(r["format"] for r in rows) / n
        tool = sum(r["tool"] for r in rows) / n
        args = sum(r["args"] for r in rows) / n
        reason_rows = [r for r in rows if r["kind"] == "reasoning"]
        rn = len(reason_rows) or 1
        reason = sum(r["args"] for r in reason_rows) / rn
        print(f"{res['model']:<20}{res['config']:<8}{fmt:>7.0%}{tool:>7.0%}"
              f"{args:>7.0%}{reason:>8.0%}{res['tok_per_s']:>8.1f}")
    print("=" * 72)
    print("format=valid JSON  tool=right tool  args=right tool+args  reason=multi-step subset")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", help="run specific models by name")
    ap.add_argument("--configs", nargs="+", choices=["single", "dual"])
    ap.add_argument("--device", help="override the single-config device, e.g. Vulkan2 (4060 Ti)")
    ap.add_argument("--max-tokens", type=int, default=256, help="completion budget per case")
    args = ap.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    spec = json.loads((HERE / "models.json").read_text())
    suite = json.loads((HERE / "cases.json").read_text())
    vk = spec["vulkan"]
    if not LLAMA_SERVER.exists():
        print(f"llama-server not found at {LLAMA_SERVER}", file=sys.stderr)
        return 1

    results = []
    for model in spec["models"]:
        if args.only and model["name"] not in args.only:
            continue
        if not (MODELS_DIR / model["file"]).exists():
            print(f"skip {model['name']}: not downloaded ({model['file']})")
            continue
        for config in model["configs"]:
            if args.configs and config not in args.configs:
                continue
            results.append(run_combo(model, config, vk, suite, args.device, args.max_tokens))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"results-{stamp}.json").write_text(json.dumps(results, indent=2))
    summarize(results)
    print(f"\nfull detail -> {RESULTS_DIR / f'results-{stamp}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
