# Hestia Lite end to end on the Pi 4 (2026-10-26)

The whole voice path on one Raspberry Pi 4 (4GB, no heatsink), every stage the one benchmarked
alone: Moonshine BASE hears, EmbeddingGemma routes to a domain (nearest training example,
0.08 gap gate), the fine-tuned FunctionGemma v4 (Q8_0 GGUF through Ollama) writes the call,
the said-words gate checks it, code builds the reply from the call, KittenTTS nano int8 says it
(voice Jasper). Actions are a **dry run**: the call is printed, not sent to Home Assistant.

Input: the 30 synthetic clips from `../lite-stt` (KittenTTS speech, so clean). One pass, K=1.
Models warm, all loaded at once; startup 14 to 23 s.

## What it did

| | Count | |
|---|---|---|
| Actions | 16 | **15 right, 1 wrong**: "play some music in the kitchen" played the query "kitchen" |
| Stayed quiet | 6 | all right ("never mind", "thank you", "what day is it", "cancel that", ...) |
| Asked "did you mean X or Y?" | 8 | all out of Lite's scope: garden, records, knowledge, media questions |

The 8 asks are the router meeting questions only the house brain can answer. Nothing wrong
happens, but the question makes no sense to the person. Lite needs a "that's for Hestia"
answer (or a hand-off) instead of a domain question.

## How long

From the end of speech to the reply audio, on the 16 actions:

| Stage | Median | Worst |
|---|---|---|
| Speech to text | 1.1 s | 2.0 s |
| Router | 0.7 s | 0.9 s |
| Caller | **3.7 s** | 5.3 s |
| Reply speech (whole clip synthesized) | 1.5 s | 2.4 s |
| **To reply audio** | **7.1 s** | 9.8 s |

Quiet and asked turns end in 1 to 4 s. The estimate of about 3 to 4 s for a round trip did
not hold: the caller is the cost. It re-reads its whole tool prompt (760 to 2,500 characters)
on every call, because Ollama keeps one cached prompt per model and the domain changes between
turns. The same call took 1.8 s warm with the prompt cached. Levers, not yet tried: a cached
prompt per domain, shorter tool descriptions, and speaking the reply sentence by sentence.

## Fixed during the run

- Moonshine writes "to-morrow" and "living-room". The said-words gate compared the caller's
  "tomorrow" to that and refused a correct reminder. Transcripts are joined back first.
- A near-tie between `none` and a domain asked "did you mean none or alerts?". It stays quiet.
- KittenTTS prints to stdout, and its loader checks the Hugging Face Hub at startup. The worker
  keeps stdout for its own protocol and every model loads offline from the Pi's cache.
- Ollama drops special tokens from raw output, so the caller GGUF marks its seven tool-call
  tokens plain (`plain_tokens.py`). Ids and weights are unchanged.

## Notes

- The Pi hit its soft thermal limit (`throttled=0x80000`) during the one-time embedding of the
  router's 3,400 examples (38 minutes, cached after) and ran at 71 to 75°C under load.
- Next: real speech (the household's recordings and the utterance log), and actuation against
  Home Assistant, which needs a decision on how the Pi reaches HA.
