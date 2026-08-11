#!/usr/bin/env bash
# Runs OFF-SITE (the dedi) every 5 minutes. Two independent probes of the house, each with
# its own state so neither can mask the other:
#
#   brain   the agent's /health over the tailnet. Is the house alive at all?
#   dns     the household resolver actually resolving, and still filtering.
#
# Why DNS is probed from outside rather than watched from inside: once the router has no
# external fallback resolver, a resolver that answers nothing takes the whole house off the
# internet while every container still reports itself healthy and every process stays up.
# That failure is silent by construction. It is also not hypothetical, see the 2026-08-11
# entry in STATUS.md, which was found by hand rather than by alert.
#
# Edge-triggered: one page per transition (down -> up, up -> down), silence otherwise, so it
# never becomes noise you learn to ignore. A single failed probe gets one retry after
# RETRY_WAIT before it counts, because a blip on the tailnet should not page anyone at 3am.
set -euo pipefail

# Required from the unit. No baked-in defaults, because a scrubbed placeholder deploys
# silently and looks like it is working.
HEALTH_URL="${HESTIA_HEALTH_URL:?set HESTIA_HEALTH_URL (brain /health over the tailnet) in the unit}"
NTFY_URL="${HESTIA_NTFY_URL:?set HESTIA_NTFY_URL (e.g. https://ntfy.sh/<random-topic>) in the unit}"

# Optional. Unset means the DNS probe is skipped, and says so loudly in the journal rather
# than passing quietly.
DNS_SERVER="${HESTIA_DNS_SERVER:-}"
DNS_PROBE_NAME="${HESTIA_DNS_PROBE_NAME:-github.com}"
DNS_BLOCKED_NAME="${HESTIA_DNS_BLOCKED_NAME:-}"

STATE_DIR="${HESTIA_WATCHDOG_STATE_DIR:-$HOME/.local/state/hestia-watchdog}"
RETRY_WAIT="${HESTIA_WATCHDOG_RETRY_WAIT:-20}"

mkdir -p "$STATE_DIR"

notify() { # $1 title, $2 body, $3 priority, $4 tags
  curl -fsS -m 10 --retry 3 -H "Title: $1" -H "Priority: $3" -H "Tags: $4" \
       -d "$2" "$NTFY_URL" >/dev/null
}

# Reads the previous state for a check and records the new one. Prints the previous.
roll_state() { # $1 check name, $2 current state
  local f="$STATE_DIR/$1" last
  last=$(cat "$f" 2>/dev/null || echo up)
  echo "$2" >"$f"
  echo "$last"
}

# --- brain ------------------------------------------------------------------------------
brain_probe() { curl -fsS -m 10 "$HEALTH_URL" >/dev/null 2>&1; }

if brain_probe; then brain=up; else sleep "$RETRY_WAIT"; if brain_probe; then brain=up; else brain=down; fi; fi
brain_was=$(roll_state brain "$brain")

if [ "$brain" = down ] && [ "$brain_was" != down ]; then
  notify "Hestia: house is DARK" \
         "Brain /health unreachable from off-site (2 probes, ${RETRY_WAIT}s apart). Power? Internet? Tailscale? Checked $(date -u +'%H:%M UTC')." \
         urgent "rotating_light"
elif [ "$brain" = up ] && [ "$brain_was" = down ]; then
  notify "Hestia: house is back" "Brain /health reachable again at $(date -u +'%H:%M UTC')." \
         default "white_check_mark"
fi
echo "watchdog: brain $brain (was $brain_was)"

# --- dns --------------------------------------------------------------------------------
if [ -z "$DNS_SERVER" ]; then
  echo "watchdog: dns SKIPPED (HESTIA_DNS_SERVER unset in the unit)"
  exit 0
fi

# First A record only. dig +short also prints CNAMEs, which are not an answer for our purpose.
dns_answer() { # $1 name
  timeout 8 dig +short +time=3 +tries=1 "@$DNS_SERVER" "$1" A 2>/dev/null \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true
}

# Resolving means a real routable answer. A blocked-style 0.0.0.0 for the probe name would
# mean the resolver is up but answering wrongly, which is still a house-wide outage.
dns_resolves() {
  local a; a=$(dns_answer "$DNS_PROBE_NAME")
  [ -n "$a" ] && [ "$a" != "0.0.0.0" ]
}

if dns_resolves; then dns=up; else sleep "$RETRY_WAIT"; if dns_resolves; then dns=up; else dns=down; fi; fi
dns_was=$(roll_state dns "$dns")

if [ "$dns" = down ] && [ "$dns_was" != down ]; then
  notify "Hestia: house DNS is down" \
         "Resolver $DNS_SERVER did not resolve $DNS_PROBE_NAME (2 probes, ${RETRY_WAIT}s apart). The router has no fallback, so the house is offline. Checked $(date -u +'%H:%M UTC')." \
         urgent "rotating_light"
elif [ "$dns" = up ] && [ "$dns_was" = down ]; then
  notify "Hestia: house DNS is back" "Resolver $DNS_SERVER resolving again at $(date -u +'%H:%M UTC')." \
         default "white_check_mark"
fi
echo "watchdog: dns $dns (was $dns_was)"

# --- dns filtering ----------------------------------------------------------------------
# Only meaningful when resolution works: a dead resolver returns nothing for everything,
# which would otherwise read as "blocked" and hide the real failure behind a false pass.
if [ -z "$DNS_BLOCKED_NAME" ] || [ "$dns" = down ]; then
  exit 0
fi

# Blocked means no answer at all (NXDOMAIN) or the null address. Anything routable means
# filtering is no longer being applied, which is a config regression rather than an outage.
dns_filters() {
  local a; a=$(dns_answer "$DNS_BLOCKED_NAME")
  [ -z "$a" ] || [ "$a" = "0.0.0.0" ]
}

if dns_filters; then filt=up; else filt=down; fi
filt_was=$(roll_state dnsfilter "$filt")

if [ "$filt" = down ] && [ "$filt_was" != down ]; then
  notify "Hestia: DNS filtering is OFF" \
         "$DNS_BLOCKED_NAME resolved to a routable address via $DNS_SERVER, so blocklists are not being applied. Resolution itself is fine. Checked $(date -u +'%H:%M UTC')." \
         high "warning"
elif [ "$filt" = up ] && [ "$filt_was" = down ]; then
  notify "Hestia: DNS filtering is back" "Blocklists applying again at $(date -u +'%H:%M UTC')." \
         default "white_check_mark"
fi
echo "watchdog: dns-filtering $filt (was $filt_was)"
