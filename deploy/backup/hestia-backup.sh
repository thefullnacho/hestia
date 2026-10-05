#!/usr/bin/env bash
# Nightly off-box backup of Hestia's irreplaceable state — the records DB (pets, breeding
# lineage, garden) and the learned memories. Everything else (model weights, code) is
# re-downloadable or already in git, so it's deliberately NOT backed up here, unless the
# operator lists it in HESTIA_BACKUP_EXTRA_DIRS (step 3b), for small state that lives outside
# this repo and exists on one disk only.
#
# Ships DATED snapshots (not a mirror) so a corrupted DB can never overwrite the last good
# copy, and keeps KEEP nights of history off-box. The snapshot is integrity-checked before it
# leaves the box, so corruption is never propagated.
#
# Transport: a dedicated key to hl-relay's real sshd over the LAN. (Tailscale SSH on the
# tailnet is check-mode/interactive and cannot run unattended.) The destination lives in the
# systemd unit's Environment= so no host/IP is hardcoded here.
set -euo pipefail

REPO=$HOME/hestia
SRC_DB="$REPO/data/hestia.db"
SRC_MEM="$REPO/memory"

# Both required from the unit — a username default here would be a placeholder that the
# public-repo scrub deploys silently (that exact failure ate three nights of backups once).
REMOTE_USER="${HESTIA_BACKUP_USER:?set HESTIA_BACKUP_USER (hl-relay user) in the unit}"
REMOTE_HOST="${HESTIA_BACKUP_HOST:?set HESTIA_BACKUP_HOST (hl-relay LAN IP) in the unit}"
REMOTE_PORT="${HESTIA_BACKUP_PORT:-22}"
REMOTE_DIR="${HESTIA_BACKUP_DIR:-hestia-backups}"
SSH_KEY="${HESTIA_BACKUP_KEY:-$HOME/.ssh/hestia_backup}"
KEEP="${HESTIA_BACKUP_KEEP:-14}"
# Optional extra directories to ship beside the DB and memories (step 3b): whitespace-separated
# absolute paths, no spaces inside a path. Set it in a systemd drop-in on the box, not here, since
# the paths are the operator's own. Each directory is capped so a wrong entry cannot flood the leg.
EXTRA_DIRS="${HESTIA_BACKUP_EXTRA_DIRS:-}"
EXTRA_MAX_MB="${HESTIA_BACKUP_EXTRA_MAX_MB:-50}"

SSH="ssh -i $SSH_KEY -p $REMOTE_PORT -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
REMOTE="$REMOTE_USER@$REMOTE_HOST"
STAMP=$(date +%Y-%m-%d)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# 1) Consistent ONLINE snapshot of the live SQLite DB (never a raw cp of a file mid-write).
python3 - "$SRC_DB" "$STAGE/hestia.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
PY

# 2) Verify the snapshot is sound BEFORE it leaves the box — don't propagate corruption.
ok=$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" "$STAGE/hestia.db")
[ "$ok" = "ok" ] || { echo "FATAL: snapshot failed integrity_check: $ok" >&2; exit 1; }

# 3) Copy the learned memories (small, plain markdown).
cp -a "$SRC_MEM" "$STAGE/memory"

# 3b) Extra operator-listed directories. Each becomes extra/<name>.tar.gz, working tree and any
#     .git included, so uncommitted edits are covered too. A bad entry must never cost the DB its
#     night: failures are collected, the good parts still ship, and the script exits non-zero at
#     the end so the OnFailure alert pages the phone instead of the gap going quiet.
extra_failed=0
extra_names=""
read -r -a extra_dirs <<<"$EXTRA_DIRS"
if [ "${#extra_dirs[@]}" -gt 0 ]; then
  mkdir -p "$STAGE/extra"
  for dir in "${extra_dirs[@]}"; do
    dir="${dir%/}"
    name=$(basename "$dir")
    if [ -z "$name" ] || [ ! -d "$dir" ]; then
      echo "extra dir missing: $dir" >&2; extra_failed=1; continue
    fi
    if [ -e "$STAGE/extra/$name.tar.gz" ]; then
      echo "extra dir name collides with an earlier entry: $name" >&2; extra_failed=1; continue
    fi
    if [ "$(du -sk "$dir" | cut -f1)" -gt $((EXTRA_MAX_MB * 1024)) ]; then
      echo "extra dir over ${EXTRA_MAX_MB} MB, skipped: $dir" >&2; extra_failed=1; continue
    fi
    if tar -C "$(dirname "$dir")" -czf "$STAGE/extra/$name.tar.gz" "$name"; then
      extra_names="$extra_names $name"
    else
      echo "extra dir tar failed: $dir" >&2; rm -f "$STAGE/extra/$name.tar.gz"; extra_failed=1
    fi
  done
fi

# 4) Ship to a dated dir on hl-relay as a streamed tarball. Plain tar over ssh — no rsync
#    needed on either end, and the payload is tiny (a fresh dated snapshot every night).
tar -C "$STAGE" -cf - . | $SSH "$REMOTE" "mkdir -p '$REMOTE_DIR/$STAMP' && tar -C '$REMOTE_DIR/$STAMP' -xf -"

# 5) Prune: keep only the KEEP most-recent dated snapshots off-box.
$SSH "$REMOTE" "cd '$REMOTE_DIR' && ls -1d 20*/ 2>/dev/null | sort | head -n -$KEEP | tr -d / | xargs -r rm -rf"

if [ "$extra_failed" -ne 0 ]; then
  echo "backup PARTIAL: $STAMP db and memories shipped, but an extra directory failed (see above)" >&2
  exit 1
fi
echo "backup ok: $STAMP -> $REMOTE:$REMOTE_DIR/$STAMP/ (db $(du -h "$STAGE/hestia.db" | cut -f1), $(ls "$STAGE/memory" | wc -l) memory files${extra_names:+, extras:$extra_names})"
