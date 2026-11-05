#!/usr/bin/env bash
# Turn a fresh Raspberry Pi 5 into a pilot Hestia box (PHONE-APP.md, "The pilot box").
#
# Run on the Pi, as the user the box will run under (not root), from a clone at ~/hestia:
#   git clone https://github.com/thefullnacho/hestia ~/hestia
#   ~/hestia/deploy/pilot/setup.sh --tz America/New_York --lat 35.6 --lon -82.5 --phone sister-iphone
#
# Safe to run again: every step checks before it acts. It never formats, wipes or partitions a
# disk. Steps that need a person (the household's Tailscale login, labelling the backup drive)
# stop with what to do, and the next run carries on from there. See deploy/pilot/README.md.
#
# What the box runs: the brain in phone mode (the model is on the household's phones), Ollama
# on the CPU for EmbeddingGemma only (the library), and a nightly backup to a USB drive.
# No chat model, no Home Assistant unless added by hand, no feed or search by default.
set -euo pipefail

REPO="$HOME/hestia"
UNITS="$HOME/.config/systemd/user"
MODELS="${HESTIA_PILOT_MODELS:-$HOME/models/lfm3}"
MODEL_FILE="LFM3-1.7B-QAD-Q4_0.gguf"
TEMPLATE_FILE="chat_template.jinja"
BACKUP_LABEL="HESTIA-BACKUP"
BACKUP_MNT="/mnt/hestia-backup"
PORT=8730

TZ_WANT="" LAT="" LON="" TAILNET="" ALLOW_SD=0 DRY=0
PHONES=()

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options:
  --tz ZONE        IANA time zone of the household (reminders and the briefing use local time)
  --lat N --lon N  the house's coordinates, for the weather tool (only lat/lon leave the box)
  --tailnet NAME   the household's tailnet, as Tailscale names it; setup stops until it matches,
                   so the box never ends up on the operator's tailnet by mistake
  --phone NAME     mint a device token for a household phone (repeatable); printed once
  --allow-sd       run from an SD card anyway (testing only: SQLite writes all day wear it out)
  --dry-run        say what would be done, change nothing
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --tz|--lat|--lon|--tailnet|--phone)
      [ $# -ge 2 ] || { echo "$1 needs a value" >&2; usage >&2; exit 2; } ;;
  esac
  case "$1" in
    --tz) TZ_WANT="$2"; shift 2 ;;
    --lat) LAT="$2"; shift 2 ;;
    --lon) LON="$2"; shift 2 ;;
    --tailnet) TAILNET="$2"; shift 2 ;;
    --phone) PHONES+=("$2"); shift 2 ;;
    --allow-sd) ALLOW_SD=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
todo() { printf '  \033[33m→\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then printf '    would run: %s\n' "$*"; else "$@"; fi; }

# --- 1. the machine ------------------------------------------------------------------------------
step "1. The machine"
[ "$(id -u)" != 0 ] || die "run as the box's normal user, not root (it uses sudo where needed)"
[ "$(uname -m)" = aarch64 ] || die "expected a 64-bit Raspberry Pi OS (aarch64), got $(uname -m)"
if grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null; then
  ok "$(tr -d '\0' < /proc/device-tree/model)"
else
  todo "not a Pi 5 ($(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)); carrying on"
fi
[ "$(realpath "$(dirname "$0")/../..")" = "$(realpath "$REPO")" ] \
  || die "the repo must live at $REPO (the systemd units expect it there)"

root_dev=$(findmnt -no SOURCE /)
case "$root_dev" in
  /dev/nvme*) ok "booting from the SSD ($root_dev)" ;;
  /dev/mmcblk*)
    if [ "$ALLOW_SD" = 1 ]; then
      todo "running from the SD card ($root_dev), allowed by --allow-sd: not for a household"
    else
      die "running from the SD card ($root_dev). Flash Raspberry Pi OS onto the SSD and boot from it
      (README.md, 'Boot from the SSD'), or pass --allow-sd to test only"
    fi ;;
  *) todo "root is on $root_dev; expected an NVMe SSD" ;;
esac

if [ -n "$TZ_WANT" ]; then
  if [ "$(timedatectl show -p Timezone --value)" != "$TZ_WANT" ]; then
    run sudo timedatectl set-timezone "$TZ_WANT"
  fi
  ok "time zone $TZ_WANT"
