"""Does EmbeddingGemma served by llama.cpp give the same vectors as Ollama's copy?

Starts llama-server in embedding mode on Ollama's own model file, embeds a few router queries
through both, and prints the cosine between each pair plus the server's RSS. The router's 3,400
cached example vectors came from Ollama, so a swap is only free if these agree to ~0.999.

    python3 embed_probe.py /path/to/ollama/blob [extra llama-server flags]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

BIN = Path.home() / "llama/llama.cpp-0.7.0/build/bin/llama-server"
PORT = 8093
QUERY = "task: classification | query: "
TEXTS = ["Turn off the kitchen lights.", "We are out of coffee.", "Will it rain tomorrow?",
         "Thank you.", "How dry is the carrot bed?"]


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def unit(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def main():
    blob, flags = sys.argv[1], sys.argv[2:]
    srv = subprocess.Popen([str(BIN), "-m", blob, "--host", "127.0.0.1", "--port", str(PORT), "--embedding",
                            "-t", "4", "--no-webui", *flags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        ins = [QUERY + t for t in TEXTS]
        ref = post("http://localhost:11434/api/embed", {"model": "embeddinggemma", "input": ins})["embeddings"]
        t = time.perf_counter()
        got = post(f"http://127.0.0.1:{PORT}/v1/embeddings", {"input": ins})["data"]
        secs = time.perf_counter() - t
        for text, a, b in zip(TEXTS, ref, got):
            print(f"  cos {float(unit(a) @ unit(b['embedding'])):.5f}  dim {len(b['embedding'])}  {text}")
        rss = next(int(l.split()[1]) for l in open(f"/proc/{srv.pid}/status") if l.startswith("VmRSS:"))
        print(f"flags {' '.join(flags) or '(none)'}: RSS {rss // 1024} MB, {secs:.2f}s for {len(TEXTS)}")
    finally:
        srv.terminate()
        srv.wait()


if __name__ == "__main__":
    main()
