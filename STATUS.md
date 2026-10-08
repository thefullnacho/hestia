# STATUS

Running log of where Hestia actually is. Newest entry at the top. Append, do not rewrite.

Host addresses, router details and LAN topology stay out of this file on purpose. This repo
is public. Those live in the operator's private notes.

---

## 2026-10-07 - the operator's chat becomes a window onto the brain

The friction was never that the brain is local, it was that real work kept moving to the operator's
Claude Code chat and happening there by hand: one-off scripts against the records, reminders
inserted directly, none of it through the code paths the brain uses. This puts that chat on the same
footing as the phone and the kitchen mic: another client of one tool layer.

- **`brain/mcp_server.py`.** A stdio MCP server whose tool list is built from `tools.SCHEMAS` and
  whose calls go through `tools.dispatch`, so the chat gets the brain's argument validation and its
  side effects (a tie logged from the chat files its follow-up reminders). Hand-rolled JSON-RPC, no
  new dependency. No listener, so the network boundary is unchanged.
- **Read-only by default.** Each tool's action enum is narrowed to the actions that only read, and
  the client is shown that. `HESTIA_MCP_WRITES=1` opens the rest; every call that changes anything
  is appended to an audit log first (tool and arguments, never results) and is refused if the log
  cannot be written. The client's own tool approvals still stand in front of every call.
- **Default deny.** Only the tools in its `READ_ONLY` table are offered. `search` is left out, there
  is no shell, and a test fails when a tool is added to the brain without being classified.
- 11 new tests, each safety property broken on purpose to confirm a test catches it; the full suite
  passes. Smoke-tested over a real stdio pipe against the live records, read-only: it listed the
  pending reminders and a dam's record, and a write attempt was refused with nothing written.
- Not yet done: registering it with Claude Code, which is the operator's call, and trying write mode
  against anything but a scratch database. Verified with a scripted client, not Claude Code's own.

---

## 2026-10-07 - a tie files its own pregnancy check and whelp-watch

A dam's tie was logged a week late and no reminder followed. The day-28 pregnancy check and the
day-56 whelp-watch for the previous litter had only existed because someone asked for them in
conversation. Nothing in the code or the whelping skill created them, so a tie logged any other
way got none. That is scheduling left to the model remembering, which is the thing this project
does not do.

- **`brain/breeding_followups.py`.** Reads the tie events on the books (a pet, not the sire; the
  verbs tied, tie, bred, mated; a second tie within 10 days is the same breeding) and files the
  day-28 and day-56 reminders as ordinary rows, spoken in the kitchen as well as pushed. It is
  idempotent, never files a reminder whose time has passed, and skips a milestone someone already
  set by hand for the same dam and day. `python breeding_followups.py --dry-run` shows what it
  would do.
- **Two triggers, one function.** The `records` tool calls it the moment a tie is logged, and its
  reply says what was set. The twice-daily puppy watch calls it as a backstop for a tie that
  never went through the tool (a retroactive entry, a script), whether or not a litter is young.
  Neither can fail the log or the pup alerts. The puppy-watch timer unit is unchanged.
- **The whelping skill** now says how to log a tie (kind breeding, did tied, `ts` = the real tie
  date), that the reminders are filed by code and must not be made by hand, and to cancel them if
  the pregnancy check is negative. A stale line saying a dam's first pairing was only planned is
  corrected.
- 13 new tests, with the three failure modes that matter broken on purpose to confirm they are
  caught; the full suite is 591 passing. The current breeding's two reminders were filed against
  the live database after a dry run showed exactly those two.
- Merged into the checkout the brain and the puppy watch run from on 2026-10-07. The puppy watch
  picks it up at its next run. The brain was stopped on purpose, so it picks it up when it is
  next started. The filed reminders fire regardless, from the reminders timer.

---

## 2026-10-06 - the board pages, and a row can be waved past without closing it

The board showed five rows a column and then a dead "+16 more" and "+23 more". The queue could
only be worked from its oldest end, and the oldest rows were the reminders that were never
getting done this week, so the front page stayed stuck on them.

- **Paging** (`brain/board.py`, `brain/board_push.py`). A column that does not fit ends in a
  bordered "1/5 · next >" button. Tapping it turns that column to its next page, oldest first,
  and the last page turns back to the first. Columns turn independently, and HOME and MEMORY
  use the same code. Pages snap back to the front `BOARD_PAGE_S` (90 s) after the last tap, so
  an unattended board always shows page one. Swiping is not built: the touch parser reports
  taps only, and a gesture is not something to ship untested on a panel I cannot hold.
- **Later.** Selecting an ordinary queue row now shows a Later button beside the two-tap done.
  Later keeps the row open and serves it after every row that has not been waved past, for
  `BOARD_LATER_DAYS` (7), then it is back in age order. Keep on a "still real?" row now does
  the same for the 14 days it already snoozed the question, so a reminder that stays real stops
  holding the front page. Both are dates in a state file (`board_later.json`, keyed on the row's
  stable id). The queue file is not written and its layout is unchanged; only a confirmed Done
  or Trash edits it. `POST /queue/{id}/later` does the same through the token-gated API, and
  `/queue.json` reports `status: "later"`.
- Not changed: Done, Trash, and the phone page, which still has only its Done button.
- 9 new tests; the full suite is 587 passing. Checked against a copy of the real queue (49 open
  rows): every page rendered, and the real touch flow run with only the SSH send faked. Page
  turns wrapped 5/5 to 1/5, Keep and Later moved rows to the back, and the queue file was
  byte-identical afterwards.
- Live from 06:45 on 2026-10-06: merged into the checkout the board runs from, the suite
  passing there (587), `hestia-board-push` restarted. The first frame drew clean, the journal
  showed no errors in the first minutes, and a render of the live board (real queue, real home
  data) pages all three columns. Not yet exercised: the taps themselves on the physical panel.

---

## 2026-10-06 - extra-directory backup confirmed on the nightly run

The 02:02 timer run shipped the extra directory without being started by hand: the journal line
reads "backup ok ... extras: <name>". That closes the first "Not verified" item from the 2026-10-05
entry. Still not verified: the off-site pull carrying `extra/` into restic.

**Next concrete action:** check that the latest off-site snapshot lists `extra/`, then merge
`feat/backup-extra-dirs` into main.

---

## 2026-10-05 - nightly backup can ship operator-listed extra directories

`HESTIA_BACKUP_EXTRA_DIRS` (off by default) makes the nightly run tar each listed directory into
`extra/` beside the DB and memories, working tree and `.git` included. Each directory is capped at
50 MB (`HESTIA_BACKUP_EXTRA_MAX_MB`).

- **A bad entry never costs the DB its night.** A missing directory, one over the cap, a name
  collision or a tar failure is collected, the good parts still ship, and the run then exits
  non-zero so the failure alert pages the phone. Paths are read into an array, so a glob character
  in the setting is treated as text.
- **Verified 2026-10-05:** the staging block was tested in isolation on six cases (normal, missing,
  over the cap, name collision, empty, glob character). Then the service ran once with one directory
  set through a local systemd drop-in, the tarball was pulled back from the backup host and
  unpacked, and the working tree and git history matched the original.
- **Not verified:** tonight's 02:03 timer run, and the off-site pull carrying `extra/` into restic.
- **Where the code is:** branch `feat/backup-extra-dirs`, pushed, not merged to main. This checkout
  carries it through a local merge so the nightly run uses it. Restore steps are in
  `deploy/backup/OFFSITE-RUNBOOK.md`.

**Next concrete action:** read tomorrow's backup journal for the `extras:` note and check the
off-site snapshot lists `extra/`, then merge `feat/backup-extra-dirs` into main.

[non-production] Say go on merging `feat/backup-extra-dirs` into main.

---

## 2026-10-03 - greenhouse door board, bench build done, phase two waits on parts

Both sensors read on one board, the board is in HA, and the alert fires. Branch
`greenhouse-door`, still not merged.

- **DS18B20 found** 2026-10-02 12:23, after three breadboard faults: the probe's adapter board had
  all three pins in one row, the jumpers went into its screw terminal beside the probe wires
  instead of into the pin rows, and the dev board covered every hole on one side, so jumpers
  "beside" its pins never reached them. Female jumpers straight onto the dev board's pins fixed
  the last one. Reads about 72 °F indoors, every 10 s.
- **In HA** 2026-10-03, with a DHCP reservation. HA prefixes each entity with the device name, as
  with kennel-box, so the door is `binary_sensor.greenhouse_door_greenhouse_door`. The README's
  alert watched `binary_sensor.greenhouse_door`, which does not exist; fixed before it was pasted.
- **Alert on the bench:** door opened 09:18:01, the automation fired 09:18:06.3. That is 5.3 s
  from open to alert, 5 s of it the hold. HA sent the push; that it reached the phone is not
  confirmed.
- **Rescanning the probe** needs a restart, because the 1-wire bus is only scanned at boot. A
  serial EN pulse restarts the board cleanly. `esptool chip-id --after hard-reset` failed once
  partway and left the board in the bootloader until the next pulse.

**In flight:** waiting on weatherproofing parts in the mail. Phase two, mounting, starts when
they land.

**Next concrete action:** when the parts arrive, give the reed leads a real connection,
weatherproof the joints, then mount on the door frame, latch side, per the README's Mounting
section.

[non-production] Waiting: the weatherproofing parts in the mail. Then, morning, hands: measure
the reed release distance, connect and weatherproof the leads, mount it, photos (door and magnet
alignment, wiring, the alert). Confirm the bench push reached the phone. Waiting after that: the
first cold night with the heater on, for the 10-minute door test.

---

## 2026-10-01 - greenhouse door board, bench test passes for the reed switch

The board is flashed and on Wi-Fi, and the door switch reads true on the breadboard. Branch
`greenhouse-door`, still not merged.

- **Flash:** the first USB upload failed, first with no bootloader handshake and then with the
  port busy, most likely ModemManager probing the freshly plugged-in CP2102. A retry seconds
  later went through without holding BOOT. The README now says to retry.
- **Reed switch on GPIO27:** OFF with the magnet on (door shut), ON apart, so no `inverted:`.
  Holds were clean while the magnet was still. It chattered only when the magnet moved slowly
  through the switching point, which the alert's 5-second hold absorbs.
- **Switching distance:** closes at about 1¼ inches, side by side. The release distance is not
  measured yet, and the 2-inch door test depends on it. The mounting rules that follow from it
  are in the README: latch side, shut gap under ½ inch, same alignment as the bench.
- **Wi-Fi on the bench:** -42 to -54 dB.
- **DS18B20 not wired yet.** The log says "Found no devices", as expected.
- The reed leads are too thin to grip a breadboard. They were wrapped round jumper pins for the
  test; the mount needs a real connection.

