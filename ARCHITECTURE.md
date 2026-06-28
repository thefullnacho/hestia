# Hestia — a home + work intelligence

*Working sketch. Not code yet — a shared mental model to argue with.*

---

## The thesis

**One stateful brain, many thin windows. Memory is the moat.**

Intelligence should be a *service reachable from anywhere*, not a feature of
whichever device is in your hand. The moment it's a service, the terminal at your
desk, the phone in your pocket, and the mic in your kitchen all become thin
windows into the *same* thing — same context, same memory, same tools. That
single reframe kills the three complaints that started this:

- **Siri friction** — voice should hit *your* brain directly, not a closed router.
- **Alexa's amnesia** — memory lives in the brain (server-side), not the client.
- **"Disconnected from intelligence outside"** — it's a networking gap, not a
  capability gap. Close the network, you're never disconnected.

---

## The one decision everything hangs on

**Your brain exposes an OpenAI-compatible endpoint.** That's the keystone.

```
                 every interface speaks ONE dialect
   terminal ─┐
   phone     ├──►  POST /v1/chat/completions  ──►  Hestia agent  ──►  Ollama
   HA voice ─┘        (the brain)                  (loop+memory+tools)   (model)
```

Why this and not "let Home Assistant talk to Ollama directly":

- If HA → Ollama, then **HA owns the loop** and your memory/tools aren't in the
  path. The kitchen mic gets a raw model with no memory of this morning.
- If everything → **Hestia → Ollama**, then your brain owns the loop *everywhere*.
  HA becomes just another client (for input) and just another tool (for control).
- It also gives you the thing you said you wanted: the whole system *feels like an
  API* — one URL, a couple of digits, let it rip. The serving mess is quarantined
  behind that URL forever.

So: **HA is both an input device (voice) and a tool (device control). It is not
the brain.** The brain is yours.

---

## Topology (who runs where)

```
                ┌────────────── Tailscale (private mesh, encrypted) ──────────────┐
                │                                                                  │
   [ Phone PWA ]┤  voice + chat, from anywhere                  [ Laptop / SSH ]──┤  terminal
   (outside)    │                                                (at desk)         │
                │                                                                  │
   ┌────────────┴───────────────────────┐        ┌──────────────────────────────┴──┐
   │  GPU BOX — "the brain"              │        │  DELL MICRO — "the appliance"    │
   │                                     │        │                                  │
   │  • Ollama        (14–32B, 4-bit)    │  REST  │  • Plex            (Quick Sync)  │
   │  • Hestia agent  → /v1 endpoint     │◄──────►│  • Prowlarr / Sonarr / Radarr    │
   │  • Memory store  (facts + history)  │  calls │  • qBittorrent + VPN (killswitch)│
   │  • Whisper STT  /  Piper TTS        │◄──────►│  • Home Assistant + Assist       │
   └─────────────────────────────────────┘  Wyoming  └──────────────┬───────────────┘
                                                                     │
                                                  [ voice satellites: kitchen, etc. ]
```

Defaults (all up for debate, see Open Decisions):

- **GPU box** runs the heavy ML: the model (Ollama), the brain (Hestia), and the
  speech models (Whisper STT, Piper TTS). These are the only things that want a GPU.
