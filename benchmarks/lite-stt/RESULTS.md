# Moonshine speech recognition on the Lite floor hardware

Pi 4 Model B, 4GB, moonshine-voice 0.1.5 (pip, no torch, 128 MB venv), 2026-10-24. 30 typical
commands (`commands.txt`) spoken by KittenTTS nano int8 across its 8 voices (`gen_clips.py`).
Synthetic speech is clean, so these error rates flatter every model; real recordings are the
next step. Latency is the time to transcribe a whole clip after it ends. Hyphenated words count
as joined (BASE writes "to-morrow", "to-day").

| Model | Word error | Exact | Median latency | Max | RTF | Peak RSS |
|---|---|---|---|---|---|---|
| TINY | 11.6% | 15/30 | 0.75s | 1.34s | 0.24 | 179 MB |
| **BASE** | **1.8%** | **28/30** | **1.07s** | **1.95s** | **0.35** | **432 MB** |
| TINY_STREAMING | 15.2% | 12/30 | 1.25s | 2.23s | 0.41 | 231 MB |
| SMALL_STREAMING | 11.6% | 15/30 | 2.75s | 4.87s | 0.90 | 491 MB |
| MEDIUM_STREAMING | 9.8% | 18/30 | 3.87s | 6.68s | 1.24 | 824 MB |

- **BASE is the pick on this data.** Its two misses: "Log that" heard as "Logged that", and
  "living-room" (a scoring artifact of the hyphen rule).
- The streaming models were fed whole clips, so their main advantage (transcribing while the
  person talks) is not measured, and their scores are dragged down by a stray "You" or "Okay"
  at clip start and by writing numbers as digits ("30%", "at 9"), which a parser handles.
  A streaming test against a live microphone would be the fair comparison.
- Keyterm biasing (`set_keyterms`) works only on the streaming models. It barely moved them
  here, but this set has few unusual names; household names are where it should matter.
- The full voice stack measured on the Pi so far: EmbeddingGemma router, Moonshine BASE
  (432 MB) and KittenTTS nano int8 (256 MB), with FunctionGemma's GGUF still to run there.
- Licence: MIT for the English models (moonshine-ai/moonshine).

Next: 20 or so real commands from each household voice, recorded at desk distance.