**Next concrete action:** wire the DS18B20 on GPIO4 and confirm the boot log finds it. Then HA,
the DHCP reservation and the alert automation, tested with the magnet before anything goes on
the door.

[non-production] Morning, bench: measure the reed release distance, tin or crimp the reed leads,
wire the DS18B20. 12:00 or 15:00: add it to HA, the DHCP reservation, paste and test the alert.
Then mount it on the latch side and take the photos. Waiting: the first cold night with the
heater on, for the 10-minute door test.

---

## 2026-09-30 - greenhouse door board, test 1 of the Homesteader Labs build loop

The greenhouse temperature probe is too slow to warn before the heater loses, so the door gets a
reed switch. Branch `greenhouse-door` (271b1e1), pushed, not merged.

- **Board** (`deploy/esphome/greenhouse-door.yaml`): an Elegoo ESP32 dev board with the reed
  switch and a DS18B20 on one board, so both readings share a clock. That gives the build log its
  number: seconds for the switch to notice an open door, against minutes for the temperature.
  Validated with `esphome config` on the 2025.12.7 pin; keys generated into the ignored
  `secrets.yaml`.
- **No alert logic on the board**, same as kennel-box. The alert is an HA automation in the
  README, because it has to fire within seconds and the brain watchers run on a schedule.
- **The working tree is on `greenhouse-door`** so the morning flash finds the file. Switching back
  to main before the merge hides it.

**Overlap to decide:** the build list includes a Pi Zero with a 2.13" e-paper "state of the day"
display, and the Kindle board (2026-09-26) already shows the queue and the home's state on e-ink.

**Next concrete action:** after the test night, pull the door and temperature history from HA
within 10 days (the recorder purges raw states at 10) and compute the number.

[non-production] Morning: flash, wire and mount the board, add it to HA, paste the alert
automation. First cold night with the heater on: the 10-minute door test.

---

## 2026-09-26 - the board: the operator's to-dos and the home's state on a Kindle by the door

The queue of jobs that need the operator had grown to 71 open rows, 54 of them over a week
old, and none of it was visible without asking for it. The board makes it ambient: one e-ink
page, always on, that shows what needs a person, split by the kind of attention it takes.

- **Renderer** (`brain/board.py`). Four columns: HANDS and SCREEN come from the operator's
  queue file (read through `BOARD_QUEUE`, default a symlink under the ignored `data/`), split
  by the slot the row names, with physical keywords as the fallback. HOME is Hestia's own:
  box watch, puppy watch, garden watch, due assets and reminders, each source failing alone.
  MEMORY is the note-taker inbox and appears only while it holds proposals. Rows with a Done
  date and rows that say when they start ("on or after", "Late November") stay off. A small
  character carries the mood of the most urgent Home item; the art is a placeholder. No model
  anywhere, the same rule as every watcher. `GET /board.png` serves it on the tailnet.
- **The Kindle** is a jailbroken Paperwhite 1 on FW 5.6.1.1, 758x1024 at 212 DPI. USBNetwork
  went on through the MR package installer after "Update Your Kindle" stayed greyed out, with
  SSH over WiFi on its own passwordless key: the operator's personal key sits behind the
  desktop keyring's passphrase prompt, which a service can never answer.
- **Push and taps** (`brain/board_push.py`, `hestia-board-push.service`). The brain binds to
  the tailnet and the Kindle is on the LAN, so the Kindle never calls in: this box holds an SSH
  stream of the touch panel and sends each frame down the same multiplexed connection, drawn
  with FBInk. Each push stops the stock Kindle UI (its status bar drew over the board) and
  holds off the screensaver; a reboot restores both and the next push reclaims the panel.
  Redraw every 5 minutes, partial refresh, one clean flash per hour. Taps are mapped from
  three calibration taps on the real panel. One tap selects and says what a second will do;
  a second tap within a minute moves a queue row to Shipped or logs a service on a due asset.
  Watch alerts only explain themselves. Memory proposals get Keep and Discard buttons.
- Used for real on the first night: two finished rows closed from the board, and the whole
  memory inbox (13 proposals) answered, which collapsed the column.
- 20 new tests; the full suite is 571 passing.
- Carried from 2026-09-24, never written up: the box watch's week-one band widened to 77-90F
  to match where the Govee actually sits, and the litter (seven pups, 2026-09-23) arms both
  kennel watchers.

In flight: nothing half-built. The service runs and reconnects on its own.

Next concrete action: let a queue row name the record that finishes it, so an NFC scan that
logs the job also closes the row. The catch-cup check was logged at the stake and then had
to be closed again by hand on the board.

