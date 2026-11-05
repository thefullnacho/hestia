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
- The box renders the prompt and parses tool calls; the phone only runs the model on text.
- The briefing is a daily briefing, worded to read the same whenever it is opened.
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
- Several phones can be connected. The box uses the one that connected most recently.
- When the app goes to the background, iOS closes the socket. That is expected, not an error.

**The socket needs a token,** unlike the rest of the brain. Whatever hosts the model sees every
prompt (the household's memory and records) and writes every tool call. A device token, issued
at setup and kept in the Keychain, keeps a stray tailnet device from becoming the household's
model.

**Built 2026-11-04 (`brain/phone.py`).** What the app implements, one JSON object per WebSocket
message, `Authorization: Bearer <device token>` on the connect:

```
phone -> box  {"type": "hello", "device": "...", "model_sha256": "<sha256 of the GGUF it runs>",
               "push_token": "<APNs device token, hex>"}       (optional; see Notifications)
box -> phone  {"type": "ready", "model_sha256": "<the box's>", "model_ok": true|false}
box -> phone  {"type": "complete", "id": "7", "prompt": "<rendered text>", "n_predict": 768,
               "temperature": 0.3, "stop": ["<|im_end|>"], "json_schema": {...}}   (schema rarely)
phone -> box  {"type": "delta", "id": "7", "text": "..."}                         (zero or more)
phone -> box  {"type": "done", "id": "7", "stop_type": "eos"|"limit"|"word",
               "tokens_evaluated": 412, "tokens_predicted": 20}
phone -> box  {"type": "error", "id": "7", "message": "..."}
```

The phone runs one request at a time, in the order they come. `model_ok: false` means the phone
runs a different model than the box serves: it downloads the box's (`GET /v1/model/info` for name,
size and sha256, `GET /v1/model/file` for the file, both with the device token) and reconnects.
Only a phone whose model matches is used. On connect the box also finishes any model work that
waited (`model_jobs`). With no phone connected, a turn from another window gets a plain "open the
app" reply. Setup on the box: `python brain/phone.py add-device <name>` prints the token once;
`HESTIA_BACKEND=phone`, `HESTIA_PHONE_MODEL` (the GGUF), `HESTIA_CHAT_TEMPLATE` and
`HESTIA_BOS_TOKEN` select and describe the model. `benchmarks/phone-render/fake_phone.py` plays the
phone's part against a llama-server, for testing the box without the app. `/health` and the turn log
name the GGUF as the model, and `/health` says whether a phone is connected; with none it still
reads `ok`, since everything but the model still answers.

## Who renders the prompt: the box

Two ways to split the model work:

1. **The phone is a plain completion engine** (decided 2026-10-31, as the more stable split). The box renders the conversation
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
`HESTIA_BACKEND=phone`: render, send down the socket, parse. The journal, feed, briefing,
note-taker and recipe organizer used to post to Ollama directly; since 2026-11-04 they come
through `backend.complete()` (no tools), so every model call in the brain passes one door.

One catch for the phone: the timed jobs run as their own processes, but the phone's socket is
held by the brain's process. A job can't reach the phone itself. In phone mode it writes its
model step to a queue in `hestia.db`, which the brain drains when a phone connects (next section).

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

