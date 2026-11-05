#!/usr/bin/env bash
# Nightly backup of a pilot box to its own USB drive (PHONE-APP.md, "The pilot box"). It holds
# the household's data, not the operator's, and never leaves the house.
#
# Each night: a dated folder with the database (SQLite's online backup, integrity-checked before
# it is kept, so a corrupt database never replaces a good copy) and the memories; and one rolling
# copy of everything else in data/ (photos, library documents), which is too big to date nightly.
# Keeps KEEP nights. Fails loudly, and writes the outcome to data/backup-status.json for /status.
#
#   deploy/pilot/backup.sh            what the timer runs
#   deploy/pilot/backup.sh --check    report the last backup and the drive, change nothing
set -euo pipefail

REPO="${HESTIA_REPO:-$HOME/hestia}"
DATA="${HESTIA_DATA_DIR:-$REPO/data}"
MEMORY="${HESTIA_MEMORY_DIR:-$REPO/memory}"
DB="${HESTIA_DB:-$DATA/hestia.db}"
MNT="${HESTIA_PILOT_BACKUP_MNT:-/mnt/hestia-backup}"
DEST="${HESTIA_PILOT_BACKUP_DIR:-$MNT/hestia}"
KEEP="${HESTIA_PILOT_BACKUP_KEEP:-30}"
STATUS="$DATA/backup-status.json"

status() {   # ok|failed, message
  local detail=${2//[\"\\]/}   # no quotes or backslashes, so the JSON stays valid
  printf '{"at": "%s", "result": "%s", "detail": "%s"}\n' "$(date -Iseconds)" "$1" "$detail" > "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
}
fail() { status failed "$1"; echo "backup failed: $1" >&2; exit 1; }

if [ "${1:-}" = --check ]; then
  cat "$STATUS" 2>/dev/null || echo "no backup has run yet"
  ls -1d "$DEST"/20* 2>/dev/null | tail -3 || true
  exit 0
fi

# The drive mounts on first access (x-systemd.automount); a missing drive must fail, not fill
# the SSD with a backup "of" itself into an empty mount point.
ls "$MNT" >/dev/null 2>&1 || true
mountpoint -q "$MNT" || fail "no backup drive mounted at $MNT (is the HESTIA-BACKUP drive plugged in?)"
[ -d "$DEST" ] && [ -w "$DEST" ] || fail "$DEST is missing or not writable"
[ -f "$DB" ] || fail "no database at $DB"

night="$DEST/$(date +%F)"
work="$DEST/.incoming-$$"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work"

# The database: SQLite's online backup is consistent while the brain keeps writing.
sqlite3 "$DB" ".backup '$work/hestia.db'" || fail "sqlite backup failed"
check=$(sqlite3 "$work/hestia.db" "PRAGMA integrity_check;") || fail "integrity check could not run"
[ "$check" = ok ] || fail "the snapshot failed its integrity check: $check"
gzip -q "$work/hestia.db"

# The memories: small markdown files, dated with the database.
if [ -d "$MEMORY" ]; then
  tar -czf "$work/memory.tar.gz" -C "$(dirname "$MEMORY")" "$(basename "$MEMORY")" || fail "memory archive failed"
fi

rm -rf "$night"
mv "$work" "$night"
trap - EXIT

# Everything else in data/ (photos, library files, device and tie files): one rolling copy.
# The live database files are skipped; the dated snapshot above is the database's backup.
rsync -a --delete --exclude 'hestia.db*' --exclude 'backup-status.json*' "$DATA/" "$DEST/files/" \
  || fail "copying data/ failed"

# Keep the newest KEEP nights.
ls -1d "$DEST"/20??-??-?? 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf

size=$(du -sh "$DEST" | cut -f1)
status ok "$(basename "$night"), $size on the drive"
echo "backup ok: $night ($size on the drive)"
