#!/usr/bin/env bash
# Runs ON hl-relay (as youruser — no sudo here). Captures the hand-tuned CONFIG the main
# records-DB backup deliberately skips: Home Assistant (.storage, yaml, HACS custom_components)
# and the *arr stack. Writes into TODAY's dated dir under hestia-backups/, alongside the records
# DB + memories the GPU box already ships, so one dated dir holds everything.
#
# Ownership gotcha: Home Assistant runs as ROOT inside its container, so its host files (e.g.
# .storage/auth) are root-owned and unreadable to youruser. There is no passwordless sudo
# here, so HA is read AS ROOT from INSIDE the container via the docker group. The *arr apps run
# as PUID 1000 (= youruser), so their files are read directly; for those we grab each app's
# OWN scheduled backup zip (a consistent DB dump its "Restore Backup" reads) plus config.xml.
#
# Re-derivable bulk is excluded: HA's recorder history DB, logs, pip deps, caches, media.
# Invoked over ssh by the off-site puller right before it pulls, so the snapshot is always fresh.
set -uo pipefail   # NOT -e: capture everything we can, then fail loudly only if HA is missing.

HOME_ROOT="${HESTIA_HOME_ROOT:-/opt/home}"
BK_ROOT="${HESTIA_BACKUP_DIR:-$HOME/hestia-backups}"
# The puller passes the dated dir that holds the records DB (the GPU push), so config co-locates
# with the DB it belongs to — robust to the GPU box (ET) and this host (UTC) disagreeing on the
# day's name. Falls back to today only if invoked standalone.
STAMP="${1:-$(date +%F)}"
DEST="$BK_ROOT/$STAMP/config"
mkdir -p "$DEST"

warn(){ echo "WARN: $*" >&2; }

# --- Home Assistant: read as root from inside the container; skip the recorder DB / logs / deps.
HA_EXCL='home-assistant_v2\.db([-.].*)?|home-assistant\.log.*|deps|tts|\.cache|\.cloud'
if [ "$(docker inspect -f '{{.State.Running}}' hl-ha 2>/dev/null)" = "true" ]; then
  if docker exec hl-ha sh -c "cd /config && tar czf - \$(ls -A | grep -vxE '$HA_EXCL')" > "$DEST/ha_config.tar.gz" \
     && [ -s "$DEST/ha_config.tar.gz" ]; then
    :
  else
    warn "HA capture failed"; rm -f "$DEST/ha_config.tar.gz"
  fi
else
  warn "container hl-ha not running — HA config NOT captured"
fi

# --- Servarr apps: their own consistent backup zip + config.xml (port, API key). Host-readable.
for app in sonarr radarr prowlarr lidarr; do
  cfg="$HOME_ROOT/arr/$app/config"; [ -d "$cfg" ] || { warn "$app config dir missing"; continue; }
  out="$DEST/$app"; mkdir -p "$out"
  cp -a "$cfg/config.xml" "$out/" 2>/dev/null || warn "$app config.xml missing"
  newest=$(ls -1t "$cfg"/Backups/scheduled/*.zip 2>/dev/null | head -1)
  if [ -n "$newest" ]; then
    cp -a "$newest" "$out/"
  else
    warn "$app has no scheduled backup zip — tarring config dir instead"
    tar --sort=name --exclude='*.db-wal' --exclude='*.db-shm' --exclude='logs.db*' \
        --exclude='*.log' -C "$cfg" -czf "$out/config.tar.gz" . || warn "$app config tar failed"
  fi
done

# --- bazarr / slskd / adguard: small configs, host-readable, plain tar (DBs re-derive upstream).
[ -d "$HOME_ROOT/arr/bazarr/config" ] && { tar --sort=name --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.log' -C "$HOME_ROOT/arr/bazarr/config" -czf "$DEST/bazarr.tar.gz" . || warn "bazarr tar failed"; }
[ -d "$HOME_ROOT/arr/slskd/config" ] && { tar --sort=name --exclude='*.log' \
    -C "$HOME_ROOT/arr/slskd/config" -czf "$DEST/slskd.tar.gz" . || warn "slskd tar failed"; }
# AdGuardHome also writes its yaml as root in-container, so read it from inside like HA.
if [ "$(docker inspect -f '{{.State.Running}}' adguardhome 2>/dev/null)" = "true" ]; then
  docker exec adguardhome tar czf - -C /opt/adguardhome/conf . > "$DEST/adguard.tar.gz" \
    && [ -s "$DEST/adguard.tar.gz" ] || { warn "adguard capture failed"; rm -f "$DEST/adguard.tar.gz"; }
fi

# HA is the irreplaceable one (no self-backup like the *arr apps). Treat its absence as fatal.
[ -s "$DEST/ha_config.tar.gz" ] || { echo "FATAL: HA config not captured" >&2; exit 1; }
echo "config-snapshot ok: $STAMP -> $DEST ($(du -sh "$DEST" | cut -f1), $(find "$DEST" -type f | wc -l) files)"
