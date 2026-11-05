# The iPhone app

The phone's half of PHONE-APP.md, build order steps 2 and 3. It runs the household's model:
it dials the box's `/v1/model/socket` with a device token, runs each prompt through llama.cpp,
and streams the answer back (`benchmarks/phone-render/fake_phone.py` is that part in Python).
Around that it shows the box's `/app` in a web view, with a native bridge for on-device speech,
reminders as local notifications, and the push token for the box's content-free ping.

| File | What |
|---|---|
| `Hestia/Link.swift` | The socket: hello with the model's sha256 and the push token, download the box's model when they differ, answer each `complete` in order |
| `Hestia/Engine.swift` | llama.cpp on a string, run the way llama-server's `/completion` runs it, with context checkpoints for reuse |
| `Hestia/ModelFile.swift` | The GGUF in Application Support: sha256, and fetching it from `/v1/model/file` |
| `Hestia/WebView.swift` | `/app` in a WKWebView and the bridge, version 1 (PHONE-APP.md, "The web view") |
| `Hestia/Speech.swift` | Listening (`requiresOnDeviceRecognition`) and speaking (AVSpeechSynthesizer) for the bridge |
| `Hestia/Notifier.swift` | The reminder sync to local notifications, and the push token from iOS |
| `Hestia/Keychain.swift` | The device token, this device only |
| `Hestia/HestiaApp.swift` | Connects and syncs in the foreground, hangs up in the background; the settings screen |

## Build

The llama.cpp framework is 870 MB and git ignores it. Build it in a llama.cpp checkout, ideally
at the commit the box's llama-server runs, then copy it in:

```bash
cd ~/src/llama.cpp && ./build-xcframework.sh
```

```bash
LLAMA_CPP=~/src/llama.cpp phone/fetch-llama.sh
```

Copy `Local.xcconfig.example` to `Local.xcconfig` and set your signing team, then open
`Hestia.xcodeproj`. Sources sit in a synchronized folder: a new `.swift` file in `Hestia/` is
part of the target without touching the project file.

The shared scheme turns off Metal API validation and frame capture, which halved the speed in
the iPhone bench (`benchmarks/RESULTS.md`). For speed numbers, still launch from the home screen.

## Use

iOS refuses a plain `ws://` socket to the box (App Transport Security), so the phone reaches it
as `wss://` through `tailscale serve`, which holds the box's tailnet certificate and forwards to
the brain on its private address.

1. On the box, start a brain in phone mode. `benchmarks/phone-render/test_box.sh` makes one
   beside the real brain and prints a device token on its first start.
2. Put HTTPS in front of it, pointing at the brain's root, **with no path**:

   ```bash
   tailscale serve --bg --https=8443 http://<box tailnet address>:8734
   ```

   A path on the target doubles every request: tailscale serve joins the target's path to the
   request's, so the socket arrives as `/v1/model/socket/v1/model/socket` and the model
   download as `/v1/model/socket/v1/model/info`. `tailscale serve status` shows the target.
3. In the app, enter `https://<box MagicDNS name>:8443` and the token, then Save and connect.
   A `wss://` address, with or without `/v1/model/socket`, works too; the app adds the paths
   itself.

The first connect downloads the model (about 1 GB, keep the app open); after that the status
reads "connected, ready" and the log shows each request.

In the simulator, a box on this Mac works over plain `ws://127.0.0.1:<port>`: local addresses
are allowed.

## How it matches llama-server

The box scored the phone's path through llama-server's `/completion` (92.7% held out), so the
engine copies what that endpoint does: tokenize with special tokens added and parsed, the
default sampler chain (top-k 40, top-p 0.95, min-p 0.05, temperature, then a random draw;
greedy at temperature 0), special tokens not printed, stop words cut and never streamed, and
`stop_type` reported as `eos`, `limit` or `word`.

Known differences, for the step 2 gate:

- **`json_schema`** is honored as "any JSON object", not the schema itself. The schema to
  grammar converter is C++ outside the framework; the box validates every call's arguments.
  The box only sends a schema to repair one tool call.
