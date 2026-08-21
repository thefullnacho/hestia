# Eyes — spec (photo-ID lane)

Vision for Hestia, scoped to what it's actually for right now: **identify things in photos
and propose entity updates to records**. Not a camera pipeline — there are no cameras worth
wiring (the old Arlos barely trigger; see "Parked: Frigate" below). The lane rides the
existing [`/ingest/photo`](hestia.py) flow and inherits its posture wholesale: vision
**PROPOSES, records DISPOSE** — the model never writes an entity or attr without a human
confirm, same as the note-taker's review inbox and the ingest endpoint's ⚠️new-entity warning.

Decision history (2026-07-22): classifier-first tiering + photo-ID scope set by Alex; VRAM
split and on-demand VL design adapted from a Kimi K3 analysis (conductor-pattern dispatch);
Frigate camera phases from that analysis parked until real cameras exist.

## What it replaces

Today the iOS Shortcut makes the *human* do the identification: pick the domain
(pet/garden/wildlife/asset), type the exact subject name, and the endpoint files the photo +
`photo` event (minting loudly if the subject is new). Eyes v1 inverts the interactive step:
**snap → Hestia says what it thinks it is → you confirm** instead of type. The typo/junk-entity
failure mode the ⚠️ warning exists for mostly disappears — proposals are drawn from the
records roster, not typed freehand.

## Model tiers (the design decision)

**ID is a label task, not judgment** (determinism north star). So the workhorse is a small
**closed-set classifier**, resident on the 4060 Ti next to the voice tenants; the 7B VL model
is an **on-demand escalation**, never resident.

| Tier | Job | Candidates | VRAM | Residency |
|---|---|---|---|---|
| Detector/classifier | wildlife species ID | MegaDetector → SpeciesNet | ~0.5 GB class | resident |
| Classifier | plant / broad taxa ID | BioCLIP (zero-shot, taxa-aware) | ~0.5 GB class | resident |
| VL judge | classifier shrugged (low conf / out-of-roster), attr extraction, "what's it doing?" | Qwen2.5-VL-7B Q4_K_M + f16 mmproj (already in `models/`) | ~8 GB loaded | **on-demand** |

Why a classifier beats a VL model here: it can't invent a wolf (closed set), it emits a
calibrated **confidence number** (which is what a *proposal* should carry), it answers in
milliseconds not seconds, and it leaves the Ti's VRAM alone. Candidate models get vetted at
build time (weights availability, license, local inference path) — the tier design doesn't
change if a specific model swaps out.

### Ti VRAM budget (revised classifier-first; measure with nvidia-smi in Phase 1 and correct)

| Tenant | Est. | Residency |
|---|---|---|
| hestia-whisper (faster-whisper, tcp 10300) | ~1.8 GB | resident |
| Chatterbox-Turbo TTS (tcp 10202) | ~2.5 GB | resident |
| classifier tier (both models) | ~1.0 GB | resident |
| **steady state** | **~5.3 / 16 GB** | |
| VL judge burst (rare) | +8 GB → ~13.3 GB | on-demand, single-flight |

Voice latency risk during a VL burst is real but rare-by-design: escalation only fires when
the classifier is unsure. Classifier inference itself is too small/fast to disturb voice.

### VL serving (adopted from the Kimi analysis, survives as-is)

Second user-level Ollama instance `hestia-ollama-eyes.service`, `OLLAMA_HOST=127.0.0.1:11435`,
model created from the local GGUF + mmproj via Modelfile. Per-request `keep_alive: 0`
(optionally `OLLAMA_KEEP_ALIVE=2m` as a burst coalescer). **Mandatory env belt** (the 0.30.x
Vulkan gotcha, see MODEL_EVAL.md): `OLLAMA_VULKAN=0`, `CUDA_DEVICE_ORDER=PCI_BUS_ID`,
`CUDA_VISIBLE_DEVICES=1` — same pinning as the voice units. Single-flight, temp 0,
`n_predict` capped, images downscaled ≤1024 px. No CUDA MPS (three contexts; default
time-slicing is fine).