- `[non-production]` Confirm the queue triage (pre-sort in the operator's notes).
- `[non-production]` DHCP reservation for the Kindle on the router.
- `[non-production]` KUAL, USBNetwork, enable at boot, now that key login is proven.
- `[non-production]` Give the Kindle a spot on USB power: the screensaver is held off, so
  battery alone will not last.
- `[non-production]` Pick a direction for the board's character, to replace the placeholder.

---

## 2026-09-21 - the whelping box gets a thermometer that answers to the litter's age

Lily is inside her window (due 2026-09-25) and the box is built: pad on its own thermostat at
85 under two thirds of the floor, the heat lamp hung outside the box, the last third unheated
so the pups and Lily can move off the heat. Until today the only record of what the box
should read was memory from the last litter.

- **Kennel box** (`deploy/esphome/`). A Heltec WiFi LoRa 32 V3 by the box listens for the
  Govee H5075 inside it over Bluetooth and decodes the broadcasts itself, so its OLED shows
  the box even when HA is down and no Govee app or cloud is involved. The decode was checked
  against a live packet before it was written. Ten minutes of silence blanks the numbers
  instead of leaving a stale one standing.
- **Version skew, found the hard way.** Built on ESPHome 2026.9, HA 2025.12 registered one
  entity out of four: the newer firmware no longer sends the `object_id` that HA 2025.12
  builds unique IDs from, so every sensor arrived as `<mac>-sensor-` and HA kept the first.
  ESPHome is now pinned to 2025.12.7 and the README says to move the pin with HA. All four
  sensors are in HA with real IDs.
- **Box watch** (`brain/box_watch.py`, `hestia-box-watch.timer`, every 2 min). Reads the box
  temperature from HA and the litter's age from its whelp date in records, and compares them
  to the week's band: 82-93F week one, 75-88 weeks two and three, 68-83 weeks four and five.
  Too hot or too cold for five minutes pushes and repeats every half hour, a silent sensor
  pushes on sight, and recovery is announced once. The plan was to copy the whelp date into an
  HA helper, and that was dropped: the copy would have synced twice a day, which leaves the
  first hours after a whelp on the wrong answer. It is silent between litters and does not
  touch HA when there is no litter. 10 tests; the full suite passes; a live read of the real
  sensor parses (72.3F, which is correctly "cold" for a day-0 box with the lamp off); a test
  push reached the phone.
- **Whelping notes** now carry the box temperatures as house practice, week one from the last
  litter and later weeks marked as general guidance until this litter confirms them. The
  knowledge file is injected on every whelping turn, so the addition was checked against the
  prompt budget; it still clears with about 9KB to spare.

In flight: nothing half-built. The watch arms itself when the first pup is logged.

Next concrete action: none for the build. When Lily whelps, log the first pup on `/whelp`
right away, because that row is what arms the box watch.

- `[non-production]` When Lily whelps, log the first pup on `/whelp` straight away.
- `[non-production]` Replace the heat lamp clamp with a chain from a joist hook or a weighted
  stand, plus a separate safety cable.
- Open, not started: switching the lamp off automatically needs a locally controlled plug.
  Govee plugs are cloud-only, so that is a purchase decision first.

---

## 2026-09-19 - the kennel gets a watcher, a board, and a number that cannot hide in prose

A question that should have been a lookup came back as an error. "When did Lily's pregnancy
start" assembled to 31718 bytes against a 31488-byte ceiling and answered with the
shorten-your-request message instead of the due date, while a longer phrasing of the same
question worked. The cause was not memory or records: `whelping` was the only skill without a
`tools:` allow-list, so every breeding question shipped all eleven tool schemas, 16KB, over
half the budget. Longer phrasings only passed because a word like "record" happened to trip the
records intent regex and scope it down. Scoping the skill took the same question to 21429
bytes. Another phrasing was passing at 30550, about 900 bytes from the same failure, so this
was not one broken question but a whole domain running with no headroom.

Pregnancy is confirmed in the records now, and confirmed as what it actually is: the operator
never took her to a vet, he could see it and can now feel the puppies. Logged as a health event
with `vet_confirmed: false`, because a record that quietly implies a vet saw her is worse than
no record.

`puppy_watch.py` is the third deterministic watcher, after garden and pest. It runs 09:00 and
20:00 while a litter is under three weeks old and pushes only when something needs a person:
lost weight, no gain in two days, still under birth weight after day 3, down 10% from the pup's
own peak, a pup never weighed, or a litter on the books with no pups logged against it. Silent
otherwise, which is what let it be installed and proven a week before the whelp instead of
written the night of.

The watcher needed a number to read, and that was the real missing half. Pup weights were
routed to a free-text health event, which a person can read and no watcher can. `records` gains
a `weigh` action storing grams the way `harvest` already does; older free-text weights are
parsed as a fallback and the birth weight is day 0, so an existing litter is not invisible to
the curve. Two bugs surfaced in the wiring, both of which would have cost weights on the night
it mattered: a birth written with a microsecond timestamp string-sorted after a same-day
weighing, and the watcher looked for the birth row in the day-collapsed series, so a pup weighed
on the day it was born silently lost its day-0 baseline.

`/whelp` is a capture board, and it exists for the same reason `nfc.py` does. At 3am every name
in this house is one the model has never heard, which is exactly the condition that produced a
confident "logged" over an empty table on 2026-09-01. The litter is a row of coloured collar
chips with a weight box on each, plus a birth form that takes a collar colour, a birth weight
and a sex. The colour is the pup's name until someone picks a real one. Identification is the
collar, not a tag: an NFC disc on a six-ounce neonate was considered and rejected, since the
collar already disambiguates and a 25mm tag is a quarter of the circumference of that neck.

Also fixed from the field: the weekly photo button did nothing when tapped on a phone. A styled
`input[type=file]` on iOS draws only a small native control inside whatever box the CSS makes,
so a full-width dark box is mostly dead pixels. The label is the tap target now, `required` is
gone from a clipped input where a validation error cannot be focused or shown, and there is a
library fallback that drops `capture`, which is the attribute Safari silently ignores when
camera permission for the site has been denied.

A weedeater took the top off one stake. Everything from the tag down survived and still scans,
because the tag sits in a recess under a cap rather than on the surface. That pocket is there to
keep water off an aluminium antenna and it turned out to also survive a trimmer. A surface
sticker would have died silently, and a tag that no longer reads looks exactly like a stake
nobody has walked out to yet.

The VPN kill-switch container had been reporting unhealthy for 93 days and nobody had read it
as anything but a stale healthcheck. It was not. Gluetun's DNS-over-TLS to Cloudflare was being
reset by the Proton exit, and the queries that failed were tracker announces, so qBittorrent
was intermittently unable to resolve its trackers. `DOT=off` now puts DNS in plaintext inside
the tunnel, which is the same privacy posture with one fewer handshake to be refused.

The worse problem was underneath. The healthcheck dialled `cloudflare.com:443` and
`github.com:443` by name, so a DNS hiccup read as a dead tunnel, and qBittorrent has
`depends_on: condition: service_healthy`. Gluetun had never once reported healthy, so **any
reboot of the relay would have left qBittorrent permanently down**, which the first `compose up`
demonstrated by refusing to start it. The healthcheck now dials raw IPs. Failing streak went
from 1,611,627 to 0, and the kill-switch verified intact on both sides of the change.

This is `stale_sensors` again, in the rack rather than the garden: a reading that is confidently
wrong is worse than one that is missing, and a status that has said the same thing for three
months is not a status. The tunnel was fine the whole time, which is exactly why nobody looked.

### In flight

- Lily is day 57. The window is 2026-09-20 to 09-30, due around the 25th. Twice-daily temps
  are the operator's, the watcher handles the weights once pups exist.
- Grant funding is closed as a channel. NLnet declined after roughly seven months, FUTO never
  replied in 78 days on a no-deadline track whose stated process is "email us and we guide
  you", and the hackathon did not place. The canonical ledger records all three.
- The commercial direction is an on-premises property ledger sold through custom-install
  integrators, not a voice assistant and not a control system. It does not actuate, which keeps
  liability near zero and keeps it out of the driver business. Two positioning corrections
  landed: Nines already ships a self-hosted container, so "nobody is on-prem" is wrong, though
  their own docs say the self-hosted build loses the cloud-dependent features. And OvrC is
  owned by Control4's parent, so device-health monitoring is already bundled for those dealers.
  The defensible line is narrower and true: device-up monitoring is not semantic-truth
  monitoring. OvrC says the device is online. It cannot say the device is online and lying.
- AGPL and the commercial fork are unresolved. Public auditability is claimed as a
  differentiator, and a closed fork would remove it. Unblocking that is cheap only until the
  first outside pull request is merged.
- Three things found in the relay's compose file and deliberately not touched at the time: a
  plaintext WireGuard private key inline rather than in an env file, two literal `/path/to/...`
  placeholder volume paths that now hold three months of real qBittorrent config, and the fact
  that gluetun's healthcheck is now a single point of failure for qBittorrent ever starting.
  None are urgent, all are mine to do, none belong in the operator's queue.
- Multilingual is understood and deliberately not scheduled. Whisper and the resident model
  already handle other languages; the harness does not. Skill routing matches whole English
  trigger words, so a non-English request scopes to nothing and the model sees every tool at
  once. It is a trigger vocabulary per language plus a voice per language, not a translation
  pass.

### Next concrete action

Add a contribution policy or CLA to the repo. It is an hour, and it is the only item on this
list whose cost rises permanently the moment someone else's code is merged, which matters now
that the forum post is drawing stars.

Then, in order: the Peach stake reprint, since it stands in mown grass because that guild has
no bed cut yet `[non-production]`; four emails to shoreline integrators on a Tuesday or
Wednesday morning, asking about their gaps rather than pitching `[non-production]`; and a
one-page call sheet to go with them. The camera and ledger work waits on what those
conversations say. Nothing about the property ledger gets built before an integrator has said
out loud what breaks in their week.

### Non-production, queued

- `[non-production]` Reprint the Peach stake post and move the tag across, then re-site it out
  of the trimmer path. Properly means cutting the peach bed.
- `[non-production]` Bookmark `/whelp` to the phone home screen, then delete the handoff file,
  which carries the live token.
- `[non-production]` Retest the weekly photo button at a stake, and report whether the camera
  button worked or whether the library fallback was needed. Those are different diagnoses.
- `[non-production]` Reply in the HA forum thread to the multilingual question.
- `[non-production]` Four emails to Tier 1 shoreline integrators, Tuesday or Wednesday morning.
- `[non-production]` One integrator call or showroom visit. This is the n=1 on the whole
  commercial direction.

## 2026-09-17 - the soil sensors get a check that notices when they stop telling the truth

A blog-post data pull went looking for a pre-drip contrast week and found six days of frozen
readings instead. June 2 to 7, every bed reported exactly one value per day, unchanging, with
Artichoke and Potatoes sitting at a literal 0.0% through an 81-87F dry stretch. Home Assistant
had carried the last value forward while the Ecowitt gateway was down, and the result looked
exactly like healthy, steady soil. The chart would have been a false claim.

The same pull showed the Hot Peppers sensor stopped on 2026-08-15 and had been dead for a
month. That one was invisible for a different reason: `soil_beds()` skips anything it cannot
parse, so the count quietly went from six to five and the briefing kept saying every bed was
fine. Two failure modes, both silent, both shaped like good news.

So `garden_watch.stale_sensors()` now runs first, ahead of every other garden alert, because
whether to water is only as good as the readings it was decided from. It flags four things: a
sensor not reporting, a sensor reading 0% (a fault, never dry soil), a sensor unchanged for
`SOIL_FLAT_HOURS`, and a battery at or under `SOIL_BATT_LOW`. Battery entities carry no bed in
their names, so the channel number is what ties a voltage to a bed.

All sensors flat at once is reported as one gateway fault rather than six probe faults, and
that line says explicitly that nothing should act on carried-forward readings. That wording is
deliberate: the plan is to let these numbers drive B-hyve zone 4, and a frozen value is worse
than a missing one because it is confidently wrong. A history read that fails is never treated
as evidence a value did not move.

The briefing also stops saying "all 5 beds fine" when there are six. It says reporting beds.

Live on the estate the moment it shipped: Hot Peppers not reporting, Tomatoes battery at 1.3V.
Both will ride tomorrow's 07:10 push.

### In flight

- Rain readings are still write-only. `rain_totals` exists; the almanac, the journal and
  `snapshot()` do not read it yet. Unchanged from yesterday and still the next build.
- Staleness is reported but does not yet gate anything, because nothing automated acts on
  moisture yet. When zone 4 is driven by these readings, the same check has to sit in front of
  the valve, not just in the morning push.
- Watering run history still lives only 10 days. B-hyve publishes the last run per zone as a
  timestamp entity with no long-term statistics, so every run older than the recorder purge is
  gone. The NFC stakes are the fix, and the tag run is not finished.

### Next concrete action

Unchanged: surface rain in the almanac and the nightly journal. Then the zone-4 gate, which is
the reason the staleness check was built rather than a nicety.

### Non-production, queued

- `[non-production]` Hot Peppers sensor (channel 7), dead since 2026-08-15. Likely battery.
  Tomatoes (channel 6) at 1.3V, worth doing on the same trip.
- `[non-production]` Decide whether to raise recorder retention for the six soil entities
  only, so next season's raw data outlives the 10-day purge. HA config change, not taken.

---

## 2026-09-16 - the stakes get into the ground, and the cup earns its place as an instrument

The catch cup's internal graduation rings were printing as spaghetti across the bore and out
onto the outside wall. They are gone. The inside is a plain cylinder now and the depth is read
by plunging a ruler to the floor, which is what the operator was going to do anyway. The mesh
went from 1.27MB to 123KB, which is a fair measure of how much of that model was ring geometry
hanging over open air.

Then the tags would not write, and the reason turned out to be worth more than the fix.

The stake URLs had been generated without a scheme, so every one of them read
`host:8730/nfc?...`. A URI record with no scheme is not a link: the tag reads back perfectly
and then nothing opens. Regenerated with `http://`, and pointed at the tailnet hostname rather
than the tailnet IP, because a tag gets write-protected and an address change would brick all
eighteen at once with no way to rewrite them.

That still failed to write. An NTAG213 reports 180 bytes of user memory, and the tool duly
said the tag was writable with space to spare, but the NDEF container and record header eat
into that and the spelled-out URL was 157 bytes at its longest. Measured on the real tags by
writing until it stopped: **137 bytes goes on, 141 does not.** The datasheet number was the
wrong number to design against, which is the same lesson the cup itself exists to teach.

So the tag now carries a position slug and nothing else, and the brain resolves it. `/nfc`
accepts `p=<slug>`, looks it up in the positions file, and fills in subject, source and
sprinkler server-side. 105 bytes at the longest, 32 to spare. An unknown slug is a 404 that
names the slug and logs nothing: never a default, never a near match, because a run written
against the wrong plant is worse than a run that refuses to be written. `make_tags.py` now
refuses to emit anything over the cap, so that failure happens at a terminal instead of
outdoors with the tag already in the stake. The spelled-out form still works, which matters
because eight asset tags are already written and write-protected.

The first real tap exposed a naming problem that would have quietly split the garden's
history. The eighteen stake names mostly did not match the place entities that already hold
records. Tapping the Blueberry stake would have created a second `Blueberry` beside
`Blueberry Guild`, and from then on that bed's water would live on one entity and its harvests
and photos on another, which the almanac cannot join. Five aliases now point the stake names
at the established entities: Strawberries, Blueberry, Peach, Plum and Pond. Peach and Plum
both resolve to `Peach/Plum Guild`, which is deliberate and means their water combines.

Then the rain came, and the cup turned out to have a second job nothing else here can do.

Two cups set out in the open both read 1.75 in at 13:52, against 1.57 in at the nearest
official gauge through 13:35, in rain that was still falling. A printed cup and an ASOS station
fifteen miles away agreeing inside about 11% is the cup validated as an instrument. It also
surfaced a gap nobody had noticed: **the house has no rain sensor at all.** The Ecowitt
gateway carries soil moisture only. These cups are the only rainfall numbers the property
owns, and eighteen of them is eighteen gauges, which makes the spread between positions a
microclimate reading nothing else here can produce. Today both read the same, which is what a
broad frontal system should do. The interesting readings are the summer convective ones.

The brain could not log any of that, so it can now. `rain` is its own event kind, not a
watering run with a rain source. A run is something that was done, timed, with a depth derived
from a rate sheet; rain is something that happened and the depth is the only fact in the row.
Folding them together would inflate every bed's run count and put a measured number in the
same column as an estimated one, which is the exact confusion the cup was built to end.
`basis` is always `measured`, because there is no other way to get the number. Two guards a
ruler cannot provide: a reading at the brim comes back flagged as a floor rather than a total,
since a full cup and a cup that overflowed twice look identical, and anything past the cup's
50mm is refused as a wrong unit or a mid-storm emptying that should be two readings.

The season's first two rain rows are measured depths. Everything else in the water table is a
spec-sheet estimate, which is the right way round for that table to start.

One test watering, logged during the first tag tap to prove the path, was deleted before the
nightly journal could narrate a run that never happened.

Separately and read-only: surveyed the Plex hubs on the free tier. Home serves three populated
rows and two of them are Recently Added, and the reason is not too many rows switched on but
that Home has nothing else to show. Pinning a collection to Home is the Plex Pass gate, so on
the free tier the only levers are trimming library tabs and reordering pins. Operator decided
to leave the hubs alone and browse elsewhere instead. Nothing was changed on the media server.

### In flight

- Thirteen of the eighteen stake positions still have no place entity and will each create one
  on first tap, with the "created it new" warning. That is correct behaviour, but the warning
  needs reading rather than swiping past: a name that was expected to exist means another
  alias is wanted.
- Rain readings are write-only in practice. `rain_totals` exists, but nothing surfaces it yet:
  not the almanac, not the nightly journal, not `snapshot()`.
- Sprinkler application rates are still spec, not measured. The cup can settle that but the
  attended run has not happened `[non-production]`.
- A local HTML library browser served from `clients/`, with the brain proxying Plex so the
  token never reaches the browser and a deep link handing off to the Plex app. Specified this
  session, not started. Home Assistant has no Plex integration configured, which is the
  separate path for playing to a device.

### Next concrete action

Surface rain where it will actually be seen. `rain_totals` is written and tested but invisible,
and a measurement nobody reads is not yet an instrument. The almanac already puts yield against
water per bed; rain belongs beside them as its own column, clearly separate from applied water
rather than summed into it. The nightly journal should mention a day that had a reading. Both
are deterministic reads of rows that already exist, no model involvement.

Then: the eighteen-position spread is the payoff, so the second and third storms matter more
than the first. Nothing to build for that, only readings to take.

### Non-production, queued

- `[non-production]` Reprint the catch cup now the inside is smooth, and check a ruler reads
  the wet line cleanly.
- `[non-production]` Write the remaining NFC tags and get the last two stakes into the ground.
- `[non-production]` Delete the stake URL file off the phone once the tags are written. It
  carries the live NFC token in plain text, once per line.
- `[non-production]` Soil moisture channel 7 reads `unavailable` and channel 6 is at 1.3V.
  Channel 7 was blind through the whole storm, so that bed has no wet-up reading.
- `[non-production]` Attended catch-cup check on a zone-3 sprinkler position, to replace the
  rate-sheet depth with a measured one.

---

## 2026-09-11 - session wrap: irrigation went from a BLE experiment to a capture path

Three days of work on one thread. Orbit forces their cloud servers to drive a manifold
sitting in this yard, so the point was to take it back locally and, having taken it back,
make water leave a trace the way rain, heat and yield already do.

Shipped: manual-mode commands built from zone and duration instead of hardcoded frames, so
a run can outlast one byte. Watering as a record, attached to a place rather than a valve,
because zone 3 feeds a sprinkler carried to eighteen different spots. NFC watering tags
carrying their own source and sprinkler, one prefilled field between a wet hand and a
logged run. A printed stake with the tag sealed inside, a catch cup so a position measures
what it received instead of trusting a rate sheet, and a generator that turns a positions
file into eighteen stakes and eighteen tag URLs. Applied water and rainfall in the almanac
next to the yield. A weekly photo offered at the tap, only when one is due.

The care throughout was in what does not get claimed. Depths are per place and never summed
across places. Volume is marked estimated until a cup reading replaces it. Beds with no
known rate log minutes and no depth. A sprinkler with no rate is refused rather than logged
with the rate quietly dropped.

Also fixed `hestiactl`, which could not reach the brain on the box it ships with, and
tightened two secret bundles that were group-readable.

[non-production] Open and queued: tomorrow's attended valve test for explicit stop and a run
over 127 seconds, the catch-cup calibration on the same trip, printing the stake set,
writing the eighteen tags, and checking the camera button opens a camera and not a library.

Next concrete action: the valve test. Explicit stop is the one failure that leaves water
running, so no valve control goes into the brain until it passes on hardware.


## 2026-09-11 - the tap asks for a photo, but only when one is due

The watering confirmation now offers a camera, and only when that place has not been
photographed in seven days. Whether one is due is a row lookup rather than anyone's
judgement, which is the same principle that keeps schedules out of the model: in a hot
spell the sprinkler runs four times a week, and four pictures a week of the same shrub is
a flipbook, not a record.

The offer comes after the run is already written, so declining it never costs the logged
watering. A place that has never been photographed is always asked.

Uploads go to a new `/nfc/photo` authorised by the NFC token rather than the ingest one.
A programmed stake is a credential in physical form, and it should be worth exactly one,
not two. The file-and-record path is shared with the Shortcut intake rather than copied,
so both routes file identically and a failed upload says so instead of looking filed.

Thirty-seven NFC tests, full suite passes. Not yet deployed: the brain needs a restart
before a tag will see any of this.


## 2026-09-11 - the stake measures what it claims

Added a catch cup that slides onto a tab above the name, so a position reports what it
actually received instead of what a rate sheet predicts. Collected depth equals applied
depth only while the cross-section never changes with height, so the sides are straight
and there is no funnel. Diameter sets how much water is caught, not what the depth reads.

45mm across, 50mm deep, about 80ml. One 15-minute Hi-Rise cycle collects 10.2mm, so four
cycles is 41mm and the cup carries a week of heavy watering plus rain before emptying.
Rings every 5mm, doubled every 10mm. Prints opening up with the mount as a vertical
through-slot, so nothing needs support. The tab sits above the text and doubles as a thumb
pad for pushing the stake in; `cup_mount = false` removes it.

This is the path off spec-derived numbers. Every watering becomes its own calibration, and
a measured reading flips the stored basis from spec to measured, which is already supported
end to end.

The cup and cap are identical for every position, so the generator renders each once rather
than eighteen times.

Decided against a photo prompt on every tap: in a hot spell the sprinkler runs four times a
week and that cadence is a flipbook, not a record. Weekly per position is the signal.


## 2026-09-11 - hestiactl can reach the estate again

`hestiactl health` had been pointing at localhost while the brain binds to a private
address, exactly as the unauthenticated-brain invariant requires, so a documented command
could not work here. It now reads the same private bundle the brain does, with the keys
added to the tracked example.

The remote host default was worse than missing: a placeholder `youruser@hl-relay` turns
"you have not configured this" into a confusing ssh failure, which is the same trap that
broke the backups. It is unset now, and the commands needing it say what to set and where.
The brain URL keeps its localhost default, which is correct for a single-box install and
wrong only where the brain binds elsewhere.

Two secret bundles were group and world readable where the other six were owner-only, one
of them the WireGuard config. Tightened to 600, and the directory to 700. Nothing outside
the operator's own account reads them, and the home directory was already 750, so this
closes a consistency gap rather than a live hole.

With the tooling working, checked the thing it exists to check: the qBittorrent
kill-switch is intact, VPN egress and host egress are different addresses. Gluetun has
been reporting unhealthy for three months while actually working, so its healthcheck is
the thing that is wrong, not the tunnel. Unresolved, noted here rather than fixed.

Also restarted the brain, which had been running since Sep 8 and so did not know about
watering tags. The eighteen tag URLs are generated and one was fetched against the live
service to confirm the form renders with the subject, source and sprinkler locked in.


## 2026-09-11 - stake pocket resized for the real tags

The first three stakes printed fine and the tag pocket was too small: the waterproof discs
are 30mm, not the 25mm a bare sticker runs. Pocket is now driven by measured tag diameter
and thickness rather than a single baked-in number, with the cap thickness separate so an
already-waterproof tag can sit flush with no cap at all.

Resizing the pocket pushed it up into the second line of text, so the head grew from 66mm
to 72mm and the pocket moved down. Clearance is now 3.4mm to the text and 11.7mm of wall
each side, with 2.6mm of floor under the tag. All eighteen re-rendered.

The operator saw slight lift from the plate and put it down to plate prep, which is
likely right. Noted in the runbook anyway that this part is long, thin and tapered, so the
tip is a small contact patch at the end of a lot of plastic and is the first thing to
peel. A brim is cheap insurance.

Print settings and tag choice are now written down in `hardware/README.md` rather than
living in one print's worth of memory.


## 2026-09-10 - water joins the almanac

The almanac grew a Water section, so applied water and rainfall sit on the same page as
yield. Rain is totalled over exactly the span the watering record covers, which is what
makes the two numbers comparable at all.

The care is in what does not get added up. Depth is per place: 0.4 inch on the peach and
0.4 inch on the strawberries is not 0.8 inch of anything, so depths are never summed
across places. Rain is added to each place instead, since it fell on all of them. Volume
is the only figure that totals, and it is marked estimated because it comes from the
sprinkler's rate sheet rather than a meter. Beds with no known rate are listed by minutes
rather than dropped, so a drip bed does not read as unwatered next to a sprinkler.

Rendered as one dense line per group rather than a bullet per place, for the same reason
the wildlife section was collapsed: the page is injected into the prompt and there are
eighteen places. Year over year now compares seasonal volume and rainfall.

Confirmed by the operator: the timer-fed nine are all zone 3, all eighteen positions are
Hi-Rise watered, and Pond is an area as well as a pond. The positions file needed no
change. Three stakes are on the printer.

Six new almanac tests, full suite passes.


## 2026-09-09 - eighteen positions, eighteen stakes

The paper journal gave up the position names, and there are eighteen, not eight. Nine fed
from the smart timer and moved by hand, nine straight off the far tap. Added
`hardware/make_tags.py`: it reads a positions file and emits a stake per position plus the
tag URL for each, so a name is typed once and becomes the embossed label, the tag subject
and the records entity together. All eighteen render in about seven seconds.

Names longer than one line split at the space that leaves the halves most even, with ties
going to the later space so the first line carries more. A name containing a `#` is URL
encoded, which matters more than it looks: unencoded it would truncate the tag URL at the
fragment and the scan would arrive with no source and no sprinkler, logging a run with
neither. Verified end to end through the live routes.

The URL file holds the NFC token on every line, so it is written owner-only, never printed,
and lives under the gitignored data directory along with the positions themselves.

Open and assumed, flagged rather than silently baked in: the timer-fed group is recorded as
zone 3, which leaves zones 1 and 2 unaccounted for, and every position is currently marked
as Hi-Rise watered. Anything actually watered by a hose at the base should lose its
sprinkler field so it logs minutes and claims no depth.


## 2026-09-09 - a watering tap, and a stake to put it on

Added `kind=watering` to the NFC path. A tag carries its own source and sprinkler, because
a stake in the ground never moves and those answers never change, so a scan lands on a form
with one prefilled field and a button. That matters more here than on any other tag:
watering gets done with wet hands, in a hurry, before coffee. The derived depth and volume
are shown back on the confirmation, so an estimate is visibly an estimate at the moment it
is made rather than a number discovered later in a report. A tag naming a sprinkler with no
known rate is refused rather than logged with the rate quietly dropped.

Added `hardware/nfc-stake.scad`, a parametric printed stake with the tag sealed in a pocket
under a cap instead of stuck to the surface. Surface stickers are what fail outdoors: the
antenna is aluminium on film, and once water gets under an edge it corrodes and the tag dies
silently. Prints flat, no supports, PETG or ASA because PLA will not last a season in the
sun. The name is both the embossed label and the event subject, so the two cannot drift.

Twenty-six NFC tests, full suite passes. Still waiting on the eight zone-3 position names
before any tag gets written. No brain tool, no Home Assistant entity, no schedule.


## 2026-09-09 - water becomes a record

Added watering to the records substrate. A run is logged against a place, not a valve,
because zone 3 feeds a sprinkler that stands in eight different spots and a zone number
is therefore not a location. The plumbing goes in a source field instead. Water and
harvest now share one entity, so the almanac can put applied water against yield without
a join.

Depth and volume are only recorded when an application rate is actually known, tagged
with the basis they came from so a manufacturer's figure never reads back as a
measurement. Drip and soaker beds log their minutes and claim no depth, the same refusal
harvest makes when it will not turn a count into a weight. Season totals report an
unmeasured count rather than dropping those runs or treating them as zero.

The Gardeners.com Hi-Rise sheet gives 1.5 to 1.7 inches per hour over a 16 to 18 foot
circle at 25 to 30 PSI. At the midpoints a 15-minute cycle is 0.4 inch and about 57
gallons, so a full eight-position rotation is roughly 450 gallons, and both sprinklers
together move about 900 gallons a pass. Those figures are spec-derived and stay marked as
such until the queued catch-cup test either confirms or replaces them. Worth noting: 1.6
inches per hour is faster than most soils absorb, so part of a 15-minute run may be
running off. The existing moisture channels can answer that without buying anything.

Fourteen focused tests, full suite passes. Still no brain tool, no Home Assistant entity,
no schedule. Next: the eight zone-3 position names, then the capture path for them.


## 2026-09-08 - a clean full minute, and control that is built instead of hardcoded

A 60-second run on another zone held on a single connection, reporting watering at every
ten-second checkpoint and idle after expiry, with no fallback stop needed. That answers
the connection-lifetime question raised by the previous run. Reported seconds remaining
does not count down, it echoes the requested duration, so it is not a progress signal.
The earlier early-idle reading is still unexplained and has appeared on one zone only.

Added a local control tool. Manual-mode commands are now built from a zone and a duration
instead of fixed frames, so runs longer than 127 seconds encode correctly, and the built
frames reproduce the two verified on hardware byte for byte. Stop turns out to be the same
command with a zero duration. Actuation refuses to proceed without an explicit
acknowledgement, starts only from a validated idle reading, watches the run throughout,
and sends a stop if it cannot confirm idle. Focused tests drive a fake manifold that
rejects any frame other than get-status or a manual-mode command for the requested zone.
The full suite passes.

Still unverified on hardware: explicit stop mid-run, and any duration above 127 seconds.
[non-production] Both need one attended run with water going, queued against the morning
watering round. No brain tool, Home Assistant entity, recurring schedule or cloud client
was added. Next: those two tests, then zone-to-bed mapping and irrigation events in records.


## 2026-09-08 - longer watering test exposes unresolved behavior

An authorized 60-second test initially returned a validated watering status with
60 seconds remaining. The operator confirmed physical actuation. A midpoint read
reported idle by approximately 30 seconds, so full-minute execution is not proven.
The BLE connection then dropped before the final read; fallback stop transmission
failed on that connection. A new read-only connection confirmed idle afterward.

No additional watering run was started. Next: reconcile physical duration with the
early idle response and investigate connection lifetime before more control tests.
The read-only tool and brain runtime were unchanged.


## 2026-09-08 - local timed watering cycle verified

Completed one operator-authorized 10-second BLE valve test. A validated status reply
first confirmed idle, then confirmed the requested zone watering with the supplied
duration, then confirmed idle after expiry. No fallback stop was needed or sent.
The command worked without the upstream clock/program setup sequence.

Updated the runbook with the narrow result and remaining validation. Explicit stop,
connection-loss behavior, and repeatability remain untested. No recurring watering
or general control integration was deployed. Next: validate explicit stop separately.


## 2026-09-08 - local B-hyve status verified

Added a standalone read-only BLE status tool. It sends only a session handshake and
a fixed status request, validates both response checksums and status fields, and
reports missing or unknown data explicitly. Live verification returned a validated
idle status without the upstream clock/program setup sequence.

The operator authorized the one-time credential lookup. That setup step is complete;
credentials remain private and the status tool makes no cloud requests. The matching
operator queue items are complete.

Nine focused discovery/protocol tests pass. Cryptography is a locked development
dependency for the synthetic protocol tests. No brain integration, recurring
polling, valve control, or schedule changes were deployed. Next: separately validate
short timed actuation, explicit stop, and automatic expiry before considering control.


## 2026-09-08 - BLE discovery working

Extended the discovery probe to include B-hyve names when advertisements omit
service UUIDs, and to report discovered service UUIDs. Name matches remain candidates,
not proof of a particular device type. All four focused tests pass.

Discovery and GATT inspection are working. Authenticated status decoding, firmware
compatibility, and valve control remain unverified. No application commands or
irrigation configuration changes were made.

Next: obtain a BLE key through an approved private path, then test a minimal status
query without the upstream setup sequence. [non-production] A one-time external
credential lookup needs an explicit decision under the local-only rule; queued at 15:00.


## 2026-09-08 - Bluetooth host access checked

Remote access is working after correcting the login identity. Hardware inventory
still exposes no Bluetooth controller on either checked host. The discovery probe
has not reached a device; protocol compatibility remains untested.

[non-production] Enable Bluetooth in firmware settings at 15:00 or the next
convenient reboot. The private queue replaces the resolved access question with
this physical step. Next: verify adapter visibility and run discovery.


## 2026-09-08 - B-hyve discovery probe prepared

Added a bounded BLE discovery probe and an operator runbook. The probe can inspect
advertisements and GATT characteristic availability without pairing or sending
application commands. Three focused tests pass. No valve control is deployed.

Upstream source review found that its high-level status method sets the clock and
disables programs during setup unless a configured program mask restores them.
The runbook records this behavior so discovery does not accidentally alter schedules.

Live discovery remains blocked on access to a Bluetooth-capable host. Firmware and
protocol compatibility are unverified. Next: run discovery on an accessible adapter,
then review authenticated status queries separately from setup and actuation.

[non-production] Operator input needed at 15:00 to identify an accessible Bluetooth
host for the probe; the private queue carries access details.


## 2026-09-08 - garden integration context review

Reviewed the existing garden integration without changing runtime behavior. The
tracked garden documentation describes six Ecowitt moisture channels exposed through
Home Assistant; the brain reads those states and garden-watch applies deterministic
weather and moisture rules. Garden records already support observations and harvests.

Historical measurement retention and its connection to recorded garden actions remain
unverified. The next concrete investigation is to check existing history coverage before
designing additional collection. No implementation or hardware rollout was performed.


## 2026-09-07 - NFC rollout verified and session wrapped

Operator wrote five maintenance tags and verified that every link opens the correct
asset page. This confirms tag routing; it does not claim service work was submitted.
All maintenance cadences are now confirmed, superseding the earlier open interval note.
The setup sheet includes the final schedules.

[non-production] Three outdoor-equipment tags remain. Operator plans to finish them
during the September 8 watering round; the existing queue item carries the details.
Gemma observation continues, with the tone review still September 14 at 15:00.
No additional build work was requested.


## 2026-09-07 - multiple fixed maintenance dates

Added multiple fixed dates per year to maintenance schedules. Each occurrence needs
its own service completion; overdue work carries forward, and schedule activation
does not invent past missed checks. Updated the private NFC sheet and closed the
remaining maintenance-cadence review in the operator queue. All 31 focused records,
maintenance API, and NFC tests passed.


## 2026-09-07 - seasonal maintenance and operator follow-through

Added fixed annual maintenance dates alongside elapsed-day intervals. Service completion
satisfies that year's check without shifting next year's date; overdue checks carry
across New Year. Briefing, records output, and dashboard show the schedule explicitly.
Regression coverage includes due-day boundaries, early completion, next-year recurrence,
and usage logs that must not clear maintenance. Full regression suite passed.
Brain restarted healthy on Gemma; the matching Glance template was deployed and restarted.

Operator confirmed improved chat reliability and the Voice PE DHCP reservation.
The Gemma trial continues with positive feedback on speed and accuracy. Authorized
note-taker backlog cleanup completed; approved memory was not changed.

[non-production] Finish writing and scanning the maintenance NFC tags using the prepared
one-sheet. The washing-machine interval remains unconfirmed. Gemma tone review remains
September 14 at 15:00. Personal schedules and setup materials remain private.


## 2026-09-07 — calendar tool, on the shopping-list pattern

**Hestia asked for a calendar; the roadmap had it queued since July.** Storage is Home Assistant's
Local Calendar integration (set up by the operator, one calendar, populated with birthdays, a
recurring trash day, a repair, and a weekly swim). The brain side is a new `calendar` tool that
follows `shopping` exactly: HA is the single source of truth, the tool relays, nothing phones home.

**What the tool does.** `show` with the user's range phrase ('today', 'this week', 'next week',
'this weekend', 'Saturday', 'September 14'), or nothing for the next 7 days; `add` with a title
and the user's date phrase verbatim. Date math lives in the tool: the reminder tool's parser is
now shared (`parse_when`) and grew weekday names ('Saturday at 10', 'next tuesday 2pm'), so a
phrase means the same day whether it becomes a reminder or an event. A phrase with no clock
time files an all-day event; a timed one defaults to an hour. Recurrence is HA's job (it owns
the `.ics`), and edits and deletions stay in the HA app: there is no delete service, and a
calendar the model could silently rewrite is worse than one it can only append to.

