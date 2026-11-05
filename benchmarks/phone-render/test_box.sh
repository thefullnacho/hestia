#!/usr/bin/env bash
# A brain in phone mode for developing the iPhone app, beside the real one, never instead of it.
# Its own port and its own scratch household (records, memory, reminders start empty), the model
# on the phone. It loads the same secrets, so light commands reach the real lights.
#
#   HOST=<the box's tailnet address> benchmarks/phone-render/test_box.sh      # start
#   benchmarks/phone-render/test_box.sh stop
#
# The first start prints a device token for the app. iOS needs TLS, so put the test box behind
# Tailscale HTTPS, tailnet only, with no path on the target (a path there joins every request's):
#   tailscale serve --bg --https=8443 http://HOST:PORT
# and point the app at https://<this box's tailnet name>:8443. Undo: tailscale serve --https=8443 off
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="${TEST_BOX_DIR:-$HOME/.local/state/hestia-testbox}"
PORT="${PORT:-8734}"
MODELS="${MODELS:-$HOME/models/lfm3}"

if [ "${1:-}" = stop ]; then
  [ -f "$DIR/brain.pid" ] && kill "$(cat "$DIR/brain.pid")" 2>/dev/null && echo "test box stopped" || echo "not running"
  rm -f "$DIR/brain.pid"; exit 0
fi
: "${HOST:?set HOST to the tailnet address of this box, never 0.0.0.0}"
case "$HOST" in 0.0.0.0|::|"*") echo "refusing $HOST: the brain binds to a private address only" >&2; exit 1;; esac
if curl -sf --max-time 2 "http://$HOST:$PORT/health" > /dev/null; then
  echo "something already answers on $HOST:$PORT; stop it first (test_box.sh stop, or kill the old" \
       "uvicorn if brain.pid is stale)" >&2
  exit 1
fi
mkdir -p "$DIR/data" "$DIR/memory"
export HESTIA_DATA_DIR="$DIR/data" HESTIA_DB="$DIR/data/hestia.db" HESTIA_MEMORY_DIR="$DIR/memory" \
  HESTIA_BACKEND=phone HESTIA_PHONE_MODEL="$MODELS/LFM3-1.7B-QAD-Q4_0.gguf" \
  HESTIA_CHAT_TEMPLATE="$MODELS/chat_template.jinja" HESTIA_BOS_TOKEN='<|startoftext|>'
cd "$REPO/brain"
if [ ! -s "$DIR/data/phone_devices.json" ]; then
  echo "device token for the app (shown once):"
  uv run --quiet python phone.py add-device iphone
fi
nohup uv run --quiet uvicorn hestia:app --host "$HOST" --port "$PORT" > "$DIR/brain.log" 2>&1 &
echo $! > "$DIR/brain.pid"
for _ in $(seq 60); do curl -sf "http://$HOST:$PORT/health" > /dev/null && break; sleep 1; done
if ! kill -0 "$(cat "$DIR/brain.pid")" 2>/dev/null; then
  rm -f "$DIR/brain.pid"; echo "test box did not start; see $DIR/brain.log" >&2; tail -3 "$DIR/brain.log" >&2; exit 1
fi
echo "test box up on http://$HOST:$PORT; serve it over Tailscale HTTPS for the app (log: $DIR/brain.log)"