## Flow (Phase 1, the smallest shippable)

1. New service `hestia-eyes.service` on the GPU box (Ti): loads the classifier tier, serves
   one internal endpoint (`127.0.0.1`, e.g. `:8731/identify`) — image in, `[{label, taxon,
   confidence}]` out. Torch process, isolated from the brain like the Wyoming services.
2. `/ingest/photo` grows an **identify mode**: when the form omits `subject` (or sends
   `identify=1`), the endpoint calls the eyes service and returns a **proposal** instead of
   filing: top guesses, confidence, and the roster match (classifier label resolved against
   records aliases — e.g. SpeciesNet's "Cyanocitta cristata" → the existing "blue jay" entity).
   Nothing is written. HTTP round 2 with the confirmed `subject` files it exactly as today.
3. The Shortcut adds one step: show the proposal, tap to confirm (or override), re-post.
   The ⚠️new-entity warning path is untouched — an out-of-roster ID *is* the new-entity case,
   surfaced before filing instead of after.
4. Low-confidence / no-roster-match → the proposal says so and (Phase 2) escalates to the VL
   judge for a better guess + description. Escalation result is still just a proposal.

**Never**: auto-file on high confidence. The confirm tap stays. (Revisit only after months of
boring accuracy, and even then per-domain.)

## Phase 2 — entity-update proposals

Beyond "file this photo": vision proposes **attr updates** on the matched entity (garden bed
observations, pet coat/condition notes, asset state). Same shape as the note-taker: proposal
files to a review inbox (`data/eyes-inbox/<stamp>.md` — snapshot path, subject, proposed
attrs, confidence, model tier that produced it), promoted via a `review_notes.py`-style flow.
VL judge does the language work here; the classifier only gates whether it fires. Nothing in
the Eyes lane ever calls `records_store` write paths directly.

## Product priority — frictionless field capture

The highest-value use is not species ID; it is making hands-busy real life easy to log. A heavy
harvest morning, a litter of newborn puppies, equipment maintenance, and an inventory intake
all share the same shape: identify the real-world thing, take a measurement/photo/note, then
review a set of structured records once. Repeating eight individual voice commands such as
“Nabu, three pounds of tomatoes” turns record-keeping into another chore, so this lane must
make a **single batch review** the normal path.

### Physical context: NFC tags

NFC is the physical front door to a capture session: **tap the real-world object → Hestia
already knows the context**. The tag carries only a stable entity reference or private Hestia
URL/shortcut; it never writes a record itself and never contains mutable facts such as a crop
list or maintenance state.

- Bed/zone tag → harvest, planting, treatment, watering, or inspection draft scoped to that
  place.
- Equipment tag → use, fuel/refill, maintenance, repair, or photo draft scoped to that asset.
- Whelping-area/litter tag → one newborn-batch session inheriting dam/sire/litter context.
- Scale or harvest-basket tag → starts a measured intake session before the first item is put
  down.

Each physical tag is an aid to fast context selection, not an authorization token. The capture
and review gate remain human-controlled.

**Hardware purchased (2026-08-21):** 50 standard NTAG213 adhesive tags for $13 (about $0.26
each), plus separate outdoor/on-metal tags and general stick-anywhere tags. Start with a small
trial set—one garden bed, the scale, a harvest basket, one maintenance-heavy asset, the
whelping area, and one temporary-project tag—before encoding the wider estate. Use the
outdoor/on-metal tags for exposed beds, tools, and metal equipment; use adhesive tags for bins,
jars, baskets, seed trays, and indoor supplies.

### The intended flow

1. Tap an NFC tag (or open the capture screen) to start a timestamped session with the right
   place, asset, or litter context already filled in. Take one or several photos: produce on
   the scale, a tray/colander, labels, a newborn puppy, or the equipment itself. A shortcut can
   add a short optional note or voice transcript, but must not require a typed subject for each
   item.
2. Eyes groups the session into a **batch proposal** and suggests one or more structured
   actions. Harvest is the first template, for example:

   ```text
   Harvest batch, Aug 21
   - Tomatoes — Bed 4 — 3 lb
   - Cucumbers — Bed 2 — 1 lb
   - Beets — Beets Round Bed — 18 count
   ```

   A whelping template instead proposes a newborn row with litter/parent context, sex/name if
   supplied, birth weight, and first photo. Each row carries its source photo(s), candidate
   entity match, confidence, and any uncertainty (for example, “weight unreadable — enter
   amount”). Photos make the later conversation concrete; a scale/label can be read when
   visible, but the model must never invent a quantity from appearance.
3. The existing propose/review posture is the commit gate: edit, remove, or add rows to the
   batch, then approve all or selected rows once. Approval calls the existing
   `records_store.log_harvest()` path for each confirmed harvest row (or the existing birth/photo
   paths for a whelping row); no Eyes model writes the database directly. The photo event and
   resulting domain event share a batch ID, so the almanac can remain structured while the human
   evidence stays attached.
4. The follow-up is conversational rather than command-shaped: “add another pound of peppers,”
   “those tomatoes were from Bed 1, not Bed 4,” or “save this batch.” The assistant updates the
   pending proposal, not the records DB, until approval.

### Design rules

- **Batch first:** one review screen/inbox item can contain many rows; never make the user
  approve or wake the assistant once per crop, puppy, or maintenance action.
- **Template-specific records stay canonical:** harvest proposals use the existing `harvest`
  schema (bed, crop, quantity, unit, timestamp); whelping proposals use the existing
  birth/photo/lineage paths. The shared capture session is temporary—there is no parallel
  harvest or breeding store.
- **Uncertainty is useful:** identify likely crops/beds from the roster and images, but leave a
  missing/unclear weight visibly unresolved instead of fabricating precision.
- **Proof, not surveillance:** this is an intentional capture flow from a phone, not a garden
  camera pipeline. Keep the image path and batch provenance auditable and retain no hidden
  background analysis.

This is the best first user experience for Eyes: NFC identifies the thing in front of you, a
photo/scale/short note is the low-friction capture, batch review is where collaboration happens,
and the database changes only after the household agrees the structured record is right.

## Phase 3 — voice hook

"Hestia, what bird was that?" after a photo lands: the almanac-skill pattern (deterministic
router) injects the latest eyes proposal into the prompt. Cheap, no new model work.

## Failure modes

| Failure | Mitigation |
|---|---|
| Classifier confidently wrong (lookalike species) | confirm tap is the backstop; roster constraint limits damage to plausible-local mistakes; confidence stored in the event attrs for later audit |
| Out-of-roster true positive (genuinely new species) | that's a feature — ⚠️new-entity proposal with the species guess pre-filled |
| VL judge hallucinates on escalation | temp 0, "say 'uncertain' if unsure" prompt, propose-only; judge output never auto-files |
| VL load OOMs against a TTS burst | single-flight + keep_alive 0 (seconds-long window); eyes Ollama dies → Restart=on-failure; voice tenants unaffected (already resident) |
| Eyes service down | ingest endpoint degrades to today's manual-subject flow — identify mode returns "eyes offline, name it yourself" |
| Vulkan regression binds eyes Ollama to the 5080 | env belt above + post-start nvidia-smi assertion before the unit reports ready |

## Parked: Frigate / live cameras

The camera half (from the Kimi analysis) is **gated on real cameras existing** — the Arlos'
detection is too poor to feed anything. When that changes: Frigate runs on hl-relay
(OpenVINO on the iGPU, substream ~5 fps, motion masks), **never** on the Ti — detection is a
constant load and belongs next to HA/MQTT/recordings; the Ti's headroom is for the burst
tier. Events would enter this same lane at step 4 (classifier first, VL escalation), with
rate limits (per-camera cooldown, bounded queue, drop-with-log). Until then: note only,
don't build — the photo-ID lane is the whole product.
