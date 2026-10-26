"""Peak resident memory of each Lite process while a run is going, and of all of them at once.

Samples /proc every 0.2 s until the command it wraps exits. Lite is several processes (the
pipeline, the TTS worker, the caller server, the embedding runner), so a 2 GB board has to hold
their sum at the busiest moment, not each one's own peak on a different turn.

    python3 memwatch.py -- ~/moon-venv/bin/python lite.py ~/clips
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

# first match wins, so the two llama.cpp servers are told apart by port before the binary
WATCH = {"lite.py": "pipeline (STT, router, gate)", "say.py": "TTS worker",
         "--port 8095": "embedding server", "--port 8090": "caller server",
         "ollama/llama-server": "embedding (Ollama runner)", "ollama serve": "Ollama daemon"}


def rss_by_part() -> dict[str, int]:
    out: dict[str, int] = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
            rss = next(int(l.split()[1]) for l in open(f"/proc/{pid}/status") if l.startswith("VmRSS:"))
        except (OSError, StopIteration):
            continue
        for key, name in WATCH.items():
            if key in cmd and "memwatch" not in cmd:
                out[name] = out.get(name, 0) + rss // 1024
                break
    return out


def main():
    cmd = sys.argv[sys.argv.index("--") + 1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    peak: dict[str, int] = {}
    peak_sum = 0
    while proc.poll() is None:
        now = rss_by_part()
        for k, v in now.items():
            peak[k] = max(peak.get(k, 0), v)
        peak_sum = max(peak_sum, sum(now.values()))
        time.sleep(0.2)
    for k, v in sorted(peak.items(), key=lambda kv: -kv[1]):
        print(f"{v:6d} MB  {k}")
    print(f"{peak_sum:6d} MB  all at once (peak)")
    avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable:"))
    print(f"exit {proc.returncode}, MemAvailable after {avail // 1024} MB")


if __name__ == "__main__":
    main()