**Briefing.** A new calendar section lists today's events and a heads-up for tomorrow's, as
facts the model narrates. The recurring trash-day event is exactly the kind of row the
determinism invariant wants: a timer, not a memory.

**Wiring.** Tool registered (eleven now), `tool_contract` knows `add` is a mutation with a
recognized receipt, intent scoping in `hestia.py` advertises the tool on calendar words, and the
system prompt tells the model when it is a calendar entry versus a reminder. Calendars are
discovered from HA (`calendar.*`, cached 10 min) unless `HESTIA_CALENDAR_ENTITIES` pins them;
new events go on the first. Tests in `brain/tests/test_calendar.py`. Verified against HA
read-only (`show` returned the live events); `add` was verified against the stub only, since
there is no way to delete a test event from the brain side.

**Not built, on purpose.** Google/iCloud sync (phones home). Phone-native calendar sync is a
Radicale-on-hl-relay job for later if the HA app's calendar view is not enough.

**Also committed at wrap:** the harvest fix that had been sitting uncommitted since the 09-01
audit (compound weights like "2 lb 7 oz", tolerant spoken bed names, and the garden_bed rule
that a harvest turn ends in a tool call or a question, never a bare "logged"). Suite green with
it.

**Model note.** The brain is on the gemma4:12b trial drop-in. Operator's read after a night of
use: liking it, a bit of an overtalker and a touch too bubbly. Tone is a prompt.py job, not a
model swap; giving it a week of ordinary use before touching it.

