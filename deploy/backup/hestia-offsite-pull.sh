#!/usr/bin/env bash
# Runs ON the dedicated off-site box (Ubuntu, youruser@offsite-host.example.net). This is the THIRD,
# off-site copy of Hestia's irreplaceable state — the leg the two home boxes (GPU box -> hl-relay,
# both in the same house) can't provide.
#
# PULL model on purpose: this box reaches into hl-relay read-only and pulls. The home boxes hold
# NO credentials to this restic repo, so a compromise or a runaway script at home cannot reach in
# and delete off-site history. Everything streams straight into an ENCRYPTED restic repo via
# stdin, so nothing plaintext ever lands on this (public, busy) game host.
#
#   1) trigger a fresh config snapshot on hl-relay (HA + *arr),
#   2) stream the latest dated dir (records DB + memories + config) into the encrypted repo,
#   3) apply retention, verify integrity, and ping a healthcheck so a SILENT failure still pages.
set -euo pipefail

REMOTE="${HESTIA_REMOTE:-youruser@relay-host}"          # hl-relay over the tailnet
SSH_KEY="${HESTIA_REMOTE_KEY:-$HOME/.ssh/hestia_offsite}"
REMOTE_BK="${HESTIA_REMOTE_BK:-hestia-backups}"                # dir under youruser's home
REMOTE_SNAP="${HESTIA_REMOTE_SNAP:-\$HOME/hl-relay-config-snapshot.sh}"  # expands on hl-relay

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$HOME/hestia-offsite/repo}"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$HOME/hestia-offsite/.restic-pass}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"

SSH="ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"

ping_fail(){ [ -n "$HEALTHCHECK_URL" ] && curl -fsS -m10 --retry 3 "$HEALTHCHECK_URL/fail" --data-raw "$1" >/dev/null 2>&1 || true; }
fail(){ echo "FATAL: $*" >&2; ping_fail "$*"; exit 1; }

# 1) Anchor on the latest dated dir that actually holds the records DB (the GPU box's push).
#    Config must co-locate with the DB it belongs to, and pulling a DB-less dir is worthless.
STAMP=$($SSH "$REMOTE" "ls -1 $REMOTE_BK/20*/hestia.db 2>/dev/null | sort | tail -1 | sed -E 's#.*/(20[0-9-]+)/hestia.db#\1#'") \
  || fail "could not query remote backups"
# Fall back to the newest dir overall only if no DB-bearing dir exists yet (first-ever run).
[ -n "$STAMP" ] || STAMP=$($SSH "$REMOTE" "ls -1d $REMOTE_BK/20*/ 2>/dev/null | sort | tail -1 | xargs -r -n1 basename")
[ -n "$STAMP" ] || fail "no dated backup dir found on $REMOTE:$REMOTE_BK"

# 2) Fresh config snapshot written INTO that same dated dir (HA + *arr).
$SSH "$REMOTE" "bash $REMOTE_SNAP '$STAMP'" || fail "remote config-snapshot failed"

# 3) Stream that dir straight into the encrypted repo. --sort=name gives stable ordering so
#    restic's content-defined chunking dedups night-over-night (configs barely change).
if ! $SSH "$REMOTE" "tar --sort=name -C $REMOTE_BK -cf - '$STAMP'" \
     | restic backup --stdin --stdin-filename "hestia-$STAMP.tar" --tag offsite --host hestia; then
  fail "restic backup failed for $STAMP"
fi

# 4) Retention + integrity (sample 10% of pack data each run; a full --read-data is the weekly job).
restic forget --tag offsite --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune \
  || fail "restic forget/prune failed"
restic check --read-data-subset=10% || fail "restic check failed"

[ -n "$HEALTHCHECK_URL" ] && curl -fsS -m10 --retry 3 "$HEALTHCHECK_URL" >/dev/null 2>&1 || true
echo "offsite ok: $STAMP -> $RESTIC_REPOSITORY ($(restic snapshots --tag offsite --json 2>/dev/null | grep -c short_id || echo '?') snapshots)"