- **Prompt reuse goes through checkpoints, as in llama-server.** LFM3 is hybrid: its recurrent
  half cannot be cut back to an earlier position. So while reading a prompt the engine saves
  that half 4 + n_ubatch and 4 tokens before the end, and the next request restores the latest
  checkpoint inside the prefix it shares, trims the attention half to match, and reads only
  the rest. Within a turn the second call reads its new tokens, not the whole prompt. In the
  simulator a two-call turn's 5,065-token second prompt took 1.6 s reused against 133 s from
  scratch (CPU only there). As llama-server warns for `cache_prompt`, results are not bit for
  bit those of a fresh read, since the prompt goes through in different batches; at
  temperature 0 the reused and fresh answers to that turn were worded differently.
- **Across turns, a checkpoint marks where the prompt changed.** With the tail layout the front
  (system prompt, tools, history) stays the same and the tail changes, and the tail can be
  longer than the 516 tokens the end checkpoints cover. So the engine also checkpoints where a
  prompt first differs from the one before it; the next turn restores there and reads only its
  new message and tail. The first turn that differs still reads in full, since that point is
  not known before it. In the simulator, three one-call turns with a 5,600-token prompt and a
  700-token changing tail took 158 s, 156 s and then 19 s. With today's template that point
  is usually the end of the fixed system prompt (about 1,980 tokens): routing changes the tool
  list, which the template places right after it, so that checkpoint is restored on nearly
  every first call. Up to 16 checkpoints are kept and the least recently used goes first, so
  that one stays however many calls a chat makes. Nothing here depends on the layout.
- **The model downloads only in the foreground.** Leaving the app mid-download starts it over.

## The web view and the bridge

The app shows `/app` from the saved box address once it has an address and a token; until then
it shows its settings. Inside the page, a **two-finger long press** opens the settings again,
and a page that will not load offers Retry and Settings. Links to the box's own pages stay in
the app; anything else opens in Safari.

The bridge follows PHONE-APP.md, "The web view", version 1. The app sends nothing until the
page says `hello`, answers with `hello` and the current `connection`, and sends `connection`
again on every change of the model socket. A page message on another version gets the app's
`hello`, so the page turns its bridge off. The settings log lists each message type that
crosses the bridge (never what was said or spoken), for debugging on the phone.

- **Listening:** on-device only (`requiresOnDeviceRecognition`). The microphone and speech
  permissions are asked the first time the mic is tapped. Every `listen` ends with exactly one
  final `transcript` or one `listen_error`; after `listen_stop` a final arrives within 2 s even
  if recognition never says it is done. The audio engine starts and stops off the main thread.
  iOS's own permission prompt says speech "will be sent to Apple"; that is Apple's fixed wording,
  and with on-device recognition required nothing is.
- **Speaking:** the system voice for the phone's language. A new `speak` replaces the one
  playing, and every `speak` ends with one `speak_finished`, including when stopped or replaced.
- **Zoom:** iOS zooms into a text field under 16 px and leaves the page zoomed, so the app holds
  the page's scale at 1. The lasting fix is a 16 px input font in `clients/app.css`, which also
  stops it in Safari.

## Reminders and the ping

On every open, and when the page sends `sync_notifications`, the app fetches
`GET /v1/phone/reminders?days=14` with `If-None-Match`. On `304` nothing changes; when `sync`
differs it removes every pending notification and schedules the list, each at its absolute
`due_at`, with the reminder's `id` as the identifier. Notifications show while the app is open
too. The settings show the last result.

The app asks for notification permission on first open and registers for remote notifications.
iOS's token goes in every socket hello as `push_token`, and a token that arrives or rotates
after a hello makes the app reconnect to send it. Development builds carry
`aps-environment: development`, so the box needs `HESTIA_APNS_SANDBOX=1` for them. Tapping the
ping only opens the app.

Checked in the simulator against the real brain in phone mode: the page's handshake, the tag
reading "model ready", the sync (`200`, then `304`), a scheduled reminder delivered on time,
and the push token stored under the device. In the simulator CoreAudio stalled bringing up the
input, so the microphone and voice were checked on an iPhone 17 Pro: a spoken command came back
as a final transcript, went to the model, and the answer was spoken through to `speak_finished`.
A ping from the box through the APNs sandbox arrived too; tapping it opened the app, which
connected and finished the queued work.

A page the box answers with an error status (tailscale serve gives `502` and an empty body when
the brain behind it is down) shows as "Can't reach the box" with Retry, not as a blank page.