- **Dell Micro** runs the light, always-on appliances: Plex (Quick Sync transcode
  is its superpower), the media-automation stack, and Home Assistant (HA is light;
  it points at the GPU box's Whisper/TTS/brain over the network).
- **Tailscale** stitches phone + laptop + both boxes into one private network.
  Nothing is exposed to the public internet.

---

## Components — commodity vs. yours

| Layer | Pick (don't build) | Build (it's yours) |
|---|---|---|
| Inference engine | **Ollama** (auto-unloads when idle — right for 24/7) | — |
| Model | a 14–32B tool-caller @ 4-bit on the 5080 | — |
| Gateway (optional) | **LiteLLM** (unify local + hosted behind one /v1) | — |
| Media | **Plex + Prowlarr/Sonarr/Radarr + qBittorrent** | — |
| House automation + voice I/O | **Home Assistant + Assist** | — |
| Speech | **faster-whisper** (STT), **Piper** (TTS), **openWakeWord** | — |
| Network | **Tailscale** | — |
| **The brain** | — | **agent loop, memory, tool wiring, /v1 endpoint** |

The rule from our earlier talk holds: the part that *hurt* (serving, transcoding,
voice hardware) is exactly the part you stand on, not build. The part worth owning
is the thin seam that's about *you*.

---

## The brain (the part you own)

A small service. Probably a few hundred lines + tool definitions, not a platform.

- **Exposes** `POST /v1/chat/completions` (OpenAI-compatible) so every client speaks one dialect.
- **Wraps** Ollama for raw generation.
- **Owns the loop**: read message → recall memory → plan → call tools → respond → write memory.
- **Tools** (each a thin wrapper):
  - `bash` — shell on the GPU box (and/or Micro over SSH). The work workhorse.
  - `home` — Home Assistant REST (lights, climate, reminders, scenes, state queries).
  - `media` — *arr + Plex REST ("grab the new season of X", "what's eating disk", "delete watched > 30d").
  - `search` — web/local search.
  - `memory` — read/write the store (see below).
- **Personality/context**: one system prompt that knows your house, your work, your preferences.

> **"Tailored to my home" ≠ fine-tuning.** It's base model + system prompt +
> memory + the right tools. You almost certainly never train anything.

---

## Memory (the moat)

Lives server-side, shared by *every* interface. The thing Alexa/Siri structurally can't do.

- **Working** — the current conversation/session (short).
- **Episodic** — a rolling log of past interactions, retrievable ("what did I ask
  you to remind me about the garage?").
- **Semantic / facts** — durable preferences and household truths ("the good coffee
  is the bag with the orange label", "kids' bedtime is 8", "work VPN is flaky on Mondays").
- **Household state** — current, queried live from HA rather than stored (don't
  cache what HA already knows).

(Odysseus already has a ChromaDB-backed memory — a reasonable starting point or reference.)

---

## Voice (the genuinely hard part) → Home Assistant

Don't clone Alexa's hardware; far-field mics + wake words + whole-house audio are
*why* Amazon/Google won. HA's **Assist** pipeline is the open path that's finally good:

```
[satellite mic] → wake word (openWakeWord) → STT (Whisper, on GPU box)
   → conversation agent = Hestia /v1  → tools (HA services, memory, media)
   → TTS (Piper) → [satellite speaker]
```

- **Satellites**: cheap ESP32 boxes, or HA's own Voice hardware, one per room you care about.
- **Wake word / STT / TTS**: HA orchestrates; the heavy STT/TTS models run on the
  GPU box and HA reaches them via the **Wyoming** protocol.
- **The crucial wiring**: set HA's *conversation agent* to a custom/OpenAI-compatible
  integration pointed at **Hestia's `/v1`** — not at Ollama. That's what puts your
  memory and tools in the kitchen-mic path, and lets the brain call *back* into HA
  to actually control devices.

This is the only part of the whole vision that's a real build-out. It's where HA
earns its place as the hub.

---

## Network & security

- **Tailscale** for all remote access. No port-forwarding, nothing public.
- **qBittorrent bound to the VPN interface, fail-closed** (kill-switch): a VPN drop
  stops downloads instead of leaking your IP. Easy to get subtly wrong — verify it.
- **Secrets** (HA token, *arr API keys, Tailscale) in one env file the brain reads;
  never in prompts, never committed.
- The brain can run shell — treat its endpoint as privileged. Tailscale-only, authed.

---

## Three journeys (to sanity-check the design)

1. **Kitchen, hands full:** *"turn off the kitchen lights and remind me to call mom
   tonight."* → satellite → HA Assist → Whisper → **Hestia** → `home`(lights off) +
   `memory`(write reminder) → Piper → "Done, I'll remind you at 7." Memory persists;
   tomorrow it can recall it. Alexa can't.
2. **On the train, phone only:** voice-to-text into the PWA over Tailscale → **Hestia**
   (same brain, same memory) → answers, or kicks off a `media` download to be ready at home.
3. **At the desk, deep work:** terminal CLI → **Hestia** → `bash` on either box, with
   full context of what you've been doing. Same brain as the kitchen and the train.

One brain answered all three. That's the whole point.

---

## Build order (commodity first, seam last)

- **Phase 0 — Reach + brain:** Tailscale everywhere; Ollama + a model on the GPU box;
  a stub Hestia that just proxies `/v1` to Ollama. *Win: talk to your home model from your phone.*
- **Phase 1 — Media appliance:** Plex + Prowlarr/Sonarr/Radarr + qBittorrent (VPN
  kill-switch) on the Micro. *Win: the media stack runs, independent of the brain.*
- **Phase 2 — House:** Home Assistant on the Micro; get basic automations working
  the boring way first. *Win: HA controls the house.*
- **Phase 3 — Voice:** Assist pipeline — Whisper/Piper on GPU box via Wyoming, a
  satellite in one room, wake word. Conversation agent still default for now.
- **Phase 4 — The seam (the part that's yours):** give Hestia real memory + tools
  (bash, home, media, search); point HA's conversation agent at Hestia `/v1`. *Win:
  one brain with memory, reachable by terminal, phone, and voice.*

Each phase is independently useful, so you're never holding a half-built thing that
does nothing.

---

## Decisions (resolved 2026-06-07)

1. **HA runs on the Dell Micro**, alongside Plex. ✅ It's light; co-locating keeps
   the appliance self-contained and the GPU box dedicated to ML.
2. **Model = Qwen2.5-14B-Instruct on the single 5080.** ✅ Benchmarked
   (`benchmarks/RESULTS.md`): 100% tool/args/reasoning at ~77 tok/s. Optional
   snappy tier Qwen2.5-7B @ 143 tok/s, probably unnecessary.
3. **Voice hardware = prove the loop cheap, then go networked.** On hand: a spare
   **RPi4 + USB mic**, a **USB Rode** mic, and maybe an **ESP32**; office is 8 ft
   from the kitchen.
   - *Step 1 (today):* Rode on an 8-ft USB cable into the GPU box — fastest way to
     prove STT→brain→TTS works at all. (8 ft is well within USB spec.)
   - *Step 2:* **RPi4 as the first real Wyoming satellite** — that's the architecture
     that generalizes to more rooms; the cabled mic is a dead-end.
   - *Step 3:* ESP32 satellites for extra rooms once the pattern is proven.
   - ⚠️ *Far-field is the real constraint:* a desk cardioid (Rode) 8 ft away will
     hear you poorly across a kitchen. Plan a mic array (e.g. ReSpeaker on the RPi4)
     for true ambient pickup; keep the Rode for desk/office voice where it shines.
4. **Build the brain FRESH; reuse the memory *design*, not Odysseus itself.** See
   `MEMORY-DESIGN.md` for the call and the why.
5. **GPU config = single-card 5080 for the brain; 4060 Ti stays free.** ✅ Pooling
   halved tok/s with zero accuracy gain (`RESULTS.md`), and the 32B that *needs*
   both cards matched the 14B at 4× lower speed. So: **5080 → 14B brain; 4060 Ti →
   Whisper STT + Piper TTS + the background memory note-taker.** Pooling stays in
   the toolbox for *fitting* a too-big model, not for everyday serving.

---

## What we are deliberately NOT building

Inference engines. Transcoders. Indexers. Torrent clients. Wake-word/STT/TTS
models. VPN meshes. All commodity, all mature, all someone else's hard-won problem.
We build **one brain and the thin glue to your house.** Everything else, we wire to.
