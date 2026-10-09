# Hardware

Printable parts for the capture paths. Nothing here is required to run Hestia.

## NFC garden stake (`nfc-stake.scad`)

A named ground marker with an NFC tag sealed inside it, for the places a capture tap has
to happen outdoors: where a hose-fed sprinkler gets set down, or a bed that wants its own
tag. Scanning it opens the matching `/nfc` form with the subject already filled in.

The tag goes in a pocket under a printed cap rather than on the surface. A stuck-on
sticker is what fails outside: the antenna is aluminium on thin film, so once water works
under an edge it corrodes and the tag dies quietly, which is the worst failure for a path
whose whole point is that it does not silently no-op.

### Placement

Keep a stake out of your own mowing and trimming paths. This is not tidiness either: on
2026-09-19 a weedeater caught one and shredded the whole top of it, name, tab and all, in
the time it takes to swing a trimmer head sideways. A stake is a thin printed post standing
at string height in exactly the band of a yard that gets cut, and it is invisible from
behind at walking speed.

Put them inside a bed, behind a border, or against something that already stops a trimmer.
If a position genuinely sits in a cut path, the stake is the wrong marker for it.

**What survived is the argument for the pocket.** The tag sits in a recess below the surface
under a printed cap, and everything from the tag down came through untouched and still
scanned. That pocket is there to keep water off an aluminium antenna, not to survive a
trimmer, and it did both. A surface-mounted sticker at that position would have been
confetti, and the failure would have been silent: a tag that no longer reads looks exactly
like a stake nobody has walked out to yet.

So a clipped stake costs a reprint and loses no data. Reprint the post, move the tag across,
and put it somewhere the string does not go.

### Printing

Prints flat on its back with the text and pocket facing up, so there is no bridging and
no support. Two parts: the stake and a cap disc.

- **PETG or ASA, not PLA.** PLA in full sun turns brittle within a season and these live
  outdoors all summer.
- 4 perimeters, 25% infill or more. The rib down the spike is there so it can be pushed
  in by hand; it is not a tent peg, do not hammer it.
- Print one before printing the set, and check the pocket against the tags you actually
  bought. **Measure them.** The pocket is cut for a 30mm waterproof disc 1.2mm thick
  (`tag_d`, `tag_h`); a 25mm sticker and a 30mm encapsulated tag are both sold as "NFC
  tag" and the difference is a reprint. If your tag is already waterproof and you would
  rather leave it exposed, set `cap_h = 0` and it sits flush with no cap.
- A brim is worth it. The part is long, thin and tapered, so the tip is a small contact
  patch at the far end of a lot of plastic and it is the first thing to peel. Wiping the
  plate with IPA first matters for the same reason.

### A whole set at once

`make_tags.py` reads a positions file and emits one stake per position plus the URL to
write on each tag, so the name is typed once and ends up as the embossed label, the tag's
`subject`, and the entity in records without a chance to drift.

```sh
python hardware/make_tags.py --positions data/irrigation-positions.json \
    --base-url http://<brain host>:8730 --stl-dir data/stakes --url-file data/stake-urls.txt
```

`--base-url` is written into the tag verbatim, so two things about it are load-bearing.
Include the scheme: a URI record without one is not a link, and the tag will read fine
while nothing opens. Use the tailnet hostname rather than the tailnet IP, because a tag
gets write-protected and an address change would brick the whole set at once.

### What fits on the tag

A stake tag carries only the token and a position slug:

```
http://<brain host>:8730/nfc?token=<token>&p=woodland-edge-guild
```

The brain resolves the slug against the same positions file and fills in subject, source
and sprinkler. That indirection is not tidiness. The spelled-out form reached 157 bytes,
and an NTAG213 has 180 bytes of user memory of which far fewer survive the NDEF container
and record header, so the long URL could not be written at all: the tag reads as writable,
with space to spare, and the write still fails. Measured on these tags 2026-09-13, writing
until it stopped: **137 bytes goes on, 141 does not.** The datasheet number is the wrong
number to design against. The short form is 105 bytes at
its longest. `make_tags.py` refuses to emit anything over `NDEF_SAFE_BYTES`, because the
alternative is finding out while kneeling in a garden bed.

