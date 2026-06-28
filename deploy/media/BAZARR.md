# Bazarr — subtitles for the Sonarr/Radarr libraries

Bazarr is the subtitle companion to the *arr stack: it watches everything Sonarr (TV) and
Radarr (movies) import and fetches matching subtitles, written as **sidecar `.srt` files** next
to each video so Plex (and every client) shows them automatically. English subs are a hard
requirement in this house — this is what makes them appear without per-file fiddling.

It runs in the same `deploy/media/compose.yml` stack on hl-relay (`/opt/home/arr/`), on the
same compose network and the same `/mnt/media:/data` mount as Sonarr/Radarr — so the paths
Bazarr sees are identical to theirs and **no path mapping is needed**.

## Current deployment (hl-relay, 2026-06-16)

Deployed and running on `:6767`. Already wired (via `config.yaml`, so it survives restarts):
- **Sonarr + Radarr connections** — live (SignalR connected); keys set, reached by service name.
- **No-account providers** — Podnapisi + TVSubtitles enabled.

**Remaining (UI, ~2 min):** create the English **language profile** and assign it as default —
this is DB-backed, so it's done in the UI, not config.yaml. Until a profile is assigned, Bazarr
won't search. Steps 3 below. Then, when you have an OpenSubtitles.com login, add it (step 4).

## Deploy

On hl-relay, from the stack dir:

```bash
cd /opt/home/arr
docker compose up -d bazarr
# UI: http://hl-relay:6767  (or the LAN IP)
```

## First-boot configuration (in the Bazarr UI)

1. **Settings → Sonarr** — Enabled; Address `sonarr`, Port `8989`, API key from Sonarr's
   Settings → General. (Service name works because Bazarr is on the same compose network.)
2. **Settings → Radarr** — Enabled; Address `radarr`, Port `7878`, API key from Radarr.
   - Leave path mappings EMPTY — the `/data` mount is identical across all three.
3. **Settings → Languages** — add a Languages Profile with **English** (enable "Use Original"
   off). Set it as the default profile, then on the Series/Movies tabs assign it to existing
   items (Mass Edit → set profile) so the back-catalogue gets subs too.
4. **Settings → Providers** — add at least one. Coverage-vs-effort:
   - *No account:* Podnapisi, TVSubtitles, Subscene-style providers — instant, weaker coverage.
   - *Account (best coverage):* **OpenSubtitles.com** — needs a (free or VIP) login; free tier
     is rate-limited but fine for a home library. Enter the username/app-password under the
     OpenSubtitles.com provider. Keep these creds OUT of git (Bazarr stores them in
     `./bazarr/config`, which is gitignored like the other `*/config` dirs).
5. **Settings → Subtitles** — set "Subtitle folder" to **alongside the media file** (sidecar),
   and turn on **Upgrade Previously Downloaded Subtitles** so hearing-impaired/better syncs
   replace weaker grabs over time.

## Notes

- New downloads get subs automatically once connections + a provider are set. For the existing
  library, run **Mass Edit → Search** (or let the scheduled task sweep) after assigning the
  language profile.
- Hearing-impaired (SDH) subs: if those are preferred, enable "Hearing Impaired" in the
  languages profile so Bazarr favours SDH tracks.
- Bazarr writes only subtitles; it never touches the video files, so it's safe alongside the
  kill-switch-verified download path.
