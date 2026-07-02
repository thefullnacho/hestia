#!/usr/bin/env bash
# Runs OFF-SITE (the dedi) every 5 minutes: is the house alive? Probes the brain's /health
# over the tailnet and pages the phone via ntfy when the house goes dark — the one alert the
# house cannot send about itself. One page per transition (down -> up, up -> down), silence
# otherwise, so it never becomes noise you learn to ignore.
#
# A single failed probe gets one retry after RETRY_WAIT before it counts — a blip on the
# tailnet shouldn't page anyone at 3am.
set -euo pipefail

# Required from the unit — no baked-in defaults (a scrubbed placeholder deploys silently).
HEALTH_URL="${HESTIA_HEALTH_URL:?set HESTIA_HEALTH_URL (brain /health over the tailnet) in the unit}"
NTFY_URL="${HESTIA_NTFY_URL:?set HESTIA_NTFY_URL (e.g. https://ntfy.sh/<random-topic>) in the unit}"
STATE_FILE="${HESTIA_WATCHDOG_STATE:-$HOME/.local/state/hestia-watchdog/state}"
RETRY_WAIT="${HESTIA_WATCHDOG_RETRY_WAIT:-20}"

mkdir -p "$(dirname "$STATE_FILE")"

probe() { curl -fsS -m 10 "$HEALTH_URL" >/dev/null 2>&1; }

notify() { # $1 title, $2 body, $3 priority, $4 tags
  curl -fsS -m 10 --retry 3 -H "Title: $1" -H "Priority: $3" -H "Tags: $4" \
       -d "$2" "$NTFY_URL" >/dev/null
}

if probe; then now=up; else sleep "$RETRY_WAIT"; if probe; then now=up; else now=down; fi; fi
last=$(cat "$STATE_FILE" 2>/dev/null || echo up)

if [ "$now" = down ] && [ "$last" != down ]; then
  notify "Hestia: house is DARK" \
         "Brain /health unreachable from off-site (2 probes, ${RETRY_WAIT}s apart). Power? Internet? Tailscale? Checked $(date -u +'%H:%M UTC')." \
         urgent "rotating_light"
elif [ "$now" = up ] && [ "$last" = down ]; then
  notify "Hestia: house is back" "Brain /health reachable again at $(date -u +'%H:%M UTC')." \
         default "white_check_mark"
fi

echo "$now" > "$STATE_FILE"
echo "watchdog: house $now (was $last)"
