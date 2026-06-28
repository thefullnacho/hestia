# Lidarr + slskd — music to the *arr stack

Adds **music** to the Phase 1 automation layer (`compose.yml`): **Lidarr** is the
Sonarr/Radarr for albums & artists; **slskd** is a headless Soulseek client that gives
Lidarr the individual tracks and rarities public torrents miss. Same Prowlarr, same
qBittorrent, same `/mnt/media` filesystem — music lands at `/mnt/media/Music` for Plex.

Lives next to the others at `/opt/home/arr/` on **hl-relay** (the Dell Micro), **not**
the GPU box.

> **STATUS: DEPLOYED & WIRED 2026-06-10.** Lidarr + slskd up; Tubifarry plugin loaded; slskd
> logged into Soulseek, sharing `/data/Music`, downloading to `/data/downloads/slskd`. Lidarr
> has BOTH download clients (slskd + qBittorrent, the latter with a `/downloads`→`/data/downloads`
> remote-path mapping) + indexers (Slskd + Prowlarr torrents); health clean; real Soulseek
> search returned 3000+ files. Seed limits set in qbit by the user (ratio 2.0 / 24h, global).
> **Remaining (optional):** expose Lidarr via Hestia's `media` tool. Steps below are the
> reproduce-from-scratch record.

## Why slskd runs DIRECT (not behind the VPN)
Soulseek is not BitTorrent. There's no swarm broadcasting your IP and no tracker — a peer
sees your IP only during a direct transfer, and the network isn't industrially monitored
the way torrent swarms are. Crucially, *downloading already exposes your IP to the peer you
pull from*, so leeching buys almost no privacy; the only real privacy lever is VPN-or-not,
and routing slskd through the qbit VPN breaks incoming connections (qbit owns the single
forwarded port), leaving Soulseek outbound-only — fewer sources, no sharing back. So slskd
runs direct + shares a modest folder. To move it behind the VPN later, give it
`network_mode: "service:gluetun"` and drop its `ports:` (accept the degraded function).

## Prerequisites
1. **Soulseek account** — there is no web signup; the account is created at first login.
   Decide on a username + password and put them in `slskd.env` (step 2). slskd registers
   them on first connect. If the username is taken, login bounces — pick another.
2. **Creds file:** on hl-relay, `cd /opt/home/arr && cp slskd.env.example slskd.env`, then
   fill in the four values. `slskd.env` is gitignored.
3. **Tailscale SSH** to hl-relay must be re-authorized (the check expired):
   `ssh youruser@hl-relay` and approve the printed login URL in a browser.

## Deploy
```bash
cd /opt/home/arr
docker compose pull lidarr slskd
docker compose up -d lidarr slskd
docker compose ps          # both Up; check `docker logs slskd` shows "logged in" to Soulseek
```
If a host port is already bound, compose fails loudly (nothing else is touched) — pick a
free port in `compose.yml` and re-run. New ports introduced: **8686** (Lidarr),
**5030** (slskd web/API), **50300** (slskd Soulseek listen).

## First-run config
### slskd — http://hl-relay:5030  (log in with SLSKD_USERNAME/PASSWORD)
slskd **ignores the `SLSKD_DIRECTORIES_*` env vars** (verified — it keeps the in-container
`/app` default). Set directories, the `/data/Music` share, and an API key in
`/opt/home/arr/slskd/config/slskd.yml` (the generated file is all-comments, so append a
clean block) and `docker restart slskd`:
```yaml
shares:
  directories:
    - /data/Music                  # Soulseek etiquette: share your library, not the whole drive
web:
  authentication:
    api_keys:
      tubifarry:
        key: <openssl rand -hex 20>   # the Lidarr/Tubifarry client authenticates with this
        role: readwrite
        cidr: 0.0.0.0/0,::/0
directories:
  downloads: /data/downloads/slskd      # MUST be under /mnt/media so finished files hardlink
  incomplete: /data/downloads/slskd/incomplete
```
Verify after restart: `GET /api/v0/options` shows the right directories, logs show "Logged
in to the Soulseek server" + "Found 1 shared directories".

### Lidarr — http://hl-relay:8686
1. **Confirm plugins build:** `System → Plugins` exists (branch `plugins`, net8.0). If not,
   the image tag isn't a plugins build — fix `lidarr.image` in `compose.yml` and recreate.
2. **System → Plugins → install `https://github.com/TypNull/Tubifarry`**, then restart Lidarr.
   Tubifarry is the slskd integration that targets net8.0 + this image; the standalone
   `Lidarr.Plugin.Slskd` fork has **no net8.0 release** and fails to load ("No compatible
   release found … net8.0"). API equivalent: `POST /api/v1/command {"name":"InstallPlugin",
   "githubUrl":"…"}` then `docker restart lidarr`.
3. **Media Management → Root Folders:** add `/data/Music` (Lidarr requires a name + default
   quality & metadata profile, unlike Radarr).
4. **Settings → Download Clients → +:**
   - **Slskd** (Tubifarry, under *Other*): baseUrl `http://slskd:5030`, the slskd API key,
     host `slskd`. Paths align (both mount `/mnt/media`→`/data`), so no remote-path mapping.
   - **qBittorrent** (album torrents): host `host.docker.internal`, port `8090`, the qbit
     WebUI creds, category `lidarr`.
5. **Settings → Indexers → + → Other → Slskd** (Tubifarry): same baseUrl + API key. Prowlarr
   also pushes the torrent indexers in via the app sync below.

### Prowlarr — http://hl-relay:9696
- **Settings → Apps → +Lidarr:** Prowlarr Server `http://prowlarr:9696`, Lidarr
  `http://lidarr:8686`, paste Lidarr's API key (`Settings → General`), Sync. This pushes the
  existing torrent indexers into Lidarr (slskd is wired separately, above).

## Expose to Hestia (after Lidarr is up — optional, do later)
Add `LIDARR_URL` / `LIDARR_KEY` to `secrets/media.env` on the **GPU box** and extend the
brain's `media` tool so "find me <song/album>" routes to Lidarr like shows route to Sonarr.

## Port map (hl-relay)
| Service | Port |
|---|---|
| qBittorrent | 8090 |
| SearXNG | 8095 |
| Home Assistant | 8124 |
| FlareSolverr | 8191 |
| Radarr / Sonarr / Prowlarr | 7878 / 8989 / 9696 |
| Plex | 32400 |
| **Lidarr** | **8686** |
| **slskd web/API** | **5030** |
| **slskd Soulseek listen** | **50300** |
