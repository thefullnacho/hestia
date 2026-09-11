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
    --base-url https://<brain host> --stl-dir data/stakes --url-file data/stake-urls.txt
```

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
emptying. Rings mark every 5mm off the floor and double up every 10mm; read the water line
against them, or measure with a ruler.

Prints opening up. The mount is a vertical through-slot, so nothing needs support. The tab
sits above the text, so a mounted cup never covers the name, and it doubles as a thumb pad
for pushing the stake in. Set `cup_mount = false` to leave it off.

One cup and one cap serve every position, so `make_tags.py` renders them once rather than
eighteen times. Pass `--no-parts` to skip them.

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
