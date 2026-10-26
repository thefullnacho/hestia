#!/usr/bin/env bash
# Start (or restart) Lite's two llama.cpp servers on the Pi, lean settings, localhost only.
# Caller: one slot per domain with its tool prompt cached. Embedder: EmbeddingGemma Q8_0.
set -euo pipefail
BIN="${LLAMA_SERVER:-$HOME/llama/llama.cpp-0.7.0/build/bin/llama-server}"
DIR="${LITE_DIR:-$HOME/lite}"

# exact path match on the binary, so this never matches the shell that runs it
for pid in $(pgrep -x llama-server || true); do
  [ "$(readlink -f "/proc/$pid/exe")" = "$(readlink -f "$BIN")" ] && kill "$pid"
done
sleep 1

cd "$DIR"
nohup "$BIN" -m functiongemma-hestia-v4-q8_0.gguf --host 127.0.0.1 --port 8090 -np 7 -t 4 \
  -c 4480 -b 128 -ub 128 -ctk q8_0 -ctv q8_0 --cache-ram 0 --no-webui > caller.log 2>&1 < /dev/null &
nohup "$BIN" -m embeddinggemma-q8_0.gguf --embedding --host 127.0.0.1 --port 8095 -t 4 \
  -c 512 -b 128 -ub 128 --cache-ram 0 --no-webui > embed.log 2>&1 < /dev/null &

for port in 8090 8095; do
  for _ in $(seq 60); do curl -sf "localhost:$port/health" > /dev/null && break; sleep 0.5; done
  echo "port $port: $(curl -s "localhost:$port/health")"
done