A slug the positions file does not know is a 404 page that says which slug, and logs
nothing. Never a default, never a near match: a run written against the wrong plant is
worse than a run that refuses to be written.

Write the record as **URL/URI**, not Text. A Text record holds the same characters and
does nothing at all when tapped, which looks identical until you tap it.

See `positions.example.json` for the shape. Leave `sprinkler` out for anything not watered
by a sprinkler: a run with no known rate logs its minutes and claims no depth, which beats
inventing inches for a hose laid at the base of a tree. Positions and the URL file both
hold household detail and a live token, so they live under the gitignored `data/`.

The URL file is written owner-only and never printed, because every line contains the NFC
token. The label is uppercased for legibility while the subject keeps the original casing;
entity resolution is case-insensitive, so the two still land on one record.

### Rendering one

```sh
openscad -o back-fence.stl -D 'part="stake"' -D 'name="BACK FENCE"' hardware/nfc-stake.scad
openscad -o cap.stl        -D 'part="cap"'   hardware/nfc-stake.scad
openscad -o cup.stl        -D 'part="cup"'   hardware/nfc-stake.scad
```

### Catch cup

An optional cup that slides onto the tab above the name, so what a position actually
received is measured rather than inferred from a rate sheet. Collected depth equals
applied depth, which is why the sides are straight and there is no funnel: any change in
cross-section with height breaks that equality. Diameter only sets how much water is
caught, not what the depth reads, so it is chosen for a rim that is easy to read.

45mm across and 50mm deep, about 80ml. One 15-minute cycle of the Hi-Rise collects 10.2mm,
so four cycles is 41mm and the cup holds a week of heavy watering plus rain before it needs
emptying. The inside is smooth on purpose: printed graduation rings stringed across the bore
and left whiskers on the outside too. Plunge a ruler to the floor and read the wet line.

Prints opening up. The mount is a vertical through-slot, so nothing needs support. The tab
sits above the text, so a mounted cup never covers the name, and it doubles as a thumb pad
for pushing the stake in. Set `cup_mount = false` to leave it off.

One cup and one cap serve every position, so `make_tags.py` renders them once rather than
eighteen times. Pass `--no-parts` to skip them.

### The cup's second job: rain

A cup catches rain whether or not the sprinkler ran, so the stake's form offers a rain
reading next to the watering one. Depth, no duration, `basis: measured`. It is a separate
event kind from watering on purpose: a watering run is a thing you did, timed, with a depth
derived from a rate sheet, while rain is a thing that happened and the depth is the only
fact in the row. Summing them would inflate every bed's run count and put a measured number
in the same column as an estimated one, which is what the cup exists to stop.

Eighteen cups is eighteen rain gauges, which is the part worth having: the house has no rain
sensor at all (the Ecowitt gateway carries soil moisture only), so these are the only rainfall
numbers the property owns, and the spread between positions is a microclimate reading nothing
else here can produce.

A reading at the brim comes back flagged as a floor rather than a total, because a full cup
and a cup that overflowed twice look identical to a ruler. Anything deeper than the cup is
refused as a bad unit or a mid-storm emptying that should be logged as two readings.

First calibration, 2026-09-13: two cups both read 1.75 in at 13:52 against 1.57 in at KGON
Groton through 13:35, in rain still falling. Agreement within about 11% between a printed cup
and an ASOS station 15 miles off.

Names longer than about ten characters should use both lines, because two lines at 6mm
read from standing height and one line at 4mm does not:

```sh
openscad -o side-yard.stl -D 'part="stake"' -D 'name="SIDE YARD"' -D 'name2="NORTH"' \
  hardware/nfc-stake.scad
```

The name is what gets said out loud about that spot, and it is also the event subject in
records, so keep the two identical.

### Tags

