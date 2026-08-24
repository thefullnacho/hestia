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
| OCR | read a scale display or a printed label into digits + unit | RapidOCR/PaddleOCR (ONNX) for print; fixed-ROI or fine-tune for seven-segment | CPU, no VRAM | resident |
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

### Reading the scale: the OCR lane

The target capture is one photo. Tap the Bed 4 tag, put the tomatoes on the scale, take the
picture, and three facts should already be settled before anyone types anything: which bed,
which crop, how much. Only the third one is a vision problem.

| Fact | Where it comes from | Kind of work |
|---|---|---|
| Place (Bed 4) | the NFC tag's entity reference | lookup, no model |
| Crop shortlist | that bed's `plantings` attr in records (`garden_overview()` reads the same field) | lookup, no model |
| Crop | classifier, constrained to the shortlist | closed-set label |
| Quantity + unit | OCR read of the scale display | transcription |
| Anything unresolved | the human at review | judgment |

The crop step is worth stating precisely, because it is the part that makes this cheap: the
tag does not know it is tomatoes, **the bed's planting record does**. Bed 4 has a known,
short list of what is growing in it, so the classifier is not asked "what plant is this" over
all of botany; it picks between the two or three things actually planted there, and a
confident answer outside that list is a flag rather than a result. If the bed's planting list
is empty or stale, the classifier falls back to the open garden roster and the proposal says
which mode it used.

#### The tier: OCR reads, it never estimates

OCR is a third tier next to the classifier and the VL judge, and it has exactly two possible
outputs: a digit string with a unit and a confidence, or `unreadable`. There is no third
option, and in particular there is no estimate from appearance. A pile of tomatoes does not
imply three pounds. If the display is glared out, the row goes to review with the weight
blank and the photo attached, which is already the rule in "Uncertainty is useful" and now
has teeth: **a quantity may only come from pixels of a display or a printed label.**

- **Printed labels** (seed packets, feed bags, part numbers, expiry dates) are ordinary OCR:
  a small ONNX engine such as RapidOCR or PaddleOCR, CPU-side, roughly 100 MB of weights.
- **Seven-segment scale displays are a different problem** and should not be assumed to work.
  General OCR engines are trained on fonts, and segmented digits, low contrast, glare, and a
  decimal point one pixel tall break them. Candidates, in the order worth trying: a fixed-ROI
  classical read (ssocr-style) which becomes practical precisely because the scale's own tag
  pins the surface and framing, a small fine-tune on photos of this scale, or escalation to
  the VL judge. **Vet these on real photos of the actual kitchen and hanging scales before
  picking one.** This is a measurement, not a preference, and the tier design survives the
  engine swapping.
- The VRAM budget above is unchanged: the OCR tier runs on CPU inside `hestia-eyes.service`.
  Only a VL escalation costs GPU, and it stays on-demand and single-flight.
- Every read stores **the cropped display region** alongside the number. Review shows the
  digits next to the pixels they came from, so verifying a weight is a glance, not a memory
  test.

#### Guards that are lookups, not judgment

Determinism north star applies to the read as much as to the schedule. Each of these is a row
or an arithmetic check, and each one flags rather than corrects:

- **Decimal point:** `3.2` and `32` are both plausible tomato weights and differ by 10x. A
  read whose decimal is low-confidence is flagged as ambiguous with both candidates shown,
  never silently rounded to the likelier one.
- **Unit is read, not assumed.** The lb/kg/oz/g indicator is part of the transcription. Legible
  digits plus an illegible unit is an unresolved row, not a guess at pounds, and the unit then
  flows into `log_harvest()`'s existing `normalize_unit()` path unchanged.
- **Settling and tare:** a scale caught mid-settle shows a number that is not the weight. If
  the display exposes a stability indicator, read it; otherwise prefer a capture taken after
  the reading holds, and treat a session's two frames disagreeing as unresolved.
- **Range check against history:** `harvest_totals()` already knows what this crop has weighed
  all season. A read far outside that range is marked "unusual for this crop" in the proposal.
  It is a flag on a row a human is already reading. It never rewrites the number.

#### The verification lane: log it yourself, then check Hestia's read

