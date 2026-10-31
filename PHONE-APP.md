# The phone app

*Draft spec, 2026-10-30. Not built. For review before any code.*

## What it is

A native iPhone app that does two things the web app can't: it **runs the model**, and it
**receives a ping**. Everything else stays on the box, in the one harness that exists today.

- **The box** keeps the agent loop, memory, records, recipes, the library, reminders, timers,
  the briefing facts and every deterministic job. It needs no GPU: any always-on computer with
  an SSD (a Pi 5, a Pi 4, an old laptop, a Mac mini).
- **The phone** hosts the model while the app is open, shows the existing `/app` in a web view,
  and handles speech with Apple's on-device recognition and voice.

Why this shape: pilot households have no GPU, and the measured model fits a phone.
LFM3-1.7B QAD-Q4_0 (1.05 GB) scores 91.1% on the whole-turn held-out suite against the
resident's 92.1%, and on an iPhone 17 Pro it reads 1,514 tokens a second and writes 56,
settling near 39 under sustained load (`benchmarks/RESULTS.md`).

A phone-only harness (no box) was weighed and set aside: it means a second codebase, the 91.1%
belongs to this harness, nothing runs while the phone is closed, and one phone is not a
household. Revisit only if the pilots show the box is what stops people.

## Decided

- One harness, on the box. The phone never makes a decision the box could make.
- Speech is Apple's on-device recognition and voice, so the phone's memory goes to the model.
- Notifications carry no content (SECURITY.md, "Phone notifications").
- Pilot problem reports are one flagged turn, sent by the person as a file (SECURITY.md).
- Pilots borrow a box for 30 days; keeping it is the measure (STATUS.md, 2026-10-30).

## The connection: the phone dials the box

The brain calls Ollama today. Here the box would have to call the phone, which means the phone
listening on the tailnet. Instead the app opens a WebSocket to the box when it comes to the
foreground, and the box sends its model requests down that socket.

```
app  --connect /v1/model/socket (device token)-->  box
box  --{id, prompt, params}------------------------>  app runs the model
app  --{id, delta}...{id, done, usage}------------->  box
```

- The phone never listens, so it works wherever the phone is, as long as it is on the tailnet.
- Several phones can be connected. The box prefers the phone that sent the turn, then any.
- When the app goes to the background, iOS closes the socket. That is expected, not an error.