The pocket takes a 30mm round waterproof tag. Seat it on the pocket floor, a drop of
superglue or clear silicone, then press the cap in. Check the diameter on the product page
rather than trusting a photo: bare NTAG213 stickers are usually 25mm and encapsulated
outdoor discs are usually 30mm.

Encode the tag with the URL for that spot, for example
`/nfc?token=...&kind=watering&subject=Back+Fence&source=zone3&sprinkler=hi-rise`. Putting
the source and sprinkler on the tag is what leaves one prefilled field between a scan and
a logged run.

Write-protect the tag after encoding. Note that the URL contains the NFC token, so a
programmed tag is a credential in physical form: it is only as private as the yard it is
standing in, and the brain is reachable on the tailnet only.

## Greenhouse door box (`greenhouse-door-box.scad`)

The enclosure for the greenhouse-door ESP32 and its DS18B20 breakout (wiring and config in
`deploy/esphome/`). It screws to the wood framing inside the greenhouse. The box is 134 mm
across, 57 tall and 47 deep with the lid on; the lid's skirt adds 2 mm around the edge and the
mounting ears add 14 top and bottom.

Every entry is on the bottom face, pointing down, so water on a cable drips off the cable
instead of following it in: two PG7 glands for the reed leads, one for the probe, a spare
beside it for a second DS18B20 on the same 1-wire bus (`spare_gland`), and a slot for the
USB-C power. The power cable has a plug on both ends and neither fits a PG7, so the slot is
sized for the plug to come up through and a printed clamp pins the cable to a pad on the back
wall. Its V-groove is sized from the measured cable and takes half a millimetre either way.

It drains rather than seals. A heated greenhouse takes this box through the dew point most
nights, so it makes its own water whatever the glands do. The floor falls toward the lid and
toward the power end so the 3 mm drain is the lowest point inside, there is no gasket, and the
lid laps over the top and sides but is open along the bottom so the joint drains too.

The ESP32 sits flipped: module toward the back wall on 6 mm posts, jumpers rising toward the
lid. The other way round needs posts about 31 mm tall to clear the jumpers, and a post that
tall prints with every layer line across it and snaps at the base. Flipped, the jumpers face
you when the lid comes off.

### Measurements

Everything that sets the fit was measured with calipers on 2026-10-09: the board and its
jumper stack, the breakout (holes on the 22.39 edge, 15.5 apart), the glands and a 4.9 mm
locknut, a 10.4 mm USB-C plug and a 2.87 mm power cable. `module_h` and `usb_rise` are still
guesses, marked `MEASURE`, with a millimetre of slack around both.

The width comes from the gland row: locknuts 6.5 mm apart at the corners, which is what lets a
6 mm screwdriver shank pass between two fitted glands to the bottom ears. That leaves 32 mm
between the board's end and the cable drop, where the plug and its bend need about 22.

The file refuses to render if floor + locknut + sealing washer outgrows the 14 mm gland thread
(1.5 mm spare as measured), or if a different `cable_d` would leave the clamp unable to grip.
A swapped power cable within half a millimetre of 2.87 needs no reprint.

### Printing and fitting

PETG, no supports. Box on its back with the open side up, lid on its outside face, clamp flat.
0.2 mm layers, 4 perimeters, 20% infill. Roughly 140 g of PETG.

The ears sit over the gaps between glands, so a straight driver still reaches the bottom two
with the glands fitted. The USB-C plug goes up through the slot from below and into the board,
and the clamp goes on last. Until there is a second probe, fill the spare hole with a PG7
blanking plug, or a gland with a stub of 5 mm rod in its seal, rather than leaving a 13 mm
hole open.

```sh
openscad -o box.stl   -D 'part="box"'   hardware/greenhouse-door-box.scad
openscad -o lid.stl   -D 'part="lid"'   hardware/greenhouse-door-box.scad
openscad -o clamp.stl -D 'part="clamp"' hardware/greenhouse-door-box.scad
```

`part="all"` shows the assembly with the boards, glands and plug drawn in, to check the fit
after changing a number. Every render prints the outside size and the thread check.
