#!/usr/bin/env bash
# Nightly backup of a pilot box to its own USB drive (PHONE-APP.md, "The pilot box"). It holds
# the household's data, not the operator's, and never leaves the house.
#
# Each night: a dated folder with the database (SQLite's online backup, integrity-checked before
# it is kept, so a corrupt database never replaces a good copy) and the memories; and one rolling
# copy of everything else in data/ (photos, recipes, library documents), too big to date nightly;
# a file deleted or changed there is kept in that night's deleted/ folder, so it has history too.
# Keeps KEEP nights. Fails loudly, and writes the outcome to data/backup-status.json, which the
# status page and /status show.
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
  local detail
  detail=$(printf %s "$2" | tr -d '"\\' | tr '\n\t\r' '   ')   # stays one valid JSON string
  printf '{"at": "%s", "result": "%s", "detail": "%s"}\n' "$(date -Iseconds)" "$1" "$detail" > "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
}
fail() { status failed "$1"; echo "backup failed: $1" >&2; exit 1; }

[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || fail "HESTIA_PILOT_BACKUP_KEEP must be 1 or more, got $KEEP"

if [ "${1:-}" = --check ]; then
  cat "$STATUS" 2>/dev/null || echo "no backup has run yet"
  ls -1d "$DEST"/20* 2>/dev/null | tail -3 || true
  exit 0
fi

# The drive mounts on first access (x-systemd.automount); a missing drive must fail, not fill
# the SSD with a backup "of" itself into an empty mount point.
ls "$MNT" >/dev/null 2>&1 || true
fs=$(findmnt -no FSTYPE "$MNT" 2>/dev/null | tail -1 || true)
[ -n "$fs" ] && [ "$fs" != autofs ] || fail "no backup drive mounted at $MNT (is the HESTIA-BACKUP drive plugged in?)"
[ -d "$DEST" ] && [ -w "$DEST" ] || fail "$DEST is missing or not writable"
[ -f "$DB" ] || fail "no database at $DB"

night="$DEST/$(date +%F)"
work="$DEST/.incoming-$$"
# Any step that fails without its own message still records the failure: an old "ok" must never
# outlive a failed night.
trap 'status failed "backup.sh stopped at line $LINENO"' ERR
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

# A second run the same night replaces it, keeping the earlier one until the new one is in place.
[ ! -e "$night" ] || mv "$night" "$night.old-$$"
mv "$work" "$night"
trap - EXIT
if [ -d "$night.old-$$/deleted" ]; then mv "$night.old-$$/deleted" "$night/deleted"; fi   # keep that history
rm -rf "$night.old-$$"

# Everything else in data/ (photos, library files, device and tie files): one rolling copy.
# The live database files are skipped; the dated snapshot above is the database's backup.
[ -n "$(ls -A "$DATA")" ] || fail "$DATA is empty; not mirroring an empty folder over the copy"
rsync -a --delete --backup --backup-dir="$night/deleted" \
  --exclude 'hestia.db*' --exclude 'backup-status.json*' "$DATA/" "$DEST/files/" \
  || fail "copying data/ failed"

# Keep the newest KEEP nights.
ls -1d "$DEST"/20??-??-?? 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf

size=$(du -sh "$DEST" | cut -f1)
status ok "$(basename "$night"), $size on the drive"
echo "backup ok: $night ($size on the drive)"