**The socket needs a token,** unlike the rest of the brain. Whatever hosts the model sees every
prompt (the household's memory and records) and writes every tool call. A device token, issued
at setup and kept in the Keychain, keeps a stray tailnet device from becoming the household's
model.

## Who renders the prompt: the box (recommended)

Two ways to split the model work:

1. **The phone is a plain completion engine** (recommended). The box renders the conversation
   and tools into the model's own prompt text with its chat template, sends the text, and parses
   the tool calls out of what comes back. The phone runs llama.cpp on a string and streams
   tokens: no template, no tool parsing, nothing that can drift from the box.
2. **The phone is a full OpenAI-style server**, like llama-server today. Simpler for the box,
   but the template and tool-call parsing then live in Swift, a second copy of harness logic.

Option 1 keeps the "one harness" promise. Its cost is a Python renderer and a parser for
LFM3's tool-call format. They are testable on the box before any phone exists: render the same
turns with llama-server on the GPU box and compare the prompt text and parsed calls exactly.

## backend.py gets a third backend

`backend.chat()` already picks Ollama or an OpenAI-compatible server. The phone becomes
`HESTIA_BACKEND=phone`: render, send down the socket, parse. Prerequisite, found while writing
this: **the timed jobs don't go through backend.py.** `journal.py`, `feed.py`, `briefing.py`,
`note_taker.py` and the recipe organizer each post to Ollama directly. They move onto
`backend.chat()` first, or they never reach the phone.

## Work that waits for a phone

iOS only lets the model run while the app is open. Chat loses nothing: you are in the app when
you ask. Model work that a timer starts has to wait:

| Job | Today | On a pilot box |
|---|---|---|
| Briefing narration | 07:10 timer | Facts gathered at 07:10 as now; narrated when the app next opens |
| Note-taker | After each chat turn | Runs right after the turn while the phone is connected; queued if the app closed first |
| Feed ranking | 03:00 timer | Candidates fetched at 03:00; ranked when the app opens |
| Journal | 23:45 timer | Off for pilots at first; it is a homestead diary |
| Recipe organizer | On import, in the app | Runs immediately; the phone is connected |

A small table on the box holds the queued model work (kind, payload, created). When a phone
connects, the box drains it oldest first, with chat turns always ahead of queued jobs. Nothing
deterministic moves: reminders, thresholds, watchers and backups run as they do now.

A box with no phone connected answers a model request the way the brain answers a down Ollama
today: a clear message, not a hang. A CPU model on the box as a fallback is possible but
unmeasured. Don't build it until a pilot misses it.

## Notifications

Two paths, so that a reminder's text never has to leave the house:

- **Local notifications for what the box already knows.** While the app is open it syncs the
  upcoming reminders and schedules them with iOS on the phone, with their full text. iOS fires
  them on time with the app closed. No Apple server sees them.
- **A content-free ping for everything else.** A reminder made from another window while the
  app is closed, a threshold crossing, the morning briefing: the box sends Apple's push service
  a notification that says only that something is waiting. Opening it syncs and shows the
  content. The box sends it directly, with an APNs key kept in `secrets/`.

The operator's own house keeps its Home Assistant notify path until the app replaces it.

## Speech

The web view's mic and speaker buttons call into the app through a native bridge instead of the
box's Whisper and Chatterbox. Recognition uses Apple's on-device mode only, so speech never
goes to Apple's servers; if a device can't recognize on-device, the mic button says so and
chat still works. Replies are spoken with Apple's on-device voice.

## The web view

The app loads `/app` from the box over the tailnet, unchanged: chat, today, status, library,
recipes. The screens stay plain files on the box with no build step. The only new web code is a
small bridge (`window.webkit.messageHandlers`) for speech, notification sync and the
connection state, used only when the page detects it is inside the app.

## The pilot box

- An always-on computer booting from an SSD, never an SD card: SQLite writes all day.
- The brain, the stores and Ollama on CPU for the library's EmbeddingGemma only. No chat model.
- Home Assistant only if the household has devices worth it.
- A nightly backup per box (a USB drive is enough for 30 days). It holds their data, not ours.
- Problem reports on, and deleted from the box at day 30 whether it stays or comes back. A box
  that comes back is wiped.
- Set up by the operator before it arrives. The household installs nothing but the app.

## Each household runs its own tailnet

Decided 2026-10-31. The brain is unauthenticated and the network is its access control, so a
pilot household must never be able to reach another household's brain, the operator's included.
Each household runs its own Tailscale network, under its own login; their phones and box are
the only machines on it. For service during the pilot, the household shares the box (one
machine, not the tailnet) with the operator's tailnet. A shared machine is quarantined: it
answers connections but can't start them into the tailnet it is shared with. Confirm that on
the first setup. At day 30 the household unshares it. Never Tailscale Funnel: that puts a node
on the public internet.

## Open questions

1. **The smallest phone.** The 17 Pro has room to spare. Bench the oldest iPhone a pilot owns
   before promising it.
2. **Distribution.** TestFlight needs a paid Apple developer account, and so does the push
   key. That is the operator's account and card.
3. **Android.** One pilot is on Android with an older phone. Out of scope for the first build;
   that household's box may need to run the model itself.

## Build order

1. **Box only, no phone:** route every model call through `backend.chat()`; the queued-work
   table; the prompt renderer and LFM3 tool-call parser, tested for exact parity against
   llama-server.
2. **The phone engine:** app with llama.cpp, the socket, the device token, the web view. Gate:
   the held-out suite run through the phone backend lands on 91.1%, within run-to-run noise.
3. **Speech and notifications:** on-device speech, local notification sync, the content-free
   ping.
4. **The pilot box:** setup script, SSD image, backups, problem reports, day-30 removal.