**First real add, and one miss (same morning).** The operator's test event landed. Then "add
Take out the Recycling to the calendar for next Tuesday" got a clarifying question instead of
an event: the trace shows the model never called the tool, it decided "next Tuesday" was
ambiguous on its own (a good failure mode, but an unneeded one, the parser already read it).
Two fixes: the schema and prompt now say relative phrases are not ambiguous, call the tool and
let it report an unreadable phrase; and "next <weekday>" now means that day of next calendar
week (Monday-based, the same week the "next week" range uses), so said on a Monday it is eight
days out, not tomorrow. Replayed live: the recycling event filed for Tue Sep 15 in one tool call.

**Briefing confirmed.** The 7:10 briefing on 2026-09-07 carried the calendar section; operator's
words: "folded into the daily briefing very cleanly." Watch item: the weekly trash event spans
8am to 8pm, and if that reads as noise when spoken, the fix is the event's shape in HA, not
the brain.

**In flight / next:**
- `[non-production]` Read the Gemma tone question again after a week of use and decide whether
  to sand the persona lines in `brain/prompt.py`.
- Next build: nothing queued from this session. The roadmap's endorsed list is now fully
  shipped; candidates are semantic recall (the brain named its own keyword-recall gap) and the
  puppy-weight watcher ahead of the late-September litter.

