# Off-site backup — the third copy

The nightly home leg (`hestia-backup.sh`, GPU box → hl-relay) keeps two copies **in the same
house**. This off-site leg adds the third copy on a dedicated box in a datacenter, closing the
3-2-1 gap and — as a bonus — covering the HA + \*arr **config** the home leg deliberately skips.

## Topology

```
GPU box ──02:00 ET push──▶ hl-relay:~/hestia-backups/<DATE>/   (hestia.db + memory/ + extra/)   [home, leg 1+2]
        └──────── same run ──▶ hl-relay:~/hestia-backups/photos/   (one rolling copy of data/photos)
                                      │
dedi (youruser@offsite-host.example.net) ──03:30 ET PULL over Tailscale──┐
  1. ssh hl-relay → hl-relay-config-snapshot.sh <DATE>  (writes  …/<DATE>/config/)
  2. ssh hl-relay → tar <DATE>  ──stdin──▶  encrypted restic repo   [off-site, leg 3]
  3. ssh hl-relay → tar photos  ──stdin──▶  same repo, its own snapshot series (hestia-photos.tar)
```

- **Photos ride separately.** The ingest photos and the identify sightings dataset are too big to
  copy into every dated dir, so hl-relay keeps a single copy, swapped in only once a night's
  stream fully arrives. Restic stores each unchanged photo once, so their off-site history costs
  about their real size.

- **Pull, not push.** dedi reaches into hl-relay read-only; the home boxes hold **no**
  credentials to the restic repo, so a compromise at home cannot delete off-site history.
- **Encrypted at rest.** dedi is a busy, public-facing box (it also hosts a Pterodactyl game
  panel), so nothing plaintext ever lands on it — the tar streams straight into restic via stdin.
- **DB-anchored.** The puller targets the latest dir that actually contains `hestia.db` and tells
  the config snapshot to write into *that* dir, so config + DB are always co-located and complete
  (robust to the GPU box being on ET and hl-relay on UTC).

## The repository lives on a storage box (2026-11-20)

The restic repository moved off the dedi's disk to a Hetzner Storage Box: sub-account `-sub1`
(its own folder; Dawn's backups use `-sub2`, so one leaked key can't reach the other). The dedi
still runs the pull, so home still holds no credentials. It reaches the box with a key used for
nothing else (`~/.ssh/storagebox`, port 23, an alias `storagebox` in `~/.ssh/config`), and a
drop-in sets `RESTIC_REPOSITORY=sftp:storagebox:restic`
(`/etc/systemd/system/hestia-offsite-pull.service.d/storagebox.conf`; delete it to point back).
The box's own daily snapshots are on, and they can't be deleted over SFTP, so even a
compromised puller can't erase history. The move was an rsync of the repository as is (167
snapshots, `restic check` clean), proved by restoring a night's `hestia.db` from the box
(`integrity_check` ok, all library passages present). The pull job moves to a small VPS when the
dedi is retired; nothing else changes.

## Household files (the ZFS mirror on hl-relay)

The household's own files on hl-relay's ZFS mirror (`tank`) go off-site by a separate nightly job,
`tank-offsite.sh` (+ `.service`, `.timer`, installed as system units on hl-relay). It mounts the
newest sanoid snapshot of each dataset read-only at a fixed path and runs `restic backup` into
the household's own storage-box sub-account, then forget/prune and a 5% read check. Time Machine
is left out: it is a backup of a laptop whose files are already here. This one pushes, with a key
that reaches only its sub-account; the storage box's daily snapshots are the guard against a
compromised box erasing history. The repository password lives on hl-relay in root's
`.restic-family-pass` and in the operator's password manager: without it the copy is unreadable.

## Components

| Where | File | Role |
|---|---|---|
| repo | `deploy/backup/hl-relay-config-snapshot.sh` | deployed to `hl-relay:~/`; tars HA (via `docker exec`, it runs as root) + \*arr (their own backup zips) into `<DATE>/config/` |
| repo | `deploy/backup/hestia-offsite-pull.sh` | deployed to `dedi:~/hestia-offsite/`; orchestrates snapshot + pull + retention + check |
| dedi | `/etc/systemd/system/hestia-offsite-pull.{service,timer}` | nightly 03:30 ET |
| dedi | `~/hestia-offsite/repo` | encrypted restic repo |
| dedi | `~/hestia-offsite/.restic-pass` | repo password (automation copy) |
| home | `secrets/restic-offsite.pass` | repo password (escrow copy) — **also keep in a password manager** |
| hl-relay | `~/.ssh/authorized_keys` | dedi's `hestia-offsite-pull@offsite` ed25519 key (`restrict`) |

