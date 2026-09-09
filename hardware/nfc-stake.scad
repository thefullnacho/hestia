// Hestia NFC garden stake: a named marker with an NFC tag sealed inside it.
//
// A stuck-on sticker is the thing that fails outdoors. The antenna is aluminium on
// thin film, so once water gets under the edge it corrodes and the tag goes dead
// silently, which is the worst failure mode for a capture path whose whole job is to
// be reliable. Printing the pocket and capping it puts plastic on both faces instead.
//
// Prints flat on its back: text and pocket both face up, so there is no bridging and
// no support anywhere. Set `part` to render one piece at a time for the slicer.
//
// Print in PETG or ASA, not PLA. PLA in full sun goes brittle inside one season and
// these live outside all summer. 4 perimeters, >=25% infill, no other tuning needed.

part = "all";        // "all" | "stake" | "cap"
name  = "BACK FENCE"; // the position, in the words actually said out loud
name2 = "";           // optional second line, so a long name stays readable

width      = 54;   // head width
head       = 66;   // head length, above the taper
length     = 200;  // total, tip included
thickness  = 5;
tag        = 27;   // NFC sticker diameter, 25mm disc plus clearance
pocket     = 1.8;  // pocket depth; leaves 3.2mm of floor under the tag
cap_gap    = 0.35; // press-fit clearance, tuned for a 0.4mm nozzle
text_depth = 0.8;
rib        = 3;    // stiffening rib down the spike, so it can be pushed not hammered

pocket_y = -42;
// Shrink the name until it fits the head, rather than letting a long one run off the edge.
// A second line is the better answer past about ten characters: two lines at 6mm read from
// standing height, one line at 4mm does not.
longest = max(len(name), len(name2));
size = min(10, (width - 8) / max(1, longest * 0.76));

module outline() {
    offset(r = 2, $fn = 24) offset(delta = -2)
        polygon([[-width/2, 0], [width/2, 0], [width/2, -head],
                 [4, -length + 14], [0, -length], [-4, -length + 14], [-width/2, -head]]);
}

module stake() {
    difference() {
        union() {
            linear_extrude(thickness) outline();
            // Rib runs the spike only: the head has to stay flat for the tag pocket.
            translate([0, -length + 12, 0])
                linear_extrude(thickness + rib, scale = [0.25, 1])
                    translate([0, (length - head - 12) / 2])
                        square([7, length - head - 12], center = true);
        }
        translate([0, pocket_y, thickness - pocket])
            cylinder(h = pocket + 1, d = tag, $fn = 64);
    }
    for (line = name2 == "" ? [[name, -16]] : [[name, -11], [name2, -22]])
        translate([0, line[1], thickness])
            linear_extrude(text_depth)
                text(line[0], size = size, halign = "center", valign = "center",
                     font = "Liberation Sans:style=Bold");
}

module cap() {
    // A drop of superglue or clear silicone in the pocket, then press this in.
    cylinder(h = pocket - 0.2, d = tag - cap_gap, $fn = 64);
}

if (part == "all")   { stake(); translate([width, -20, 0]) cap(); }
if (part == "stake")   stake();
if (part == "cap")     cap();