---

## 2026-09-01 — harvest-log audit uncovers a silent tool-call miss; ships NFC capture as the fix

**Chat-logged harvests didn't match what the user actually said.** Asked to validate a morning
harvest-logging chat session against `hestia.db`: only 2 of 8 harvest messages had actually
called the `records` tool, despite the brain replying as if all of them logged. Root cause: an
unresolved bed name ("the squash bed" — never a real entity, actually one of the four numbered
raised beds, Bed 2) hits a chat-agent path with no loud-failure or clarifying-question
behavior — it fabricates a success reply instead of asking or erroring. The 5 lost harvests
were reconstructed from the log text and backfilled by hand (event ids 134–138); the carrots
entry was backdated to the date the user actually meant (2026-06-15).

**Shipped a parallel, no-LLM capture path (`GET /nfc`, `POST /nfc/log`) instead of patching the
chat agent's prompt.** A physical NFC tag encodes a bed/asset name; scanning opens a
locked-subject form that writes straight to `records_store` and confirms synchronously — no
model in the loop, so the failure class above is structurally impossible on this path. Three
kinds: `harvest`, `service` (resets `due_assets()`), `use` (a runtime metric with no due-date
implied, e.g. weedwhacker minutes). Token-gated (`NFC_TOKEN`, `secrets/nfc.env`). First real tag
(Bed 2) written and verified end-to-end: 5.1 lb winter buttercup squash logged via a physical
tap, no typing.

**Registered 7 assets** for the maintenance side: Weedwhacker and Ego Lawn Mower (no interval
set — usage-only / unknown schedule), Washing Machine and Furnace (30d / 365d, from the user's
own estimates), AC Living / AC Guest / AC Master (30d, assumed default for Midea U-Shape units,
not confirmed against the manual).

**Added `GET /maintenance/due` + a third Glance tile ("Maintenance due")**, deployed to
hl-relay. Same one-collector-many-consumers shape as `/status` and `/memory/inbox`. Backend
verified live (5 assets correctly showing as never-serviced); the tile's actual on-screen render
is unconfirmed — Glance loads widget content client-side, so a plain curl of the page never
shows tile text even for tiles known to work.

### In flight

- **NFC tags physically un-written.** `[non-production]` URLs generated for all 7 remaining
  assets; writing them into NFC Tools and testing each tap is on the operator.
- **Glance tile render, unverified.** `[non-production]` Needs a browser check, not a build
  step — the backend and deploy are both confirmed clean.
