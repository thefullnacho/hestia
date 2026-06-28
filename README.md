# Hestia

One stateful brain, many thin windows. See [ARCHITECTURE.md](ARCHITECTURE.md) for
the thesis and [MEMORY-DESIGN.md](MEMORY-DESIGN.md) for the memory plan.

> **Part of the Forager constellation.** Hestia is one of four related projects (with
> `forager_ml`, `forager-field-station`, and the Homesteader Labs site). Cross-project knowledge
> — how the repos relate, the shared model registry, the dev box / CUDA gotchas, brand, and known
> divergences — lives in the **Forager wiki** at `~/Documents/Forager/forager-wiki/` (start at
> `index.md`). Update it when you change something that crosses repos.

## Status

- **Phase 0 — Reach + brain** ✅ *in place* (see below)
- **Phase 1 — Media appliance** ✅ *complete* — Plex + qBittorrent + gluetun VPN
  kill-switch (verified) + the *arr automation layer (Prowlarr/Sonarr/Radarr +
  FlareSolverr). Full loop wired: search → download (via VPN) → hardlink → Plex.
- Phase 2 — House (Home Assistant) — *head start*: `hl-ha` already running on the Micro
- Phase 3 — Voice (Assist pipeline) — not started
- **Phase 4 — The seam (memory + tools)** 🔶 *in progress* — brain is now an agent:
  tool-calling loop + six scoped tools (`home`, `media`, `memory`, `records`, `search`,
  `weather`), deterministic skill injection, and **HA's conversation agent now points at
  Hestia** (the seam — Assist/voice routes through the brain). Verified by voice/text. The
  brain also **gets smarter over time**: a background note-taker proposes durable facts
  after each exchange for review (see *Memory & learning* below). Next: vision (Eyes), the
  voice satellite (Phase 3).

---

## Phase 0 — Reach + brain

> *Win: talk to your home model from your phone.*

The brain (`brain/`) is a thin OpenAI-compatible proxy onto Ollama. Every client —
terminal, phone, kitchen mic — speaks one dialect (`POST /v1/chat/completions`).
In Phase 0 it forces the chosen model, injects Hestia's system prompt (persona +
the hardened safety rules from the benchmark A/B), and streams the reply back.
Memory and tools land in Phase 4 behind this same URL.

### What runs (GPU box)

| Service | What | Bind | GPU |
|---|---|---|---|
| `hestia-ollama` | Ollama inference engine | `127.0.0.1:11434` (localhost only) | RTX 5080 only |
| `hestia-brain`  | Hestia `/v1` proxy | `0.0.0.0:8730` (reachable over Tailscale) | — |

Both are **user** systemd services (no root), defined in `deploy/systemd/` and
installed into `~/.config/systemd/user/`. Linger is enabled, so they survive
logout/reboot. Ollama is pinned to the 5080 (`CUDA_VISIBLE_DEVICES`), leaving the
4060 Ti free for Phase 3 (Whisper/Piper) per the benchmark verdict.

Model: **`qwen2.5:14b`** (Q4_K_M) — the benchmark pick.

### Operate

Day to day, use `deploy/hestiactl` (symlinked into `~/.local/bin`) — one command
for the whole estate, run from the GPU box:

```bash
hestiactl status              # brain health + local units + every container on hl-relay
hestiactl health              # raw /health JSON
hestiactl up|down|restart X   # X: brain ollama | arr services | plex qbit ha adguard ... | all
hestiactl logs X [-f]         # journalctl (local) or docker logs (remote)
hestiactl vpn                 # verify the qBittorrent kill-switch
```

`all` covers only the Hestia-managed pieces (local units + arr stack); core
containers (AdGuard = house DNS, gluetun, HA) are controlled one at a time and
ask for confirmation before stopping.

The underlying commands, for when you need them directly:

```bash
# status / logs
systemctl --user status hestia-ollama hestia-brain
journalctl --user -u hestia-brain -f

# restart after editing brain code or a service file
systemctl --user daemon-reload          # only if you edited a .service
systemctl --user restart hestia-brain

# health (Ollama up + model present?) — brain binds the Tailscale IP, not localhost
curl -s 127.0.0.1:8730/health | jq

# talk to it
curl -s 127.0.0.1:8730/v1/chat/completions -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello Hestia"}]}' | jq -r .choices[0].message.content
```

If you edit a `deploy/systemd/*.service` file, re-copy it into
`~/.config/systemd/user/` before `daemon-reload`.

### Reach it from the phone (Tailscale)

Tailscale is the one piece that needs root, so it isn't auto-installed. On the GPU
box:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then on the phone: install the Tailscale app, sign in to the same tailnet. The
brain is then reachable at `http://<gpu-box-tailscale-name>:8730/v1` from any app
that speaks OpenAI (set that as the base URL; any API key string works — Ollama
ignores it). Nothing is exposed to the public internet.

### Brain layout

```
brain/
  hestia.py       # the agent loop: /v1/chat/completions + /health, tools, memory, note-taker hook
  config.py       # single source of paths + secret loading; makes the brain relocatable
  prompt.py       # SYSTEM_PROMPT — persona + hardened safety rules
  records_store.py / memory_store.py   # SQLite entities+events / markdown soft facts
  note_taker.py   # background "gets smarter over time" extractor
  review_notes.py # CLI to review + promote the note-taker's proposals
  tools/          # home, media, memory, records, search, weather (+ skill router)
  tests/          # pytest: stores, dispatch, note-taker (run: uv run --project brain pytest)
  pyproject.toml  # deps + dev (pytest) + pytest config (uv-managed, isolated venv)
```

