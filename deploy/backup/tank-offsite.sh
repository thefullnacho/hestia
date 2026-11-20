#!/usr/bin/env bash
# Nightly off-site copy of a household's ZFS datasets (hl-relay's tank) into an encrypted restic
# repository on a storage box. Runs as root on the box that holds the pool.
#
# Reads the newest sanoid snapshot of each dataset, mounted read-only at a FIXED path, never the
# live folders: a file someone is mid-way through saving can't land half-written, and a fixed
# path lets restic find last night's snapshot as its parent and skip unchanged files.
#
# This is a push: the box holds a key that reaches only its own storage-box sub-account. The
# storage box's own daily snapshots (not deletable over SFTP) are what stop a compromised box
# from erasing history.
#
#   RESTIC_REPOSITORY      e.g. sftp:storagebox-family:restic   (REQUIRED, no default)
#   RESTIC_PASSWORD_FILE   e.g. /root/.restic-family-pass       (REQUIRED, no default)
#   DATASETS               e.g. "tank/dawn tank/photos"         (REQUIRED, no default)
#   STAGE                  where snapshots are mounted (default /mnt/tank-offsite)
set -euo pipefail
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY}" "${RESTIC_PASSWORD_FILE:?set RESTIC_PASSWORD_FILE}"
: "${DATASETS:?set DATASETS}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
STAGE="${STAGE:-/mnt/tank-offsite}"

mounted=()
cleanup() { for m in "${mounted[@]}"; do umount "$m" 2>/dev/null || true; done; }
trap cleanup EXIT

paths=()
for ds in $DATASETS; do
  # The newest daily snapshot; on the first day, before a daily exists, the newest hourly.
  snap=$(zfs list -H -t snapshot -o name -s creation "$ds" | grep -E '@autosnap_.*_daily$' | tail -1)
  [ -n "$snap" ] || snap=$(zfs list -H -t snapshot -o name -s creation "$ds" | grep -E '@autosnap_' | tail -1)
  [ -n "$snap" ] || { echo "tank-offsite: no snapshot of $ds yet" >&2; exit 1; }
  dir="$STAGE/${ds//\//_}"
  mkdir -p "$dir"
  umount "$dir" 2>/dev/null || true
  mount -t zfs -o ro "$snap" "$dir"
  mounted+=("$dir")
  paths+=("$dir")
  echo "tank-offsite: $snap"
done

restic backup --tag tank --host "$(hostname)" "${paths[@]}"
restic forget --tag tank --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
restic check --read-data-subset=5%
echo "tank-offsite ok: $(restic snapshots --tag tank --json | grep -o '"short_id"' | wc -l) snapshots"
