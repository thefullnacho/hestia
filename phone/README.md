# The iPhone app

The phone's half of PHONE-APP.md, build order step 2. This first build is the model engine
only: it dials the box's `/v1/model/socket` with a device token, runs each prompt through
llama.cpp, and streams the answer back. No web view, speech or notifications yet.
`benchmarks/phone-render/fake_phone.py` is the same behavior in Python.

| File | What |
|---|---|
| `Hestia/Link.swift` | The socket: hello with the model's sha256, download the box's model when they differ, answer each `complete` in order |
| `Hestia/Engine.swift` | llama.cpp on a string, run the way llama-server's `/completion` runs it |
| `Hestia/ModelFile.swift` | The GGUF in Application Support: sha256, and fetching it from `/v1/model/file` |
| `Hestia/Keychain.swift` | The device token, this device only |
| `Hestia/HestiaApp.swift` | Connects in the foreground, hangs up in the background; one settings screen |

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
   tailscale serve --bg --https=8444 http://<box tailnet address>:8734
   ```

   A path on the target doubles every request: tailscale serve joins the target's path to the
   request's, so the socket arrives as `/v1/model/socket/v1/model/socket` and the model
   download as `/v1/model/socket/v1/model/info`. `tailscale serve status` shows the target.
3. In the app, enter `https://<box MagicDNS name>:8444` and the token, then Save and connect.
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