- **AC/washer/mower service intervals are assumptions, not confirmed numbers.** `[non-production]`
  30d defaulted for the three ACs and the washer; the mower has no interval at all ("not sure on
  maintenance schedule"). Needs the operator's own knowledge or the manuals, not a build
  decision.
- **Backlog from the session's own scoping conversation, not yet built:** a multi-subject
  `service` tag (one tap on the power washer logging house + pergola + vinyl fence at once), a
  greenhouse-roof asset/tag, a mulch-tool tag, and a genuinely different shape — a yearly,
  no-tap-to-scan calendar reminder for January seed-starting, which needs the `reminders` table
  to support recurrence (it's one-shot only today). Next concrete action: extend `/nfc/log`'s
  `service` kind to accept comma-separated subjects.
- **Fridge/pantry inventory + receipt logging + leftover capture** raised and explicitly parked
  as its own future project — not started, not scoped.

## 2026-08-29 — the Qwen3.8-27B trial survives a real OOM crash, and hestiactl gets a GPU toggle

**Minecraft needed the 5080's VRAM.** First attempt (pinning Prism Launcher's rendering to the
4060 Ti via GNOME's `switcherooctl`, a `.desktop` override) crashed Minecraft and was rolled
back. The fix that stuck: `hestiactl` gained a `gpu` target (`up`/`down`), stopping or starting
`hestia-brain` and `hestia-ollama` together. `down brain` alone was never enough to free VRAM,
since `OLLAMA_KEEP_ALIVE=-1` keeps the model resident until Ollama itself stops. Committed and
pushed (`61f1f61`).

**The Qwen3.8-27B resident-brain trial hit a real production crash.** Same day it started, the
UD-IQ4_XS quant (about 15.5GB resident, leaving only ~700MB headroom on the 16GB 5080) began
throwing genuine CUDA out-of-memory aborts mid-conversation, confirmed in `ollama`'s own log
(`ggml_cuda_pool_vmm::alloc`, `runtime OOM detected`), not a hang. Reverted to `qwen3:14b` to
restore service.

**Side effect worth knowing:** the `num_ctx=32768` fix that made the trial possible at all
(`93b20e9`, already committed) applies to every resident model, not only the trial one.
`qwen3:14b`'s own footprint grew from about 10GB to about 14.5GB, so the 5080's normal headroom
is now roughly 1.8GB, not the ~6GB earlier notes describe.

**Round two, same day: the smaller `Q3_K_XL` quant plus a halved context window.** Pulling
`Q3_K_XL` (13.1GB weights vs UD-IQ4_XS's 14.3GB) hit a real upstream Ollama bug: registration
fails with a bare "file does not exist" right after the download completes clean
([ollama/ollama#15447](https://github.com/ollama/ollama/issues/15447)). Worked around it by
hand-building the Ollama manifest from the already-downloaded, hash-verified blobs, no re-pull
needed. Result: the smaller quant alone barely moved headroom (about 756MB free, roughly the same
as before), but halving `HESTIA_NUM_CTX` (32768 to 16384) on top brought it to about 1052MB free.
Confirms the architecture's near-fixed-size KV state again: neither the weight quant nor the
context length is the dominant VRAM lever the original trial notes assumed.

Live now on `Q3_K_XL` + 16384 context (`~/.config/systemd/user/hestia-brain.service`, not the
tracked template, this stays a personal live-unit trial). The unit has run continuously since
2026-08-28 07:27 with no restarts and no OOM errors logged, including through a real stretch of
ordinary use that afternoon and evening (media recommendations, movie downloads via the `media`
tool). That is a genuinely good sign. As of this entry, `qwen3:14b` happens to be the model
actually resident in VRAM (likely separate manual testing, unrelated to the brain unit, which
never restarted), so the next real request through the brain will pay a fresh cold-start reload
of `Q3_K_XL`.

### In flight

- **`Q3_K_XL` + halved-context Qwen3.8-27B, soak-testing.** `[non-production]` Watching whether
  roughly 1GB of headroom holds up over the following days is on the operator, not a build task.
  Revert path if it OOMs again: set `HESTIA_MODEL=qwen3:14b` in the live unit, drop the
  `HESTIA_NUM_CTX` override, `daemon-reload` + restart `hestia-ollama` and `hestia-brain`.
- **Multi-GPU tensor split, untested.** The 4060 Ti sits mostly idle (about 4GB used for voice,
  about 12GB free) while `hestia-ollama` is pinned to the 5080 only via `CUDA_VISIBLE_DEVICES`.
  Letting Ollama see both GPUs would tensor-split the model across roughly 28GB combined, likely
  solving the headroom problem outright at the cost of some cross-GPU latency. Next concrete
  action, not yet started.
- **Semantic memory recall moved up in priority**, not from the original design doc but from live
  use: the resident brain named its own keyword-only recall as a real gap unprompted, independently
  confirming what `MEMORY-DESIGN.md`'s vector-recall "later" item already planned.

**Small find, not a big deal:** a Plex library listing (71 movies) surfaced one mis-tagged entry,
"The Lord of the Rings: The Two Towers - Extended Edition - Audio Commentary by the Cast" is filed
as its own movie, and the actual film isn't in the library under its normal name. `[non-production]`

## 2026-08-24 — Eyes gets an OCR lane, and a maintenance clock that anything could reset

**Where this started.** Looking for notes on "giving Hestia eyes" in the context of photo-logging
friction. They existed and were three days old: `brain/EYES_PLAN.md`, the NFC-assisted field
capture section. The gap on re-reading was that **OCR was never actually in the plan**. The only
line touching it said a scale or label "can be read when visible", with the read assumed to fall
out of the VL judge, no accuracy expectation, no test, and no way to find out when it was wrong.

**The OCR lane, now specified.** It is a tier of its own, and it returns digits plus unit plus
confidence, or `unreadable`. Never an estimate. A quantity may only come from pixels of a display
or a printed label, because a pile of tomatoes does not imply three pounds. Printed labels are
ordinary OCR on CPU, so the Ti VRAM budget is untouched. Seven-segment scale displays are written
down as a genuinely separate and harder problem rather than assumed to work, to be vetted against
photos of the actual scales before an engine is picked.

The part that makes the whole capture cheap is not vision at all: the NFC tag gives the bed, and
**the bed's `plantings` attr gives the crop shortlist**. The classifier is never asked what plant
this is over all of botany, only which of the two or three things actually planted there. A
confident answer from outside that list becomes a flag rather than a result.

**Trust is earned per surface** (superseded later the same day, see "Reversed the commit gate"
below; kept because the reasoning is why the reversal happened). Log the harvest by hand as usual, let Eyes read the same photo,
and reconcile rather than insert: match on the capture batch ID or on (bed, crop) in a short
window, count agreements, and keep disagreements as labelled failures with the display crop
attached. Duplicate harvest rows would poison the season totals and the year-over-year deltas,
which is worse than a bad read. Same posture as the note-taker's inbox, and it means the decision
to trust a read is a number rather than a feeling.

**The bug that was already live.** `due_assets()` found an asset's last logged event **of any
kind** and compared its age to `interval_days`. No filter on kind. The path was reachable in
shipped code rather than only by future use-taps: the photo intake's `asset` domain files a `photo`
event against the asset itself, so photographing the mower would have marked it maintained and
dropped it out of the morning briefing silently. Checked the live DB afterwards: no asset carries
`interval_days` yet, so nothing was actually lost, and the fix lands before the first one does.
Fixed today with an allowlist (`chore`, `service`), deliberately not a denylist, because
the failure directions are not symmetric. An unlisted kind leaves an asset visibly due, which is
annoying. A kind wrongly counted as service makes it disappear and never come back. The module
docstring had described the correct behaviour all along; the query never implemented it.
Regression test proves it: it fails against the old query, passes against the new one. Full suite
green, 209 tests.

### In flight

- **Accumulator and reset tags**, written into the spec and explicitly not decided. One tag per
  action rather than one per object: the washer's lid counts loads, the filter door records the
  drain and zeroes the count. Nothing needs disambiguating because the meaning was chosen by which
  tag got touched. The washing-machine filter at roughly 20 washes is the motivating case, and the
  reason it was never tracked is that there was no counter to hang it on.
- **Usage may add urgency, never remove it.** Taps undercount, because a forgotten tap leaves no
  trace and nothing corrects it. So a use threshold can only be a floor ORed with the existing
  calendar ceiling: 20 uses or 56 days, whichever trips first. Sequenced after the decision above,
  and it is records-and-briefing work with no model in it.
- **A connected scale would bypass OCR entirely.** Prefer a number to a picture of a number. Worth
  knowing which scales in the house already talk before any investment in the seven-segment read.
- **Wildlife into the almanac** is the shortest unbuilt path in the lane. `sighting` events, the
  wildlife skill's routing, and the almanac's wildlife section all already exist; only the capture
  step is missing. The gain is the species you cannot name out loud, plus photo evidence attached
  to a first-of-season date that year-over-year comparisons will lean on.

**Reversed the commit gate, same day.** The spec had field capture ending in a batch review:
nothing enters records until a human approves the rows. Checked the note-taker's inbox, which is
the identical propose-then-promote mechanism running since mid-June: **29 pending against 4
promoted, oldest 71 days**. A gate that depends on coming back later is a gate that stays shut, and
the calibration lane was worse still, asking for the harvest to be logged by hand *and* the machine
read reviewed against it. The asymmetry that settles it: a wrong weight is visible and editable
forever, an unlogged harvest never existed. So capture writes immediately flagged `unverified`, and
the 7:10 briefing (an existing habit, already read) is where corrections happen by reply.
Calibration then costs nothing, because every correction is a labelled failure captured at a moment
the human was already looking. Invariant #4 holds: entities still gate on a confirm because they
are expensive to retrofit, events do not because they are cheap and correctable.

**Flag expiry specced in the same pass**, because the briefing is now load-bearing and could
become the next unread inbox. Two briefings then the flag clears for good, the row keeps its number
either way, and the list is capped with the remainder counted so a heavy harvest morning cannot
produce a wall. It is a predicate rather than a process: an `unverified_until` stamp in the event
attrs, so there is no sweep job that can fail silently. The nastier catch it also closes is that
free calibration depends on reading silence as agreement, which is only true if somebody looked;
without engagement in the window a row is `unreviewed` and excluded from the rate, since a
believable wrong accuracy number is worse than no number.

### Next concrete action

Read `EYES_PLAN.md` end to end with the accumulator/reset context fresh and settle the one open
decision `[non-production]`. The prerequisite that would have made use-taps dangerous is already
cleared, so the build behind it is small: `interval_uses` ORed against `interval_days`, a
`COUNT(*)` since the last reset event, and a tap endpoint. Everything else in the lane is
downstream of choosing which objects get tagged first.

### Non-production, queued

- `[non-production]` Read the spec fresh and decide on accumulator/reset tags.
- `[non-production]` Check whether either house scale is Bluetooth or WiFi, which decides whether
  the seven-segment OCR work is worth doing at all.
- `[non-production]` Decide what happens to the 29 stale note-taker proposals. Recommendation is
  bulk discard rather than a review session: the oldest is 71 days, most are moot, and the fix
  was the mechanism, not the backlog.

---

## 2026-08-18 — Voice PE down two days: a stale pinned address, found by a person

**The outage.** The kitchen Voice PE satellite went silent for two days while every service
probe stayed green. Postmortem: Home Assistant on the relay had crashed on file-descriptor
exhaustion (`Errno 24`, fatal event-loop shutdown) and restarted; in the same window the
satellite took a new DHCP lease. HA's ESPHome entry stores a *pinned* host address, and HA
runs in a docker bridge network, so its mDNS view never learned the new one. HA retried the
dead address for two days while the device sat healthy on the LAN, port 6053 open. Brain,
Whisper, Piper and Ollama were fine the whole time — the break was purely HA → device. Fixed
by rewriting the pinned host in HA's storage (stop container, edit from a throwaway container
— the file is root-owned and the deploy user has no sudo — start). Satellite back to `idle`,
all entities available, verified through the HA API.

**The lesson was already named.** Fourth instance of the placeholder/pinned-address family:
the placeholder backup host, hestiactl's placeholder remote, the brain unit's bind
contradicting its own comment, and now this. The family's signature is that nothing crashes —
reality just drifts from what a config file claims. Two new observability layers, both built
on the existing watchdog patterns, now cover it:

*Critical-entity probe (off-site watchdog).* The dedi now also checks, over the tailnet, that
the HA API answers and that an allowlist of critical entities does not read
`unavailable`/`unknown`. An entity must read bad on two consecutive runs (~10 min) before it
counts, so routine HA restarts stay silent; one page per transition, same as the other probes.
An unreachable HA pages urgent once and mutes the entity checks, so one outage cannot page as
many. All four paths (up, down, recovery, HA-down) were tested live against the real house
before shipping. This probe alone would have paged on Sunday night.

*Weekly drift check (GPU box).* `hestia-drift.sh` + `hestia-drift.timer`: sweeps installed
units and hestiactl for unsubstituted placeholders, compares the brain's bind address against
what hestiactl probes, and compares the satellite's pinned host in HA against its live mDNS
address. Alerting is edge-triggered on the *set* of findings — one page when the set changes,
silence while it persists. Its first live run caught two real findings immediately: hestiactl
still on the placeholder remote (remote status/logs broken), and the brain binding the
tailnet address while hestiactl probes localhost — so `hestiactl status` shows red on a
healthy brain, training the operator to ignore red. Both still open at this writing.

**Still open.** A DHCP reservation for the satellite on the router (AdGuard's DHCP is off, so
the router owns leases — without a reservation the pinned address will drift again); the
fd-exhaustion root cause on the HA container (nofile limit, plus LIFX entries throwing setup
errors in the log); the end-to-end voice canary and the backup dead-man's switch remain
unbuilt. README's ops bullet now reflects the two new layers.

---

## 2026-08-17 — Tool-layer audit: eleven fixes, and an alert layer that had never once worked

A full audit of `brain/tools/` against the design invariants, then every finding fixed
test-first. Seventeen commits, and the suite went from 148 tests to 200. Work was split
across two agents on file-disjoint sets and merged without a conflict.

**Invariant 1 held cleanly.** Nothing in the tool layer hands scheduling, counting,
thresholding or date math to the model. Reminder parsing, shopping splits, harvest totals,
release selection and weather thresholds are all computed in code. The thesis is intact.

**The headline: NWS alerts have been dead since the day they were written.**
`api.weather.gov` accepts at most four decimal places and 301s anything longer. The
configured lat/lon carries seven, and httpx raises on an unfollowed redirect, so the call
failed every single time. The old code swallowed that and returned an empty list, which
rendered as a confident "No active National Weather Service alerts", in the tool and in the
7:10 briefing alike. The frost and freeze warning layer, the one safety-relevant readout in
the whole tool, had been reporting all-clear while never reaching the service. It only
became visible because the same session made outages announce themselves. Coordinates are
now rounded and both weather.gov calls follow redirects; verified end to end against the
live service, resolving zone CTZ012.

**The other fixes, by shape.**

*Failures that reported success.* Alerts as above. The `home` tool blamed a whole action
when only its confirmation read-back flaked, so the model would apologise for a light that
had in fact changed and might toggle it back. `media` hid a dead Lidarr behind a bare
`except: pass` and just omitted music from the download list.

*Failures that took down more than themselves.* `dispatch` caught only `TypeError`, so any
store or filesystem error from `memory`, `recipe` or `reminder` escaped as a bare HTTP 500
with no answer at all. One `[N/A]` column from `nvidia-smi` raised out of `snapshot()` and
cost the entire health readout plus a 500 on `/status`. An Ollama restart 500'd the client
because only `asyncio.TimeoutError` was guarded. All three now degrade to an answer.

*A real security hole.* The `search` tool's fetch took any model-supplied URL with
redirects followed and no host restriction, on a box that runs unauthenticated internal
services by design. An injected page could have steered the model into reading loopback,
LAN or tailnet endpoints and speaking the contents back. Fetch now resolves every address a
host maps to and refuses anything not globally routable, including Tailscale's `100.64/10`,
which Python's `is_global` would otherwise pass. Redirects are refused outright.

*Wrong answers.* "Tonight at 9" fired at 9 the next morning, a twelve-hour miss delivered
with a cheerful confirmation. Bare hours after "tonight" now read as PM across 4 to 11, with
an explicit am/pm always winning. ISO input with `Z` or an offset was stored in the wrong
timezone and then string-compared against naive rows; everything is naive local now.

*Unvalidated model input.* `records` accepted any `qty`, so a negative harvest could poison
permanent season totals, and silently dropped malformed `attrs` rather than refusing.
`shopping` filed "milk, milk" twice.

**Deploy correctness.** The tracked brain unit shipped `--host 127.0.0.1` beneath a comment
describing a Tailscale bind. Deployed verbatim it starts cleanly and cuts off the phone, HA,
hl-relay and the Voice PE, the same failure family as the placeholder backup host and as the
two `hestiactl` defects recorded on 2026-08-16. It now carries a placeholder that fails
loudly until substituted. README separately claimed the brain binds `0.0.0.0`, contradicting
invariant 3 and SECURITY.md in the same public repo.

**Invariant 5 got an honest exception.** The weather tool has always called Open-Meteo and
api.weather.gov while the README claimed the brain never phones home. Both are keyless and
take only a lat/lon, and a forecast cannot be computed locally. That is now stated in
CLAUDE.md and README rather than quietly contradicted. `CLAUDE.md` is also tracked for the
first time, so the invariants survive a clone.

**One audit finding was wrong and is recorded as such.** The claim that `limit=-1` would
dump the whole event log was false: `records_store.py` already clamps to `[1, 200]`, so the
worst case was 200 rows. The `qty` and `attrs` halves of that finding were real.

**In flight.**

- The work sits on `fix/tool-layer-hardening`, merged to main and pushed as part of this
  entry. The brain has been restarted onto it and verified.
- `[non-production]` Now that `CLAUDE.md` is tracked on a public repo, it names the private
  docs by filename. Contents are not exposed, only the names. Worth a decision.

**Next concrete action.** Nothing here is load-bearing. The nearest useful follow-on is the
class of bug this session kept finding: a component that fails silently and reports success.
The watchdog already covers the brain being down, but nothing covers a subsystem that is up
and lying, which is exactly what the alert layer did for months.

---

## 2026-08-16 — Plex "library won't load" traced to dead remote access, not DNS

Reported symptom: the TV would show the server but never populate the library. Suspicion was
split between Plex and household DNS. It was neither.

**The server was healthy throughout.** `/identity` answered in about a millisecond, the library
mounts were all bound read-write and populated, and the scanner was actively generating chapter
thumbnails while the TV was failing. The direct LAN path was verified end to end, including a TLS
connection over the exact secure hostname a client is supposed to use, which returned 200.

**Root cause: remote access has been dead since 2026-07-29.** Plex self-tests its own public
address hourly and had failed 828 consecutive times, refusing immediately rather than timing out.
The server was relying on an automatic UPnP port mapping that no longer exists, which is
consistent with the fully-closed WAN verified in the 2026-08-11 exposure audit. The server keeps
publishing that dead endpoint as a valid way in, so any client that reaches for it instead of the
direct LAN route hangs and never loads the library.

Deliberately **not** reopening the port. Invariant 5 and the closed-WAN posture win over a client
sitting on the same LAN as the server. The fix belongs on the client side, plus turning off remote
access so the dead endpoint stops being advertised at all.

**Ruled out, recorded so none of it gets re-hunted.**

1. *AdGuard is not blocking Plex.* The apex `plex.direct` record returns a null address from
   Cloudflare and Google identically. That is Plex publishing it, not a filter rule. No blocklist
   on the resolver contains the domain. Only a Plex telemetry host is genuinely blocked, which is
   intentional and harmless.
2. *DNS rebinding protection is not in play.* The per-server secure hostname, which encodes a
   private address, resolves correctly through the household resolver, the router, and public
   resolvers alike.
3. *The GDM discovery errors in the Plex log are stale*, last seen 2026-07-29, zero in the
   trailing two hours. The server is on host networking with the discovery ports listening.

**Two side findings, both unresolved.**

- *The relay host does not use its own DNS filter.* Its resolver is Tailscale MagicDNS, so the box
  hosting AdGuard resolves around it. Plex logged 14 transient failures to resolve a public
  hostname between 2026-08-01 and 2026-08-16, roughly one every day or two. Nothing watches this.
- *The VPN sidecar is marked unhealthy* on its own internal resolver timing out, while the tunnel
  itself is intact and the kill-switch verified (egress address still differs from the WAN
  address). Torrent traffic is unaffected in practice, which is why it went unnoticed.

**Two defects in `deploy/hestiactl`, found and not yet fixed.**

1. The remote target falls back to a **placeholder** SSH user, so every remote subcommand fails
   until the operator already knows the real one. Same failure family as the scrub-broke-backups
   incident: a placeholder shipped as a default.
2. The brain URL defaults to loopback while the brain correctly binds its private address per
   invariant 3, so `hestiactl status` reports the brain unreachable **permanently** while it is up
   and serving every tool. A status board that cries wolf every single time is worse than none,
   because the one real outage gets skimmed past.

**Next concrete action:** confirm which subnet the TV is on `[non-production]`, already queued.
Then turn off Plex remote access so the dead endpoint stops being advertised, and fix the two
`hestiactl` defaults.

---

## 2026-08-11 — DNS layer audit and remediation

Triggered by a question about AdGuard blocking traffic from a phone app. The answer was
boring, the things found underneath it were not.

**The original question, answered.** The blocked domains were analytics, log upload, event
collection and device-fingerprinting endpoints. Every block in the trailing 19 days was a
blocklist rule match, none were threat detections. The app's functional endpoints resolved
normally throughout, and the blocked hosts were re-queried at a low steady rate rather than
in retry storms, which is what a genuinely broken dependency looks like. No action needed.

**Three real gaps found in the DNS layer.**

1. *Blocks cannot mean "malware" on this deployment.* SafeBrowsing and parental filtering
   are both off, so every block is a list match by construction. Deliberately not enabling
   SafeBrowsing: it sends a hash prefix of every domain to a third party, which breaks
   invariant 5. The gap should close with local lists instead.
2. *No per-device attribution.* The router forwards DNS on behalf of clients, so every
   query in the log carries the router's address. Questions of the form "which device did
   this" are unanswerable as configured. The router's firmware exposes no LAN DHCP DNS
   option, so the only routes are per-device manual DNS or moving DHCP to the resolver.
3. *DNS-over-HTTPS bypass is open.* A client is actively bootstrapping encrypted DNS to a
   third-party resolver, 850 lookups in the window. Any client on DoH is invisible to
   filtering entirely. Unresolved.

**Incident, caused and fixed in-session.** Pointing the router's secondary resolver at
AdGuard unmasked a latent reverse-DNS loop: private PTR resolution was configured to use the
host's system resolver, which is the router, which now forwarded back to AdGuard. Each
lookup hung for two seconds and drained the worker pool until real queries timed out. The
external secondary had been the accidental escape hatch. Compounding it, the per-client rate
limit was being applied to the whole household, because the household presents as one
client. Both settings corrected via the AdGuard UI, resolution and filtering verified.

Lesson recorded: read a service's own upstream and PTR configuration before changing what
points at it.

**Correction.** The change was recommended on the grounds that the external secondary was
leaking a share of household browsing unfiltered. That was asserted from general resolver
behavior and never verified. Measured afterwards, steady-state query rate returned to its
prior baseline rather than stepping up, which is not what a real leak produces. The claim is
withdrawn. Pointing both entries at the local resolver is still correct, but for the smaller
reason of removing ambiguity. Note the vantage problem: the resolver cannot observe queries
that never reach it, so this remains weakly evidenced in both directions.

### Done same night: the watchdog gap

The off-site watchdog now runs three independently-tracked probes instead of one, on the
existing 5 minute timer and the existing ntfy channel:

| Probe | Catches | Priority |
|---|---|---|
| `brain` | house dark, as before | urgent |
| `dns` | resolver up but not resolving, the exact failure from tonight | urgent |
| `dns-filtering` | resolution fine, blocklists silently not applied | high |

Each keeps its own state, so one cannot mask another. The filtering check is skipped when
resolution is down, because a dead resolver answers nothing for everything and would
otherwise read as a false pass. Both failure and recovery transitions were fired against a
throwaway ntfy topic and a scratch state directory before being trusted, so this is a tested
alert rather than a written one.

Also documented: `SECURITY.md` gained a threat model, and external exposure was measured
rather than assumed. The home perimeter is fully closed; the off-site host exposes only the
ports its firewall intends.

### The root blocker, found last

Attempted to route tailnet DNS at the resolver to finally get per-device attribution. It
does not work, and the reason explains an earlier dead end too.

The resolver runs on a Docker **bridge** network. Queries arriving over the tailnet are
source-NAT'd to the bridge gateway, so every tailnet device logs as one indistinguishable
client. Proven by timing: the off-site watchdog fired at 00:32:56 UTC from its own tailnet
address, and the resolver recorded that exact query pair as the gateway address. LAN queries
are unaffected, because they arrive through published-port DNAT and keep their source.

This is the same reason the resolver cannot act as a DHCP server. One change fixes both:
host or macvlan networking, which first requires dealing with the host's stub resolver on
port 53. That is the unlock, and it is a daylight job.

What still works today: setting a LAN device's resolver manually. One device on the network
has been doing this all evening and shows up correctly attributed, which is the proof.

Two methodology notes, both of which cost real time tonight:

- The on-disk query log buffers in memory and can sit byte-identical for an hour at low query
  rates, while the web UI shows current data. Do not diagnose a stuck logger from the file.
  Nearly restarted a healthy container over this.
- `tailscale set --accept-dns=false` also disables MagicDNS. Applying it to the off-site box
  silently broke hostname resolution for the nightly backup pull, which was compensated with
  a static hosts entry. That entry is the better arrangement anyway, since it survives DNS
  and coordination-server problems entirely.

### In flight

- DoH bypass unaddressed. A client is using encrypted DNS to a third-party resolver, which
  no amount of local filtering or logging can see.
- Threat coverage still rests on a single aggregated feed.
- Per-device DNS attribution still missing, so the new probes can say the house is broken
  but not which device is misbehaving.
- On the off-site host, the panel database listens on all interfaces and is not exposed only
  because the firewall says so. It should bind to loopback.

### Next concrete action

Move the resolver to host networking. It is the single change that unlocks per-device
attribution and the DHCP option together, and everything else on this list is worth less
until it lands. Deal with the host stub resolver on port 53 first, recreate the container,
verify resolution and filtering before walking away.

Then: a full-day query-volume comparison against the trailing baseline to settle
the leak question properly `[non-production]`; local phishing and malware-distribution
lists, added one at a time so false positives are attributable; and `dns-watch`, a
timer-driven deterministic feed into the existing ntfy channel and `snapshot()`. `dns-watch`
is worth more after per-device DNS lands, so it is sequenced last. Detection stays in
timers and row comparisons, never the model.

### Non-production, queued

- `[non-production]` Per-device DNS on the machines worth attributing. Router cannot hand
  it out.
- `[non-production]` Passwordless sudo across the boxes. Scoped NOPASSWD for container and
  service control is the lighter option.


## 2026-09-21: Voice satellite connection restored

Corrected a stale ESPHome host address after a DHCP lease change. Backed up the
Home Assistant configuration before the edit and restarted Home Assistant. The
satellite returned to `idle`; the local web endpoint returned HTTP 200. No
application code changed.

Next action: `[non-production]` At 12:00, verify the router DHCP reservation and
try a spoken request. Operator actions are recorded in the central queue.

Operator confirmed the router DHCP reservation is now saved. The remaining
`[non-production]` action is a spoken request at 12:00 to confirm end-to-end voice.
