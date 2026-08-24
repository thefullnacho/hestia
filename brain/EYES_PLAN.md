# Eyes — spec (photo-ID lane)

Vision for Hestia, scoped to what it's actually for right now: **identify things in photos
and propose entity updates to records**. Not a camera pipeline — there are no cameras worth
wiring (the old Arlos barely trigger; see "Parked: Frigate" below). The lane rides the
existing [`/ingest/photo`](hestia.py) flow. Posture, refined 2026-08-24: **the model never mints
an entity or writes durable memory without a human confirm** (invariant #4, the ⚠️new-entity
warning, the note-taker's inbox), while *events* in the field-capture lane are written at capture
time and corrected in the morning briefing. Entities are expensive to retrofit; events are cheap
and correctable. See "Why capture commits, and correction happens later".

Decision history (2026-07-22): classifier-first tiering + photo-ID scope set by Alex; VRAM
split and on-demand VL design adapted from a Kimi K3 analysis (conductor-pattern dispatch);
Frigate camera phases from that analysis parked until real cameras exist.
Added 2026-08-21: NFC-assisted field capture. Added 2026-08-24: the OCR tier and its
per-surface calibration gate (decided), plus accumulator/reset tags and the landing-site
survey (written down for review, not decided). Revised later the same day: the batch-review commit
gate is replaced by write-on-capture plus correction in the morning briefing, after the note-taker
inbox showed 29 pending against 4 promoted over 71 days.

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

**Never**: mint a new entity on the model's say-so. The confirm tap stays for anything that
creates a roster entry, because entities are the expensive-to-retrofit half of the substrate.
Filing an *event* against an entity that already exists is a different risk and, for the field
capture lane, is written immediately and corrected later. See "Why capture commits" below.

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

### Accumulator and reset tags (proposed 2026-08-24, not decided)

A tap is a bare session marker: it says something happened to this object, not what. The way
out is not to make the tap descriptive, it is to **let the tag carry the meaning**. One tag per
action, not one tag per object. At $0.26 a tag this is the cheap side of the trade.

Give an object as many tags as it has actions worth counting, which in practice is two: the
action that **accumulates**, and the action that **resets** the counter.

| Object | Accumulator tag | Reset tag |
|---|---|---|
| Washing machine | lid: "washed a load" | filter door: "drained the filter" |
| Mower | handle: "used it" | oil cap: "changed the oil" |
| Chainsaw | case: "used it" | bar: "sharpened / re-tensioned" |

Nothing needs disambiguating, because the human resolved the meaning by choosing which tag to
touch. No menu, no session state, no model. The reset tap is simultaneously the service record
and the zeroing of the count, so there is only ever one thing to remember to do.

The motivating case is the washing machine filter: about every 20 washes, reliably forgotten,
and never tracked because there was no counter to hang it on.

**Prerequisite: FIXED 2026-08-24.** `due_assets()` (`records_store.py:475`) found an asset's
*last logged event of any kind* and compared its age to `attrs.interval_days`. The query had no
filter on `kind`, so any event reset the maintenance clock. The path was reachable in shipped
code, not only by future use-taps: the photo intake's `asset` domain files a `photo` event against
the asset itself, so photographing the mower would have marked it maintained and it would have gone
quiet in the morning briefing. No live asset carries `interval_days` yet, so the fix is ahead of the
first one that will. Now only `_SERVICE_KINDS` (`chore`, `service`) restart the clock, as an allowlist, so
a future NFC use-tap fails safe by leaving the asset visibly due. Regression test:
`test_only_service_events_reset_the_clock`.

**Usage may add urgency, never remove it.** Taps undercount, because a forgotten tap is a use
that never gets recorded and there is no correcting signal. So a use threshold can only ever be
a floor, ORed with the existing calendar ceiling: `interval_uses: 20` OR `interval_days: 56`,
whichever trips first, and the briefing says which one did.

**Where this would be built:** `due_assets()` plus `briefing.py` plus a tap endpoint. It is a
counter and a threshold, which is a `COUNT(*)` since the last reset event, so it is records and
timers work and none of it belongs to the eyes service or the model. It is written down here
only because NFC is the shared front door. The hour-meter read below is the sole piece of it
that is actually vision.

### The intended flow

1. Tap an NFC tag (or open the capture screen) to start a timestamped session with the right
   place, asset, or litter context already filled in. Take one or several photos: produce on
   the scale, a tray/colander, labels, a newborn puppy, or the equipment itself. A shortcut can
   add a short optional note or voice transcript, but must not require a typed subject for each
   item.
2. Eyes turns the session into structured rows and **writes them**, each flagged `unverified`
   with its source photo, confidence, and the tier that produced it. Harvest is the first
   template, for example:

   ```text
   Harvest batch, Aug 21   (unverified)
   - Tomatoes — Bed 4 — 3 lb
   - Cucumbers — Bed 2 — 1 lb
   - Beets — Beets Round Bed — 18 count
   ```

   A whelping template instead writes a newborn row with litter/parent context, sex/name if
   supplied, birth weight, and first photo. The rows call the existing
   `records_store.log_harvest()` path (or the birth/photo paths), so there is no parallel store
   and the almanac sees them immediately. The photo event and the domain event share a batch ID,
   so the human evidence stays attached to the structured record.
3. **The next morning's briefing carries the unverified rows**, because it is a habit that
   already exists and is already read at 7:10. Correcting one is a reply: "those tomatoes were
   Bed 1, not Bed 4", "make that 3.2". A row nobody corrects simply stays, and clears its
   `unverified` flag after a set time so the briefing does not accumulate.
4. A row that cannot be filled stays visibly incomplete rather than being invented. An
   unreadable weight writes the harvest with a null quantity and says so in the briefing, which
   is a prompt to fix a real row rather than a proposal waiting to become one.

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
imply three pounds. If the display is glared out, the row is written with the weight
blank and the photo attached, and the briefing asks for the number, which is already the rule in "Uncertainty is useful" and now
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
- **A connected scale beats all of this.** Where the hardware can hand over the number
  directly (Bluetooth/WiFi), take the number and skip the read entirely. See "Where this
  lane lands" below.
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
  all season. A read far outside that range is written through but called out as "unusual for
  this crop" in the briefing, which is the one place a human is already reading. It never
  rewrites the number.

#### Why capture commits, and correction happens later

**Decided 2026-08-24, and it reverses the earlier design in this document.** The first version of
this lane ended in a batch review: nothing entered records until a human approved the rows. That
is the right shape for an operator with reliable admin discipline. It is the wrong shape for this
household, and there was already evidence in the house saying so. The note-taker's review inbox,
the same propose-then-promote mechanism, had been running since mid-June and stood at **29 pending
proposals against 4 promoted, the oldest 71 days old**. A commit gate that depends on coming back
later is a gate that stays shut.

So the posture for field capture inverts, on one asymmetry:

> **A missing entry costs more than a wrong one.** A weight recorded as 32 lb instead of 3.2 lb is
> visible, editable, and recoverable at any time. A harvest that was never logged never existed,
> and no year-over-year comparison can reconstruct it. The failure mode being designed against is
> omission, not error.

**This does not violate design invariant #4** ("the note-taker proposes, it does not write"), and
the line it draws is worth stating precisely, because it is the same line the records substrate
already draws:

- **Events are cheap and correctable, so capture writes them.** A tap is not a model proposal at
  all; the human resolved the meaning by choosing which tag to touch. A photographed harvest row
  is a deliberate physical act with a model filling in two fields.
- **Entities are expensive to retrofit, so minting still gates.** A junk or misspelled entity
  pollutes the roster permanently and quietly corrupts every rollup keyed on it. The ⚠️new-entity
  path keeps its confirm, and an out-of-roster ID still asks before it mints. Invariant #4 protects
  exactly this, and it is untouched.
- **Durable memory still proposes.** Nothing here changes the note-taker or `review_notes.py`.

**Calibration comes free from corrections.** The earlier design asked for the harvest to be logged
by hand *and* Eyes' read reviewed against it, which is double logging at the moment the habit is
most fragile. Delete it. Every correction made in the briefing is already a labelled OCR failure,
tagged with the surface it came from, captured passively at the moment the human was going to look
anyway. `data/eyes-ocr-calibration.jsonl` gets the same rows it would have got: raw read, corrected
truth, confidence, surface, engine. Uncorrected reads count as agreements after their flag clears.
The accuracy record per surface is identical and costs nobody a step.

**What this buys, stated plainly:** capture and commit become one action, which is why the NFC tap
is the model for the whole lane and not just its front door. There is no queue that can silently
stop being serviced, and with two people sharing the system there is no review step for each to
assume the other did.

**The one real cost** is that wrong rows exist in records for a while, and some are never
corrected. That is accepted deliberately: an uncorrected 3 lb is a season total slightly off, while
an unlogged harvest is a hole in the record that YoY comparisons cannot see around. Corrections
remain possible forever, and the `unverified` flag plus the stored display crop means an audit
later can always tell which numbers a model produced.

### Where this lane lands

The lane is worth more than harvest logging, and most of its landing sites already exist as
records paths. What is missing in each case is only the capture step.

- **Harvest** (first template): classifier picks the crop from the bed's plantings, OCR reads
  the scale, `log_harvest()` files it. Feeds the almanac's Harvest section and the YoY deltas.
- **Whelping**: a newborn batch inheriting dam/sire/litter context from the whelping-area tag,
  proposing birth weight and first photo per pup.
- **Wildlife into the almanac** (the long-discussed piece, and the shortest path of the lot):
  send a photo, the classifier proposes a species against the records roster, confirming it
  logs a `sighting` event exactly as the wildlife skill's voice path already does. The almanac
  already keys its wildlife section off those events (first sighting this season, ×N total,
  `almanac.py:73`), so a confirmed photo ID lands in the season page with no new plumbing.
  The gain over voice logging is not speed, it is the species you cannot name out loud, plus
  a photo attached as evidence to a first-of-season date that YoY comparisons will lean on.
- **Maintenance meters**: photograph a mower or generator hour meter and OCR the reading, so a
  usage threshold runs on real hours instead of tap count. See the accumulator/reset tags above.
- **Inventory and labels**: seed packets, feed bags, part numbers, expiry dates. Ordinary
  printed-text OCR, the easy half of the tier.

**Connected hardware beats reading a display.** A Bluetooth or WiFi scale would hand over an
exact number with no read to verify and no calibration lane to run, and where that hardware
exists it should be preferred outright: prefer a number to a picture of a number. That keeps
OCR aimed at the surfaces that will never be connected, which is most of them. The mower's
hour meter, the seed packet, the old kitchen scale, and the neighbour's borrowed tool are not
getting firmware. Worth checking which scales in the house already talk before investing in
the seven-segment read. (Noted 2026-08-24, not chased yet.)

### Design rules

- **Capture commits, the briefing corrects:** rows are written at capture time flagged
  `unverified`, and the next morning's briefing is where they get fixed. Never build a queue that
  has to be visited, and never make the user approve or wake the assistant once per crop, puppy,
  or maintenance action. See "Why capture commits" above for the evidence behind this.
- **Template-specific records stay canonical:** harvest proposals use the existing `harvest`
  schema (bed, crop, quantity, unit, timestamp); whelping proposals use the existing
  birth/photo/lineage paths. The shared capture session is temporary—there is no parallel
  harvest or breeding store.
- **Uncertainty is useful:** identify likely crops/beds from the roster and images, but leave a
  missing/unclear weight visibly unresolved instead of fabricating precision.
- **Trust is measured per surface, not waited for:** every correction in the briefing is a
  labelled failure for that surface, so the accuracy record builds itself from work already being
  done. A surface that reads badly shows up as a correction rate, which is a number to act on
  rather than a reason to withhold the feature.
- **Proof, not surveillance:** this is an intentional capture flow from a phone, not a garden
  camera pipeline. Keep the image path and batch provenance auditable and retain no hidden
  background analysis.

This is the best first user experience for Eyes: NFC identifies the thing in front of you, a
photo/scale/short note is the low-friction capture, the record exists from the moment it is
captured, and the morning briefing is where the household corrects what the machine got wrong.

## Phase 3 — voice hook

"Hestia, what bird was that?" after a photo lands: the almanac-skill pattern (deterministic
router) injects the latest eyes proposal into the prompt. Cheap, no new model work.

## Failure modes

| Failure | Mitigation |
|---|---|
| Classifier confidently wrong (lookalike species) | roster constraint limits damage to plausible-local mistakes; the row is written `unverified` and surfaced in the next briefing, where a correction is a reply; confidence stored in the event attrs for later audit |
| Out-of-roster true positive (genuinely new species) | that's a feature — ⚠️new-entity proposal with the species guess pre-filled |
| VL judge hallucinates on escalation | temp 0, "say 'uncertain' if unsure" prompt, propose-only; judge output never auto-files |
| VL load OOMs against a TTS burst | single-flight + keep_alive 0 (seconds-long window); eyes Ollama dies → Restart=on-failure; voice tenants unaffected (already resident) |
| Eyes service down | ingest endpoint degrades to today's manual-subject flow — identify mode returns "eyes offline, name it yourself" |
| Vulkan regression binds eyes Ollama to the 5080 | env belt above + post-start nvidia-smi assertion before the unit reports ready |
| OCR misreads the decimal (3.2 lb read as 32 lb) | low-confidence decimal is flagged ambiguous with both candidates shown, never rounded; range check against `harvest_totals()` marks it unusual; the display crop rides along so review is a glance |
| Model invents a weight the display never showed | quantity may only come from pixels of a display or label; the tier returns `unreadable` rather than an estimate, and the row is written with a null quantity that the briefing asks about |
| Eyes row duplicates a harvest the human logged by voice | capture writes once and the human is not asked to log it twice, which removes most of the overlap; a same-day (bed, crop) collision is flagged in the briefing as a possible double rather than silently merged |
| OCR trusted on a surface it was never good at | calibration is per surface, not global, and is built from briefing corrections; a surface whose correction rate stays high gets its reads written blank-with-a-prompt instead of filled |

## Parked: Frigate / live cameras

The camera half (from the Kimi analysis) is **gated on real cameras existing** — the Arlos'
detection is too poor to feed anything. When that changes: Frigate runs on hl-relay
(OpenVINO on the iGPU, substream ~5 fps, motion masks), **never** on the Ti — detection is a
constant load and belongs next to HA/MQTT/recordings; the Ti's headroom is for the burst
tier. Events would enter this same lane at step 4 (classifier first, VL escalation), with
rate limits (per-camera cooldown, bounded queue, drop-with-log). Until then: note only,
don't build — the photo-ID lane is the whole product.
