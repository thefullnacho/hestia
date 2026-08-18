#!/usr/bin/env bash
# hestia-drift — weekly "reality vs. repo" check. Runs on the GPU box as a user unit.
#
# This project's named defect family is not crashes but *drift*: a placeholder that deploys
# cleanly and looks healthy, or an address pinned in one place while reality moves on.
# Instances on record: the backup unit's placeholder host, hestiactl's placeholder remote,
# the brain unit's bind contradicting what hestiactl probes, and the 2026-08-15 Voice PE
# outage (HA's ESPHome entry pinned to a stale DHCP address for two days). Every one of
# those is mechanically checkable, so that is what this script checks:
#
#   1. unsubstituted deploy placeholders in the installed user units / hestiactl
#   2. brain bind address vs the URL hestiactl probes (mismatch = red on a healthy brain)
#   3. voice satellite: HA's stored ESPHome host vs the device's real mDNS address
#
# Alerting is edge-triggered on the SET of findings: one page when the set changes (new
# drift appears, or everything clears), silence while nothing changes. A persistent finding
# therefore pages exactly once, not weekly.
set -euo pipefail

NTFY_URL="${HESTIA_NTFY_URL:?set HESTIA_NTFY_URL (e.g. https://ntfy.sh/<random-topic>) in the unit}"

# Optional. Voice-satellite check runs only when both are set; skipped loudly otherwise.
DRIFT_RELAY="${HESTIA_DRIFT_RELAY:-}"              # ssh target that can read HA's storage
DRIFT_VOICE_MDNS="${HESTIA_DRIFT_VOICE_MDNS:-}"    # e.g. home-assistant-voice-xxxxxx.local
DRIFT_HA_CONFIG="${HESTIA_DRIFT_HA_CONFIG:-/opt/home/ha_config}"

UNITS_DIR="${HESTIA_DRIFT_UNITS_DIR:-$HOME/.config/systemd/user}"
BRAIN_URL="${HESTIACTL_BRAIN_URL:-http://127.0.0.1:8730}"   # same default as hestiactl
STATE_DIR="${HESTIA_DRIFT_STATE_DIR:-$HOME/.local/state/hestia-drift}"

mkdir -p "$STATE_DIR"
findings=()

# --- 1. unsubstituted placeholders --------------------------------------------------------
# Placeholder markers used across the tracked units/scripts. A hit in an INSTALLED unit
# means the deploy step that was supposed to substitute it never happened.
if compgen -G "$UNITS_DIR/hestia-*.service" >/dev/null; then
  while IFS= read -r f; do
    findings+=("placeholder in installed unit $(basename "$f") — deploy substitution missed")
  done < <(grep -lE 'RELAY_LAN_IP|RELAY_USER|CHANGE-ME|youruser|_TAILNET_IP' "$UNITS_DIR"/hestia-*.service 2>/dev/null || true)
fi

# hestiactl: the placeholder remote only bites when HESTIACTL_REMOTE is not set to override it.
if [ -z "${HESTIACTL_REMOTE:-}" ]; then
  ctl=$(command -v hestiactl || true)
  if [ -n "$ctl" ] && grep -q 'youruser@hl-relay' "$ctl" 2>/dev/null; then
    findings+=("hestiactl remote is the placeholder (HESTIACTL_REMOTE unset) — remote status/logs broken")
  fi
fi

# --- 2. brain bind vs hestiactl probe ------------------------------------------------------
# hestiactl status probes BRAIN_URL; the brain unit binds --host. If those differ (and the
# bind is not a wildcard) the dashboard shows red on a healthy brain — anti-observability:
# it trains the operator to ignore red.
brain_unit="$UNITS_DIR/hestia-brain.service"
if [ -f "$brain_unit" ]; then
  bind=$(grep -oE -- '--host [0-9a-fA-F.:]+' "$brain_unit" | awk '{print $2}' | head -1)
  probe_host=$(echo "$BRAIN_URL" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')
  if [ -n "$bind" ] && [ "$bind" != "0.0.0.0" ] && [ "$bind" != "::" ] && [ "$bind" != "$probe_host" ]; then
    findings+=("brain binds $bind but hestiactl probes $probe_host — status red on a healthy brain")
  fi
fi

# --- 3. voice satellite address drift ------------------------------------------------------
# HA's ESPHome entry stores a pinned host; the device takes whatever DHCP gives it. HA runs
# in a docker bridge, so its mDNS cache never learns the new address — the two-day outage of
# 2026-08-15. Compare the pinned host against the device's live mDNS address on the LAN.
if [ -z "$DRIFT_RELAY" ] || [ -z "$DRIFT_VOICE_MDNS" ]; then
  echo "drift: voice-satellite check SKIPPED (HESTIA_DRIFT_RELAY / HESTIA_DRIFT_VOICE_MDNS unset in the unit)"
else
  stored=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$DRIFT_RELAY" python3 - "$DRIFT_HA_CONFIG" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1] + "/.storage/core.config_entries"))
print(next((e["data"].get("host", "") for e in d["data"]["entries"] if e.get("domain") == "esphome"), ""))
PY
) || stored="SSH-FAILED"
  # ahostsv4: the pinned host in HA is an IPv4; a link-local IPv6 (which plain getent may
  # return first) would be a permanent false positive.
  actual=$(getent ahostsv4 "$DRIFT_VOICE_MDNS" 2>/dev/null | awk '{print $1}' | head -1)
  if [ "$stored" = "SSH-FAILED" ]; then
    findings+=("cannot read HA config on $DRIFT_RELAY over ssh — drift check blind")
  elif [ -z "$actual" ]; then
    findings+=("voice satellite $DRIFT_VOICE_MDNS did not resolve via mDNS — powered? on wifi?")
  elif [ -n "$stored" ] && [ "$stored" != "$actual" ]; then
    findings+=("voice satellite drift: HA dials $stored but the device is $actual — the 2026-08-15 outage shape")
  fi
fi

# --- report --------------------------------------------------------------------------------
for f in ${findings[@]+"${findings[@]}"}; do echo "drift: FINDING: $f"; done
[ ${#findings[@]} -eq 0 ] && echo "drift: no findings"

digest=$(printf '%s\n' ${findings[@]+"${findings[@]}"} | sort | sha256sum | awk '{print $1}')
last=$(cat "$STATE_DIR/digest" 2>/dev/null || echo none)
echo "$digest" >"$STATE_DIR/digest"

if [ "$digest" != "$last" ]; then
  if [ ${#findings[@]} -eq 0 ]; then
    curl -fsS -m 10 --retry 3 -H "Title: Hestia drift: all clear" -H "Priority: default" \
         -H "Tags: white_check_mark" -d "All previously reported drift findings are resolved." \
         "$NTFY_URL" >/dev/null
  else
    body=$(printf '%s\n' ${findings[@]+"${findings[@]}"})
    curl -fsS -m 10 --retry 3 -H "Title: Hestia drift: ${#findings[@]} finding(s)" \
         -H "Priority: high" -H "Tags: warning" \
         -d "$body" "$NTFY_URL" >/dev/null
  fi
  echo "drift: paged (finding set changed)"
else
  echo "drift: finding set unchanged, no page"
fi