Built 2026-11-04 (`brain/model_jobs.py`). With `HESTIA_BACKEND=phone` a job does everything
code can do on time, then writes its model request to the `model_jobs` table in `hestia.db`
with what it needs to finish. The jobs and the brain share that database, so no extra hop is
needed. When a model is reachable the brain drains the queue oldest first: it runs each request
and hands the answer to that kind's finisher, which does exactly what the job would have done
with it (word the stored briefing, save the feed picks, propose the note-taker's facts). A newer
briefing or feed ranking replaces a waiting one; note-taker work waits up to a week; anything
past its expiry is dropped, not delivered stale. A failed run waits for the next drain, three
tries in all. In phone mode a direct model call fails loudly instead of reaching for a server
that isn't there; the journal then writes its plain-facts entry, as it does when the model is
down.

A box with no phone connected answers a model request the way the brain answers a down Ollama
today: a clear message, not a hang. A CPU model on the box as a fallback is possible but
unmeasured. Don't build it until a pilot misses it.

## Notifications

Two paths, so that a reminder's text never has to leave the house:

- **Local notifications for what the box already knows.** While the app is open it syncs the
  upcoming reminders and schedules them with iOS on the phone, with their full text. iOS fires
  them on time with the app closed. No Apple server sees them.
- **A content-free ping for everything else.** A reminder made from another window while the
  app is closed, a threshold crossing, the daily briefing: the box sends Apple's push service
  a notification that says only that something is waiting. Opening it syncs and shows the
  content. The box sends it directly, with an APNs key kept in `secrets/`.

The operator's own house keeps its Home Assistant notify path until the app replaces it.

**The reminder sync, built 2026-11-04 (box side).** Rows only, no model.

```
GET /v1/phone/reminders?days=14          Authorization: Bearer <device token>
                                         If-None-Match: "<last sync>"   (optional)
200  {"device": "dawn-15", "person": "dawn"|null,
      "reminders": [{"id": "reminder-12-3f9a0c1b2d4e", "text": "call the farrier",
                     "due_at": "2026-11-06T09:00:00-05:00", "person": "dawn"|null}, ...],
      "sync": "<sha256 of the list>", "truncated": false,
      "complete_until": "2026-11-18T14:02:11-05:00", "days": 14,
      "generated_at": "2026-11-04T14:02:11-05:00"}
304  the list matches If-None-Match            401  no or unknown device token
```

- **What is in it:** reminders not yet fired, due after now and within `days` (default 14,
  clamped to 1..31), soonest first, at most 64, since iOS keeps 64 pending local notifications
  per app. If the cap cut the list, `truncated` is true and `complete_until` is the last entry's
  time: sync again before then. Otherwise `complete_until` is the end of the horizon.
- **Using it:** `id` is the notification identifier. It is unique to the row for its lifetime,
  so a reused row number gets a new one. When `sync` differs from the last one the app kept
  (the `ETag` carries it too), remove every pending Hestia notification and schedule the list.
  `due_at` carries its offset, so schedule it as an absolute date.
- **Reminders are one-shot.** The store has no repeating reminders, so each row is one
  notification. If repeats arrive, the box expands each into its occurrences inside the window,
  each with its own `id`, and the app needs no change.
- **Whose reminders:** a reminder's `person` names who it is for (null is the household). The
  operator ties a device to a person with `python brain/phone.py tie <device> <person>`
  (`-` unties it), kept in `data/phone_people.json`, never guessed from the name. A tied phone
  gets that person's reminders. An untied phone is a default phone: the household's reminders,
  plus those of anyone with no phone tied, the same fallback `reminders_tick.py` uses.
- **Double pings.** `reminders_tick.py` still sends each due reminder through Home Assistant.
  A house that also runs the app gets both until it turns one off. Which to turn off, and how,
  is not decided yet.

**The ping is built 2026-11-04 (`brain/push.py`); the app's half is not.** What the app does:
ask iOS for notification permission and register for remote notifications, then send the APNs
device token (the `Data` from `didRegisterForRemoteNotificationsWithDeviceToken`, as lowercase
hex) in every socket hello:

```
phone -> box  {"type": "hello", "device": "...", "model_sha256": "...", "push_token": "<hex>"}
```

The box files it under the device its bearer token names (not the hello's `device` field). Sending
it on every connect keeps a token iOS rotated current; leaving the field out keeps the stored one.
A value that is not 64 to 200 hex characters is ignored. Tapping the ping only needs to open the
app: it connects, the box drains waiting model work, and the web view syncs.

When the box pings, deterministically: model work is queued (`model_jobs`) while no phone is
connected, one ping however many jobs; or a reminder is made from another window (the MCP
server, web chat) and that phone's app is closed, each closed phone pinged, since each syncs its
own reminders. A phone gets at most one ping per `HESTIA_PUSH_DEBOUNCE_MIN` minutes (default 30)
unless its app connected since. Threshold watchers are not wired to it; an operator house keeps
Home Assistant notifications for those. The ping's words are
fixed in code (SECURITY.md, "Phone notifications"). Apple's 410 Unregistered or BadDeviceToken
drops the stored token until the app sends a new one.

Box config, in `secrets/push.env` (template `deploy/push.env.example`): `HESTIA_APNS_KEY_FILE`
(the `.p8`, a relative path is read under `secrets/`), `HESTIA_APNS_KEY_ID`,
`HESTIA_APNS_TEAM_ID`, `HESTIA_APNS_BUNDLE_ID`, and `HESTIA_APNS_SANDBOX=1` while the app is a
development build (its tokens only work on the sandbox host). Without a key, a box with
`HESTIA_BACKEND=phone` logs each ping as a dry run; any other box without a key does nothing.

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

1. **Box only, no phone:** route every model call through `backend.py` (done 2026-11-04: `chat()`
   for turns, `complete()` for everything else); the queued-work table (done 2026-11-04:
   `model_jobs.py`, drained by the brain once step 2 gives it a phone); the prompt renderer and LFM3 tool-call parser, tested for exact parity against
   llama-server.
2. **The phone engine:** app with llama.cpp, the socket, the device token, the web view. Gate:
   the held-out suite run through the phone backend lands on 91.1%, within run-to-run noise.
   The box half is done (2026-11-04): `chat_format.py` renders byte for byte with llama-server,
   and the phone's exact path run on the box (`HESTIA_BACKEND=completion`) scores 92.7% against
   93.3% through llama-server's chat endpoint (`benchmarks/phone-render/`).
   The phone half has a first build (2026-11-04, `phone/`): llama.cpp, the socket, the device
   token and the model download, run end to end in the simulator against `phone.py`. Prompt reuse
   across calls works through context checkpoints, as in llama-server. **Gate met 2026-11-04:**
   the held-out suite on an iPhone 17 Pro scored 292/315 = 92.7%, the same as the phone path on
   the box, at a 1.8 s median turn (`benchmarks/phone-render/RESULTS.md`). Not yet: the web view,
   resuming an interrupted model download, and a schema-exact `json_schema` (`phone/README.md`).
3. **Speech and notifications:** on-device speech, local notification sync, the content-free
   ping. The box half of the sync is built (2026-11-04, `GET /v1/phone/reminders`, above).
4. **The pilot box:** setup script, SSD image, backups, problem reports, day-30 removal.