elif [ "$(timedatectl show -p Timezone --value)" = "Etc/UTC" ]; then
  todo "the time zone is UTC: pass --tz with the household's zone, or reminders land at the wrong hour"
else
  ok "time zone $(timedatectl show -p Timezone --value)"
fi

# --- 2. packages ---------------------------------------------------------------------------------
step "2. Packages"
need=()
for pkg in git curl sqlite3 poppler-utils rsync zstd; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg")
done
if [ ${#need[@]} -gt 0 ]; then
  run sudo apt-get update -q
  run sudo apt-get install -y -q "${need[@]}"
fi
ok "git, curl, sqlite3, poppler-utils (pdftotext, for the library), rsync"

if [ ! -x "$HOME/.local/bin/uv" ]; then
  run sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
ok "uv"
run "$HOME/.local/bin/uv" sync -q --project "$REPO/brain"
ok "the brain's Python environment"

# --- 3. Ollama, for EmbeddingGemma only ------------------------------------------------------------
step "3. Ollama (EmbeddingGemma for the library; no chat model on this box)"
if ! command -v ollama >/dev/null; then
  run sh -c 'curl -fsSL https://ollama.com/install.sh | sh'
fi
# The installer adds a system-wide service; the box runs Ollama as the user's unit instead, like
# the operator's box, so hestiactl and the brain's Wants= find it. Localhost only either way.
# Masked, not disabled: upgrading through install.sh re-enables a merely disabled service, which
# would take port 11434 as the ollama user, without embeddinggemma.
if [ "$(systemctl is-enabled ollama 2>/dev/null || true)" != masked ] && systemctl cat ollama >/dev/null 2>&1; then
  run sudo systemctl mask --now ollama
fi
ok "ollama installed"

# --- 4. the phone model the app downloads ------------------------------------------------------------
step "4. The phone model (served to the household's phones; the box never runs it)"
if [ -f "$MODELS/$MODEL_FILE" ] && [ -f "$MODELS/$TEMPLATE_FILE" ]; then
  ok "$MODELS/$MODEL_FILE and $TEMPLATE_FILE"
else
  todo "copy the model and its chat template from the operator's box, then run this again:"
  todo "  mkdir -p $MODELS && scp <operator box>:models/lfm3/{$MODEL_FILE,$TEMPLATE_FILE} $MODELS/"
  [ "$DRY" = 1 ] || exit 1
fi

# --- 5. Tailscale: the household's own tailnet --------------------------------------------------------
step "5. Tailscale (the household's own tailnet: PHONE-APP.md, 'Each household runs its own tailnet')"
if ! command -v tailscale >/dev/null; then
  run sh -c 'curl -fsSL https://tailscale.com/install.sh | sh'
fi
TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
if [ -z "$TS_IP" ]; then
  todo "log the box into the HOUSEHOLD's tailnet (their account, not the operator's):"
  todo "  sudo tailscale up --hostname hestia"
  todo "then run this again"
  [ "$DRY" = 1 ] || exit 1
  TS_IP=100.64.0.1   # a placeholder for the dry run only
fi
if [ "$DRY" = 0 ]; then
  read -r joined login < <(tailscale status --json | python3 -c 'import json, sys
s = json.load(sys.stdin); me = s["Self"]
print((s.get("CurrentTailnet") or {}).get("Name", "?"), s.get("User", {}).get(str(me.get("UserID")), {}).get("LoginName", "?"))')
  if [ "$joined" != "$TAILNET" ]; then
    todo "the box is on the tailnet \"$joined\" (logged in as $login)."
    todo "if that is the household's own tailnet, run again with  --tailnet $joined"
    todo "if not: sudo tailscale logout, and log in with the household's account"
    exit 1
  fi
fi
ok "on the household's tailnet ${TAILNET:-?} at $TS_IP"

# --- 6. the brain and Ollama as user services --------------------------------------------------------
step "6. Services"
run mkdir -p "$UNITS/hestia-brain.service.d" "$UNITS/hestia-ollama.service.d" "$REPO/data" "$REPO/memory"
# Settings from an earlier run survive a rerun that does not repeat them (adding a phone later).
# pilot.conf is this script's; anything set by hand (Home Assistant) goes in local.conf beside it.
prev="$UNITS/hestia-brain.service.d/pilot.conf"
[ -n "$LAT" ] || LAT=$(sed -n 's/^Environment=HESTIA_LAT=//p' "$prev" 2>/dev/null || true)
[ -n "$LON" ] || LON=$(sed -n 's/^Environment=HESTIA_LON=//p' "$prev" 2>/dev/null || true)
units_sum() { { cat "$UNITS/hestia-brain.service" "$prev" 2>/dev/null || true; } | sha256sum; }   # none yet on a first run
before=$(units_sum)
if [ "$DRY" = 0 ]; then
  sed "s/^ExecStart=\(.*\)TAILSCALE_IP/ExecStart=\1$TS_IP/" "$REPO/deploy/systemd/hestia-brain.service" > "$UNITS/hestia-brain.service"
  cp "$REPO/deploy/systemd/hestia-ollama.service" "$UNITS/hestia-ollama.service"
  cp "$REPO/deploy/pilot/systemd/hestia-pilot-backup.service" "$REPO/deploy/pilot/systemd/hestia-pilot-backup.timer" "$UNITS/"
  {
    echo "# Written by deploy/pilot/setup.sh: the brain in phone mode, the model on the phones."
    echo "[Service]"
    echo "Environment=HESTIA_BACKEND=phone"
    echo "Environment=HESTIA_PHONE_MODEL=$MODELS/$MODEL_FILE"
    echo "Environment=HESTIA_CHAT_TEMPLATE=$MODELS/$TEMPLATE_FILE"
    echo "Environment=HESTIA_BOS_TOKEN=<|startoftext|>"
    echo "# setup.sh already synced the environment; boot must not need PyPI"
    echo "Environment=UV_NO_SYNC=1"
    [ -z "$LAT" ] || echo "Environment=HESTIA_LAT=$LAT"
    [ -z "$LON" ] || echo "Environment=HESTIA_LON=$LON"
  } > "$UNITS/hestia-brain.service.d/pilot.conf"
  {
    echo "# Written by deploy/pilot/setup.sh: CPU only, EmbeddingGemma stays loaded (about 0.6 GB)."
    echo "[Service]"
    echo "Environment=CUDA_VISIBLE_DEVICES="
  } > "$UNITS/hestia-ollama.service.d/pilot.conf"
else
  printf '    would write: %s\n' "$UNITS/hestia-brain.service (bound to $TS_IP:$PORT)" \
    "$UNITS/hestia-brain.service.d/pilot.conf (phone mode)" "$UNITS/hestia-ollama.service(.d)" \
    "$UNITS/hestia-pilot-backup.{service,timer}"
fi
# User services start at boot, with nobody logged in.
loginctl show-user "$USER" -p Linger --value 2>/dev/null | grep -q yes || run sudo loginctl enable-linger "$USER"
run systemctl --user daemon-reload
run systemctl --user enable --now hestia-ollama.service
for _ in $(seq 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; [ "$DRY" = 1 ] && break; sleep 1; done
if [ "$DRY" = 0 ] && ! curl -sf http://127.0.0.1:11434/api/tags | grep -q embeddinggemma; then
  run ollama pull embeddinggemma
fi
ok "ollama running, embeddinggemma pulled"
run systemctl --user enable --now hestia-brain.service
if [ "$DRY" = 0 ] && [ "$before" != "$(units_sum)" ]; then
  systemctl --user try-restart hestia-brain.service   # a rerun's new settings take effect now
fi
if [ "$DRY" = 0 ]; then
  for _ in $(seq 60); do curl -sf "http://$TS_IP:$PORT/health" >/dev/null && break; sleep 1; done
  health=$(curl -sf "http://$TS_IP:$PORT/health") || die "the brain did not come up: journalctl --user -u hestia-brain -n 50"
  echo "$health" | grep -q '"backend":"phone"' || die "the brain is up but not in phone mode: $health"
fi
ok "brain up in phone mode on $TS_IP:$PORT (tailnet only)"

# --- 7. HTTPS for the app ------------------------------------------------------------------------------
step "7. HTTPS for the app (iOS needs TLS; tailnet only, never Funnel)"
if [ "$DRY" = 1 ]; then
  run sudo tailscale serve --bg --https=443 "http://$TS_IP:$PORT"
elif sudo timeout 20 tailscale serve --bg --https=443 "http://$TS_IP:$PORT" >/dev/null; then
  ok "https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
else
  todo "tailscale serve failed: in the household's Tailscale admin console, turn on MagicDNS and"
  todo "HTTPS certificates (DNS page), then run this again"
fi

# --- 8. the nightly backup ---------------------------------------------------------------------------
step "8. Nightly backup to a USB drive labelled $BACKUP_LABEL"
if ! grep -q "LABEL=$BACKUP_LABEL" /etc/fstab; then
  run sudo mkdir -p "$BACKUP_MNT"
  if [ "$DRY" = 1 ]; then
    run sudo tee -a /etc/fstab "<<< LABEL=$BACKUP_LABEL $BACKUP_MNT auto nofail,noatime,x-systemd.automount,x-systemd.device-timeout=10s 0 2"
  else
    sudo cp /etc/fstab /etc/fstab.hestia-bak
    [ -z "$(tail -c1 /etc/fstab)" ] || echo | sudo tee -a /etc/fstab >/dev/null
    echo "LABEL=$BACKUP_LABEL $BACKUP_MNT auto nofail,noatime,x-systemd.automount,x-systemd.device-timeout=10s 0 2" \
      | sudo tee -a /etc/fstab >/dev/null
    if ! sudo findmnt --verify --tab-file /etc/fstab >/dev/null 2>&1; then
      sudo cp /etc/fstab.hestia-bak /etc/fstab
      die "the new /etc/fstab did not verify; put the old one back (findmnt --verify to see why)"
    fi
    sudo systemctl daemon-reload
  fi
fi
if lsblk -no LABEL 2>/dev/null | grep -qx "$BACKUP_LABEL"; then
  fstype=$(lsblk -no LABEL,FSTYPE | awk -v l="$BACKUP_LABEL" '$1 == l {print $2; exit}')
  [ "$fstype" = ext4 ] || die "the $BACKUP_LABEL drive is $fstype; it must be ext4 (ownership and rsync -a need it)"
  # Start the automount now (daemon-reload alone does not) and touch it, so the folder is made on
  # the drive, never in the empty mount point on the SSD.
  run sudo systemctl start "$(systemd-escape -p --suffix=automount "$BACKUP_MNT")"
  ls "$BACKUP_MNT" >/dev/null 2>&1 || true
  if [ "$DRY" = 0 ] && [ "$(findmnt -no FSTYPE "$BACKUP_MNT" | tail -1)" != ext4 ]; then
    die "the $BACKUP_LABEL drive did not mount at $BACKUP_MNT (findmnt $BACKUP_MNT)"
  fi
  run sudo mkdir -p "$BACKUP_MNT/hestia"
  run sudo chown "$USER:" "$BACKUP_MNT/hestia"
  ok "backup drive mounted at $BACKUP_MNT"
else
  todo "no drive labelled $BACKUP_LABEL. Plug one in and label it (this ERASES that drive, check the"
  todo "device name with lsblk first):  sudo mkfs.ext4 -L $BACKUP_LABEL /dev/sdX1   then run this again"
fi
run systemctl --user enable --now hestia-pilot-backup.timer
ok "backup timer on (nightly; keeps 30 nights)"

# --- 9. the household's phones --------------------------------------------------------------------------
step "9. Phones"
for name in "${PHONES[@]}"; do
  if [ "$DRY" = 1 ]; then
    run "$HOME/.local/bin/uv" run --project "$REPO/brain" python "$REPO/brain/phone.py" add-device "$name"
  elif (cd "$REPO/brain" && "$HOME/.local/bin/uv" run -q python -c \
        'import phone, sys; sys.exit(0 if sys.argv[1] in phone._devices() else 1)' "$name"); then
    ok "$name already has a token (phone.py add-device makes a new one; the old stops working)"
  else
    echo "  device token for $name (shown once; enter it in the app):"
    (cd "$REPO/brain" && "$HOME/.local/bin/uv" run -q python phone.py add-device "$name")
  fi
done
[ ${#PHONES[@]} -gt 0 ] || todo "no --phone given: mint tokens later with  cd ~/hestia/brain && uv run python phone.py add-device <name>"

# --- 10. the pilot's clock ----------------------------------------------------------------------------
step "10. The pilot"
if [ ! -f "$REPO/data/pilot.json" ] && [ "$DRY" = 0 ]; then
  printf '{"started": "%s"}\n' "$(date +%F)" > "$REPO/data/pilot.json"
fi
ok "pilot started $(sed -n 's/.*"started": "\(.*\)".*/\1/p' "$REPO/data/pilot.json" 2>/dev/null || date +%F); day 30 is the end (README.md, 'Day 30')"
echo
echo "Done. Still by hand: share this one machine with the operator's tailnet from the household's"
echo "admin console, and confirm it is quarantined (README.md, 'Sharing the box')."
