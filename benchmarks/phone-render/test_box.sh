#!/usr/bin/env bash
# A brain in phone mode for developing the iPhone app, beside the real one, never instead of it.
# Its own port and its own scratch household (records, memory, reminders start empty), the model
# on the phone. It loads the same secrets, so light commands reach the real lights.
#
#   HOST=<the box's tailnet address> benchmarks/phone-render/test_box.sh      # start
#   benchmarks/phone-render/test_box.sh stop
#
# The first start prints a device token for the app. Point the app at ws://HOST:PORT.
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
echo "test box up: ws://$HOST:$PORT/v1/model/socket (log: $DIR/brain.log)"