**Relocatable.** Every path derives from `config.py`'s own location, so moving or
restoring the repo to a new path needs no edits; `HESTIA_ROOT` overrides if needed. All
service URLs, tokens, and thresholds stay env-overridable next to the tools that use them.

---

## Phase 1 — Media appliance (Dell Micro = `hl-relay`)

> *Win: the media stack runs, independent of the brain.*

Most of this already existed on the Micro before Hestia: **Plex** (`hl-plex`),
**qBittorrent** behind **gluetun** (Surfshark, OpenVPN, NL) with a **fail-closed VPN
kill-switch**, plus AdGuard, MQTT, and Home Assistant. The kill-switch is verified:
qBittorrent's traffic egresses via the VPN datacenter IP, not the host's. Don't
`docker compose up` the existing `/opt/home/compose.yml` blindly — its volume paths
are literal `/path/to/...` host dirs that the running containers depend on.

Hestia added the missing **automation layer** as a separate, isolated stack
(`deploy/media/compose.yml`, deployed to `/opt/home/arr/`): **Prowlarr** (:9696,
indexer manager), **Sonarr** (:8989, TV), **Radarr** (:7878, movies). All reachable
over Tailscale.

Also added **FlareSolverr** (:8191) so Prowlarr can reach Cloudflare-protected
indexers, wired as a Prowlarr indexer-proxy (tag `flaresolverr`).

Wired via API: root folders point at the existing Plex library
(`/data/TV Shows`, `/data/Movies`); a remote-path mapping (`/downloads` →
`/data/downloads`) lets Sonarr/Radarr **hardlink** from qBittorrent's downloads into
the library (instant, no copy — both are one filesystem under `/mnt/media`); Prowlarr
is connected to Sonarr + Radarr (`fullSync`). Five reputable **public indexers** added
(The Pirate Bay, Knaben, LimeTorrents, plus 1337x + EZTV via FlareSolverr) and synced
down to the apps. YTS deliberately excluded (history of feeding user data to copyright trolls).

**qBittorrent** is wired as the download client in both Sonarr (category `tv-sonarr`)
and Radarr (`radarr`), tested OK. The full loop works: search → download through the
VPN → hardlink into the Plex library. Both apps report no health warnings.

⚠️ Media currently lives on the Micro's 98 GB root disk (~66 GB free). Fine to start;
plan a dedicated disk or NAS before the library grows.

### Operate (on `hl-relay`)

```bash
cd /opt/home/arr
docker compose ps
docker compose pull && docker compose up -d   # update *arr
```

---

## Phase 4 — the seam: HA conversation agent → Hestia

`deploy/ha/custom_components/hestia/` is a thin custom HA integration: it registers a
conversation agent (`conversation.hestia`) that forwards each utterance to Hestia's
`/v1` and speaks the reply. Hestia owns the loop (memory + tools, incl. controlling
HA back); HA is just input + a tool. This is the architecture's keystone made real.

Wiring on `hl-relay` (not in this repo — lives in HA's config):
- Integration files installed to `/opt/home/ha_config/custom_components/hestia/`.
- A config entry points it at `http://127.0.0.1:8730/v1/chat/completions` (Hestia
  over Tailscale; the HA container can reach it).
- The preferred Assist pipeline's `conversation_engine` is set to `conversation.hestia`,
  so the Assist chat and voice satellites route through the brain.

Verified: via HA's conversation API, "turn on the TV light" drove the real light and
"what coffee should I buy?" recalled a memory — HA → Hestia → HA round trip.

## Memory & learning — it gets smarter over time

Two stores back the brain: `memory_store` (markdown soft facts/preferences, git-auditable)
and `records_store` (SQLite entities + a uniform event log: pets/lineage, wildlife, chores,
service reminders, the garden). Both are injected into the system prompt per request, scoped
to what the request implies.

The brain also learns passively. After each exchange — once the answer is already on the
wire — a background **note-taker** (`note_taker.py`) reads the turn and proposes durable
facts it heard ("trash pickup is Tuesday mornings"). True to *propose, don't dispose*, those
land in a review inbox (`memory/inbox/`), **not** straight into live memory:

```bash
uv run --project brain python brain/review_notes.py list
uv run --project brain python brain/review_notes.py promote <id> | --all
uv run --project brain python brain/review_notes.py discard <id> | --all
```

It reuses the resident model by default and never blocks or breaks a request. Tuning knobs:
`HESTIA_NOTETAKER=0` disables it; `HESTIA_NOTETAKER_AUTOWRITE=1` skips the review queue and
writes durable memories directly; `HESTIA_NOTETAKER_MODEL` points it at a cheaper model (e.g.
a second Ollama on the free 4060 Ti) to take the load off the brain.

## License & security

Hestia is licensed under the **GNU Affero General Public License v3.0** — see [LICENSE](LICENSE).
The AGPL is deliberate: Hestia is built to be self-hosted, so the copyleft keeps it open even for
anyone who runs a modified version as a network service, while imposing nothing on you for running
it at home.

Before running it, read **[SECURITY.md](SECURITY.md)**: the brain has no built-in authentication
and can control your Home Assistant devices, so it must stay on a private network (Tailscale/LAN)
and must never be exposed to the public internet. It deliberately has no shell tool.

© 2026 TheFullNacho and contributors.