The way to earn trust in an OCR number is to have the truth already written down next to it.
So for the first stretch, log the harvest yourself as you do today, and let Eyes read the same
photo independently. Review is then a **comparison**, which is the same posture as the
note-taker's inbox and `review_notes.py`: the model proposes in the open, you dispose, and
nothing enters records without you.

The rule that makes this safe:

- **Never two rows.** An Eyes read is reconciled against your own entry, not inserted beside
  it. Match on the capture session's batch ID when the entry came from the same NFC session,
  otherwise on (bed, crop) inside a short time window. Duplicate harvest rows would poison the
  season totals and the almanac's year-over-year deltas, which is a worse failure than a bad
  read.
- **Three outcomes.** *Agree*: the proposal is discarded, nothing is written, the agreement is
  counted. *Disagree*: your number stands, and the disagreement is kept with the display crop.
  *Eyes-only*: you did not log it, so it behaves as a normal proposal you approve or edit,
  exactly as in "The intended flow".
- **Disagreements are the product of this phase.** Each one is a labelled example of a read
  that failed, tagged with the surface it came from, which is what tells you whether the
  problem is the engine, the glare, or that particular scale.
- **The calibration record** lives at `data/eyes-ocr-calibration.jsonl` (under the gitignored
  `data/`, same as the eyes inbox): one line per read with the surface, raw read, confirmed
  truth, confidence, and engine. It is provenance for a model, not a domain record, so it does
  not go anywhere near `records_store`. Agreement rate per surface is the number that decides
  anything.
- **Shadow first, graduate on evidence.** OCR ships proposing only. It gets to pre-fill a
  weight in a batch proposal after a real agreement rate per surface says it earns it, and even
  then the confirm tap stays. Auto-filing a weight without review is not on the roadmap.

`review_eyes.py` mirrors `review_notes.py` (`list` / `promote` / `discard`) and adds a
`calibration` subcommand that prints agreement rate per surface, so the decision to trust a
read is a number you can look at rather than a feeling about how it has been going.

### Design rules

- **Batch first:** one review screen/inbox item can contain many rows; never make the user
  approve or wake the assistant once per crop, puppy, or maintenance action.
- **Template-specific records stay canonical:** harvest proposals use the existing `harvest`
  schema (bed, crop, quantity, unit, timestamp); whelping proposals use the existing
  birth/photo/lineage paths. The shared capture session is temporary—there is no parallel
  harvest or breeding store.
- **Uncertainty is useful:** identify likely crops/beds from the roster and images, but leave a
  missing/unclear weight visibly unresolved instead of fabricating precision.
- **Trust is earned per surface:** a read is only allowed to pre-fill a field after the
  calibration record shows it agreeing with hand-logged truth on that surface. Until then
  Eyes proposes and the human's own entry is canonical.
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
| OCR misreads the decimal (3.2 lb read as 32 lb) | low-confidence decimal is flagged ambiguous with both candidates shown, never rounded; range check against `harvest_totals()` marks it unusual; the display crop rides along so review is a glance |
| Model invents a weight the display never showed | quantity may only come from pixels of a display or label; the tier returns `unreadable` rather than an estimate, and the row reaches review blank |
| Eyes proposal duplicates a harvest the human already logged | reconcile, never insert beside: match on the capture batch ID, else on (bed, crop) in a short window; agreement discards the proposal instead of writing a second row |
| OCR trusted on a surface it was never good at | calibration is per surface, not global; `review_eyes.py calibration` prints agreement rate per scale/label type and pre-fill graduates per surface |

## Parked: Frigate / live cameras

The camera half (from the Kimi analysis) is **gated on real cameras existing** — the Arlos'
detection is too poor to feed anything. When that changes: Frigate runs on hl-relay
(OpenVINO on the iGPU, substream ~5 fps, motion masks), **never** on the Ti — detection is a
constant load and belongs next to HA/MQTT/recordings; the Ti's headroom is for the burst
tier. Events would enter this same lane at step 4 (classifier first, VL escalation), with
rate limits (per-camera cooldown, bounded queue, drop-with-log). Until then: note only,
don't build — the photo-ID lane is the whole product.
