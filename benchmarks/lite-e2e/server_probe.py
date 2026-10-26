"""Start the caller server with given flags, warm every domain slot, time real calls, report RSS.

    python3 server_probe.py -c 4480 -ub 256 -b 256
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BIN = Path.home() / "llama/llama.cpp-0.7.0/build/bin/llama-server"
GGUF = Path.home() / "lite/functiongemma-hestia-v4-q8_0.gguf"
PORT = 8091
P = json.loads((Path(__file__).parent / "prompts.json").read_text())
CASES = [("lights", "Turn off the kitchen lights."), ("inventory", "We are out of coffee."),
         ("lists", "Add milk and eggs to the shopping list."), ("lights", "Dim the bedroom lights to thirty percent.")]


def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    flags = sys.argv[1:]
    srv = subprocess.Popen([str(BIN), "-m", str(GGUF), "--host", "127.0.0.1", "--port", str(PORT),
                            "-np", "7", "-t", "4", "--no-webui", *flags],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        for slot, (before, _) in enumerate(P.values()):
            post({"prompt": before, "id_slot": slot, "cache_prompt": True, "n_predict": 0})
        slots = list(P)
        for d, text in CASES:
            t = time.perf_counter()
            out = post({"prompt": P[d][0] + text + P[d][1], "id_slot": slots.index(d), "cache_prompt": True,
                        "n_predict": 96, "temperature": 0, "stop": ["<start_function_response>", "<end_of_turn>"]})
            print(f"  {time.perf_counter() - t:5.2f}s  {out['content'][:70]!r}")
        rss = next(int(l.split()[1]) for l in open(f"/proc/{srv.pid}/status") if l.startswith("VmRSS:"))
        print(f"flags {' '.join(flags) or '(none)'}: RSS {rss // 1024} MB")
    finally:
        srv.terminate()
        srv.wait()


if __name__ == "__main__":
    main()
