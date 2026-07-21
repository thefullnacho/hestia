---
name: media
description: Use when the user wants to find, download, or add a movie, TV show, or song/album to the library (Radarr/Sonarr/Lidarr → Plex), or check what's downloading. Scopes the request to the media tool so the model fires it instead of drifting into web search.
triggers: movie, movies, film, films, tv show, tv shows, the show, new show, series, season, seasons, episode, episodes, download, downloading, radarr, sonarr, lidarr, plex, watch a movie, put on a movie, song, songs, album, albums, soundtrack, discography, band, music, artist
tools: media
metadata:
  domain: media
  version: 0.1.0
---

# Media

The user wants something in the media library (Radarr for movies, Sonarr for TV, Lidarr
for music → Plex). Call the `media` tool — do NOT use web `search`; the media tool is the
only thing that can actually add or grab a title.

Pick `kind`:
- a movie/film → `kind='movie'`
- a TV show / series / season / episode → `kind='tv'`
- a song / album / artist / band / "put on some music" → `kind='music'`

Choose the action:
- "download / grab / add / get me / put X on Plex" → `action='add'` with `kind` and
  `query` (the title; include the year if the user gave one, e.g. `'The Breadwinner 2026'`).
  This adds it and starts downloading in the background — it returns immediately.
- "do we have / find / is there / search for X" → `action='search'` with `kind` and
  `query`. Returns candidates; don't add unless they then ask to.
- "what's downloading / how's the download / download status" → `action='status'`
  (no kind or query needed).

Movies have a hard 1080p ceiling — omit `quality` for the sensible default; only pass
`quality` ('small' / '1080p' / 'best') if the user explicitly asks about size/quality.

For music, a single-song request grabs the album it's on (Lidarr works album-level); if
you're unsure which album, `action='search'` first, otherwise just `action='add'` with
the song or "song by artist" as the query.

Fire the tool; don't deliberate about which tool to use — this request is yours. After an
add, tell the user it's downloading and that they can ask what's downloading to check on it.