Retention: `--keep-daily 14 --keep-weekly 8 --keep-monthly 12`. Each run verifies a 10% data
sample (`restic check --read-data-subset=10%`).

## Restore (disaster recovery)

From the dedi box (or any box with the repo + password):

```bash
export RESTIC_REPOSITORY=~/hestia-offsite/repo
export RESTIC_PASSWORD_FILE=~/hestia-offsite/.restic-pass   # or paste from password manager
restic snapshots                       # list available nights (two series: hestia-<DATE>.tar and hestia-photos.tar)
restic restore <ID> --target /tmp/r    # the newest hestia-<DATE>.tar snapshot's ID
tar -xf /tmp/r/*.tar -C /tmp/r          # → /tmp/r/<DATE>/{hestia.db, memory/, extra/, config/}
```

Then put state back:
- **records DB** → `/tmp/r/<DATE>/hestia.db` → copy to the GPU box `~/hestia/data/hestia.db` (stop the brain first).
- **memories** → `/tmp/r/<DATE>/memory/` → copy to `~/hestia/memory/`.
- **extra dirs** (only if `HESTIA_BACKUP_EXTRA_DIRS` is set) → `/tmp/r/<DATE>/extra/<name>.tar.gz` holds the
  directory by its own name, working tree and `.git` included. Restore with
  `tar -xzf extra/<name>.tar.gz -C <parent of the original directory>`. A failed or oversized entry
  makes the nightly run exit non-zero and page the phone, though the DB and memories still ship.
- **photos** → `restic restore latest --path /hestia-photos.tar --target /tmp/p`, then
  `tar -xf /tmp/p/hestia-photos.tar -C /tmp/p` and copy `/tmp/p/photos/` to `~/hestia/data/photos/`.
  The sightings table in `hestia.db` points at these files by relative path.
- **HA** → `tar -xzf config/ha_config.tar.gz -C /opt/home/ha_config` on hl-relay (HA stopped).
- **\*arr** → in each app's UI, System → Backup → Restore, upload `config/<app>/<app>_backup_*.zip`.
- **adguard** → `tar -xzf config/adguard.tar.gz -C /opt/home/adguard/confdir`.

If the repo itself is gone, you only need `RESTIC_REPOSITORY` + the **password** to read any
surviving copy — which is why the password is escrowed in two places besides the box.

## Alerting — nothing fails silently

Lesson learned the hard way: the home leg once broke for four nights while the off-site leg
kept reporting "ok" (it was re-archiving a stale snapshot). Three guards now close that:

1. **Staleness guard** (in `hestia-offsite-pull.sh`) — the pull FAILS if the newest DB-bearing
   snapshot is older than `HESTIA_MAX_STAMP_AGE_DAYS` (default 2), so a broken home leg breaks
   the off-site leg loudly instead of being papered over.
2. **`OnFailure=hestia-alert@%n.service`** (`deploy/watchdog/hestia-alert@.service`) — any
   hooked unit that fails pushes to the phone via an [ntfy](https://ntfy.sh) topic. Installed
   as a system unit on dedi (hooked to the pull) and a user unit on the GPU box (hooked to the
   nightly push). The topic name is a *credential* — random, never committed.
3. **Off-site watchdog** (`deploy/watchdog/hestia-watchdog.{sh,service,timer}`, on dedi, every
   5 min) — probes the brain's `/health` over the tailnet and pages on house-dark / recovered
   transitions only. This is the one alert the house cannot send about itself.

No placeholder defaults anywhere: both backup scripts now *require* their user/host from the
unit's `Environment=`, because a scrubbed-for-public default deploys silently and fails at 3am.

## Lost the box entirely?

The repo is on dedi's disk. If dedi dies, the off-site copy is gone but the two home copies
remain. To re-establish off-site: provision any box, `restic init` a fresh repo (reuse the
escrowed password or a new one), re-add its pull key to hl-relay, drop the two scripts + units.
