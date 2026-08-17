# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

Hestia is a local-first home assistant: **one stateful brain, many thin windows.** A local LLM
(Ollama, `qwen3:14b`) sits behind an OpenAI-compatible endpoint; the phone, the terminal, the
kitchen mic, and Home Assistant are all clients of that same brain, sharing one memory and one
tool set. Nothing runs in the cloud. Nothing is exposed to the public internet.

`ARCHITECTURE.md` is the long version. `MEMORY-DESIGN.md` covers the memory plan. `SECURITY.md`
and `AUDIT.md` hold the security posture. Read those before proposing structural changes.

## Commands

```bash
uv sync --project brain            # install deps (uv, not pip; uv.lock is committed)
cd brain && uv run pytest          # test suite (pythonpath="." is set in pyproject)
cd brain && uv run pytest tests/test_pest_watch.py   # single file

deploy/hestiactl status            # whole estate, one screen
deploy/hestiactl health            # raw brain /health JSON
deploy/hestiactl logs brain -f     # journalctl or docker logs, by name
deploy/hestiactl vpn               # verify the qBittorrent kill-switch
```

Local services are `systemd --user` units (`deploy/systemd/`). Everything else is docker on
`hl-relay` over Tailscale SSH. `hestiactl up|down|restart all` touches only the Hestia-managed
pieces — the core stack (Plex, qbit/gluetun, HA, AdGuard, MQTT) predates Hestia and is never
bulk-touched. Control those one at a time, by name.

## Layout

| Path | What |
|---|---|
| `brain/hestia.py` | FastAPI app, `POST /v1/chat/completions`, the agent loop |
| `brain/tools/` | One module per scoped tool: home, media, memory, records, recipe, reminder, search, shopping, status, weather. `skill.py` dispatches skill packs |
| `brain/skills/` | The brain's own skill packs (almanac, garden_bed, home_control, media, recipe, whelping, wildlife). **Not** Claude Code skills |
| `brain/*_store.py` | SQLite stores — records, memory, reminders |
| `brain/voice/` | Whisper STT, Chatterbox-Turbo TTS, Piper CPU fallback. Separate venv and pyproject |
| `deploy/systemd/` | Units and timers: brain, ollama, briefing, journal, reminders, garden-watch, backup, whisper, chatterbox, piper |
| `benchmarks/`, `brain/eval_*.py` | Model evaluation. `MODEL_EVAL.md` records results |

Modules in `brain/` are flat and import each other flatly (`import config`, `import records_store`)
because the service runs with `WorkingDirectory=brain`. Keep it that way.

## Design invariants — do not violate these without saying so out loud

1. **Deterministic work does not go to the model.** A chore being due, a threshold being crossed,
   the trash going out Tuesday — those are timers, rows, and records. The LLM is for judgment and
   conversation only. If a change would hand scheduling, counting, or thresholding to the model,
   stop and say so. This is the thesis of the project, not a preference.
2. **There is no shell tool, deliberately.** The brain can act in the house but cannot run
   arbitrary commands. Do not add one.
3. **The brain is unauthenticated, so the network boundary is the access control.** It binds to a
   private address — never `0.0.0.0`, never a LAN or docker-bridge interface.
4. **The note-taker proposes, it does not write.** Durable facts land in an inbox for approval.
   Keep that gate.
5. **Nothing phones home.** No cloud service, no third-party API in the hot path. One
   sanctioned exception: the `weather` tool calls Open-Meteo and `api.weather.gov`, both
   keyless and account-free, sending only a lat/lon. A forecast cannot be computed locally.
   Anything beyond those two needs a decision, not a commit.

## This repo is public

`github.com/thefullnacho/hestia`. Several paths are gitignored **on purpose**, and their content
must never be moved, quoted, or summarized into a tracked file: `secrets/`, `.env`, `memory/*`,
`data/`, `deploy/media/slskd.env`, `deploy/glance/glance.env`, and the operator's personal
working documents. The ignore rules are the authority, not this list. Treat any untracked path
as private by default: if git does not track it, do not copy it into something git does, and do
not name it in a tracked file either.

The matching `*.example` templates **are** tracked deliberately. When adding config, add the real
file to `.gitignore` and commit an example alongside it.

Never read, print, or echo anything under `secrets/` — it holds live HA tokens, *arr API keys,
WireGuard config, and the restic passphrase.

## Conventions

- **Commits**: conventional prefix and scope, lowercase, imperative, and where it helps, a clause
  saying why. Matching the existing log:
  `perf(almanac): fold the wildlife section into one line, so it stops growing per species`
  `feat(garden): harvest logging, so the beds get a yield track record`
  `fix(pest-watch): never open a window for pests with no emergence event`
- **Docs are part of the change.** A behavioral change updates the relevant `.md` in the same
  commit. `docs(...)` commits are first-class here.
- **Tests**: pytest under `brain/tests/`, one file per tool or subsystem. New tool, new test file.
- **Style**: no em dashes in prose written for this repo.
