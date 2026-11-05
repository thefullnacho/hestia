# The pilot box

A Raspberry Pi 5 that runs Hestia for one pilot household for 30 days (PHONE-APP.md, "The pilot
box"). The household's phones run the model; the box holds their data, runs the tools, and
serves the app. The operator sets it up before it arrives; the household installs only the app.

What runs on it: the brain in phone mode, Ollama on the CPU for EmbeddingGemma only (the
library), and a nightly backup to a USB drive. No chat model. No Home Assistant unless the
household has devices worth it (added by hand). No feed and no web search by default.

## What you need

- A Raspberry Pi 5 (8 GB is plenty), its official 27 W power supply, and an NVMe SSD on an NVMe
  HAT. 128 GB or more; the box uses well under 50 GB. Never an SD card for a household: SQLite
  writes all day.
- A USB drive for the nightly backup (32 GB or more; it holds 30 nights).
- The household's Tailscale account (their own; not the operator's).
- The phone model and its chat template, copied from the operator's box.

## 1. Boot from the SSD

Flash Raspberry Pi OS Lite (64-bit) straight onto the SSD with Raspberry Pi Imager, using a USB
adapter, or flash an SD card first and copy it across with the Imager on the Pi. Set the user,
hostname and SSH in the Imager's settings. Then make the Pi try NVMe first:

    sudo rpi-eeprom-config --edit     # set BOOT_ORDER=0xf416, save, reboot

Before it goes to a household, load the drive for 10 minutes and check the kernel log. Power
problems show up as NVMe timeouts or resets under combined load:

    sudo apt-get install -y stress-ng
    stress-ng --cpu 4 --hdd 2 --hdd-bytes 4G --temp-path ~ --timeout 10m   # CPU and SSD together, 10 minutes
    dmesg | grep -i nvme              # "timeout", "reset" or "controller is down" = send it back

## 2. Run the setup

    git clone https://github.com/thefullnacho/hestia ~/hestia
    mkdir -p ~/models/lfm3 && scp <operator box>:models/lfm3/{LFM3-1.7B-QAD-Q4_0.gguf,chat_template.jinja} ~/models/lfm3/
    ~/hestia/deploy/pilot/setup.sh --tz <their zone> --lat <lat> --lon <lon> --phone <name>-iphone \
        --tailnet <their tailnet>

`--dry-run` shows every step without changing anything. The script stops where a person is
needed and says what to do; run it again afterwards and it picks up where it stopped:

- **Tailscale:** `sudo tailscale up --hostname hestia`, logged in with the household's account.
  Setup then shows which tailnet the box joined and stops until `--tailnet` names it, so the box
  never lands on the operator's tailnet by mistake.
- **HTTPS:** in the household's Tailscale admin console, turn on MagicDNS and HTTPS certificates.
- **Backup drive:** label it `HESTIA-BACKUP` (`sudo mkfs.ext4 -L HESTIA-BACKUP /dev/sdX1`, after
  checking the device with `lsblk`: this erases it). It mounts at `/mnt/hestia-backup` on use.

Each `--phone` prints a device token once. Enter it in the app with the box's HTTPS address. To
add a phone later, run setup again with just `--tailnet` and the new `--phone`; settings from the
first run are kept. Setup owns `~/.config/systemd/user/hestia-brain.service.d/pilot.conf`; put
anything set by hand (Home Assistant's `HA_URL` and `HA_TOKEN`) in a `local.conf` beside it.

## 3. Sharing the box

For service during the pilot, the household shares **this one machine** (not their tailnet) with
the operator's tailnet, from their admin console (the machine's menu, Share). A shared machine is
quarantined: it answers connections but cannot start them into the operator's tailnet. Confirm
that on the first box, after the operator has connected to it once: from the Pi, a connection to
an operator machine must fail, for example `nc -zvw3 <operator machine's 100.x address> 22`.
(`tailscale ping` cannot show it: its probe bypasses the filter.) Never Tailscale
Funnel: that puts the box on the public internet.

Households are told, before they start: the operator can reach the box during the pilot, and
because the brain has no login, that access can read their data. The share ends at day 30.

## Checking on it

    curl -s http://<box tailnet ip>:8730/health        # "backend": "phone", and whether a phone is connected
    ~/hestia/deploy/pilot/backup.sh --check            # the last backup and the newest nights
    journalctl --user -u hestia-brain -n 50

## Restoring from the backup

Each night is a folder on the drive named by date, holding `hestia.db.gz` and `memory.tar.gz`;
`files/` is a rolling copy of everything else in `data/` (photos, recipes, library documents,
device files). A file deleted or changed in `data/` keeps its old copy in that night's `deleted/`.

    systemctl --user stop hestia-brain
    rm -f ~/hestia/data/hestia.db-wal ~/hestia/data/hestia.db-shm   # stale WAL would replay over the restore
    zcat /mnt/hestia-backup/hestia/<date>/hestia.db.gz > ~/hestia/data/hestia.db
    tar -xzf /mnt/hestia-backup/hestia/<date>/memory.tar.gz -C ~/hestia
    rsync -a /mnt/hestia-backup/hestia/files/ ~/hestia/data/
    systemctl --user start hestia-brain

## What else runs, and what does not

Only the brain, Ollama and the backup. The daily briefing, the nightly journal, the feed and the
watchers are not installed: their timers are the operator's box's, and each needs its own
decision for a household (the feed needs search, which is off for pilots until they answer).
The backup's result shows on the status page and in Hestia's status answer; a failed or missing
night reads as a problem there.

The brain binds to the tailnet address only. A device on the household's LAN could still reach
that address by adding a route through the Pi (Linux answers on any interface); the operator's own
box has the same exposure. A firewall rule that only lets port 8730 in on `tailscale0` would close
it; not added yet.

## Reminders on a pilot box

The phones schedule reminders themselves (`GET /v1/phone/reminders`) and the box wakes a closed
app with the content-free ping, so a pilot box needs neither Home Assistant nor the reminders
timer. The ping needs the operator's APNs key in `secrets/push.env` on the box
(`deploy/push.env.example`); without it, a reminder made while the app is closed waits until the
app is next opened.

## Day 30

Not automated yet. At day 30 (`data/pilot.json` holds the start date): the household unshares the
box; the problem-report code is deleted from it (SECURITY.md, "Pilot problem reports"; not built
yet, so nothing to delete until it is); and a box that comes back is wiped before reuse.
