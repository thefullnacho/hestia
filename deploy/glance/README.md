# The Hestia dashboard (Glance on hl-relay)

At-a-glance tiles for the whole house: hyperlinked service cards with live up/down, the
brain's own health, the note-taker's review queue, RSS, and a garden forecast. Runs on
hl-relay — metal we own — not the dedi rental.

**URL:** `http://hl-relay:8082` (tailnet or LAN)

## Why Glance, and why it isn't forked

Glance's `custom-api` widget renders arbitrary JSON with Go templates, so everything
Hestia-specific is a config block, not a patch. Nothing here is a fork, and upgrading is a
tag bump in `compose.yml`.

The obvious alternative was [gethomepage](https://gethomepage.dev), which ships native
widgets for the exact stack running here — Sonarr, Radarr, Lidarr, Prowlarr, Bazarr,
qBittorrent, Plex, Home Assistant. It lost anyway, for two reasons:

1. **`status.snapshot()` already is the integration.** The brain probes every one of those
   services and serves the result at `/status`. Homepage's integration depth solves a problem
   that was solved in June, and re-probing from a second place would let the dashboard and the
   spoken "is everything ok?" answer drift apart.
2. **Homepage has no RSS widget.** Glance's is first-class.

Homarr was ruled out on footprint: it idles at 100–150MB and climbs with widgets, and hl-relay
has been taken down once by swap thrash (see `hestia-hl-relay-perf-incident`). Glance is a
single Go binary.

## Layout

| Widget | Source | Notes |
|---|---|---|
| Brain | `${BRAIN}/status` | ollama/model/grabs, per-GPU VRAM bars, load·ram·disk·swap |
| Weather | Glance native | glanceable forecast |
| Garden watch | Open-Meteo | same lat/lon as the brain's `weather` tool; rain ≥0.25" and lows ≤36°F flagged |
| Services | Glance `monitor` | 12 hyperlinked tiles, live probes |
| Memory queue | `${BRAIN}/memory/inbox` | pending note-taker proposals, **read-only** |
| Feeds / Upstream / Reddit | Glance native | |

## Deploy

```sh
ssh thefullnacho@hl-relay 'mkdir -p /opt/home/glance'
scp compose.yml glance.yml thefullnacho@hl-relay:/opt/home/glance/
# then create glance.env from glance.env.example (it is gitignored — this repo is public)
ssh thefullnacho@hl-relay 'cd /opt/home/glance && docker compose up -d'
```

Config changes need only `docker compose restart`; the YAML is bind-mounted read-only.

## Things that bit, so they don't bite twice

- **`monitor` probes `check-url`, links to `url`.** Without that split the *arr index pages —
  heavy SPAs that take 1.5–3s — blew past Glance's 3s default and read as **Timed Out** while
  being perfectly healthy. Their `/ping` endpoints answer in under 500ms. Probe liveness, not
  UI render time.
- **`di:` icons default to `.svg`.** dashboard-icons ships slskd as PNG only, so `di:slskd`
  404s into a broken tile; `di:slskd.png` is correct.
- **qBittorrent's API needs auth** (403 behind gluetun), so it is probed on its root with
  `alt-status-codes: [401, 403]` — a VPN blip still reads as down, a login page does not.
- **`RELAY` is resolved by your BROWSER, not the container.** The service tiles are
  click-throughs, so that host has to work from the device you're looking at the dashboard on.
  `BRAIN` is the opposite — the container fetches it, so it must be an IP the container can
  route to (Docker's resolver has no MagicDNS).
- **The memory queue is read-only over HTTP by design.** Promoting a proposal into live memory
  stays `review_notes.py promote <id>` at the CLI, so the dashboard can surface the queue
  without becoming a way to nod it through.
