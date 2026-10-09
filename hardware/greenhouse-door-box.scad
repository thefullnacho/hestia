// Hestia greenhouse door box: an enclosure for the greenhouse-door ESP32 and its DS18B20
// breakout, screwed to the wood framing inside the greenhouse. Wiring and config live in
// deploy/esphome/greenhouse-door.yaml.
//
// It hangs on a vertical face with every entry on the bottom, pointing down. A cable that
// comes in from the side or the top leads water along itself straight to the hole; one that
// leaves downward drips off its own lowest point instead. The lid laps over the top and both
// sides, so water running down the box goes past the joint, and is open along the bottom, so
// the joint drains.
//
// It drains rather than seals. The greenhouse is heated and this box will go through the dew
// point most nights, so it makes its own water whatever the glands do, and a sealed box keeps
// it. There is no gasket for the same reason. The floor falls toward the lid and toward the
// power end, so the 3 mm drain is the lowest point inside rather than one spot on a flat
// floor. A degree and a half of tilt under a locknut does not matter.
//
// The ESP32 is mounted flipped: module toward the back wall on short posts, jumpers rising
// toward the lid. The other way round needs four posts about 31 mm tall to lift the board
// over its own jumpers. A tall thin post prints standing up, so every layer line runs across
// it, and it snaps at the base the first time a jumper is pushed on hard. Flipped, the posts
// are 6 mm, the inside depth still follows from the 34.03 mm stack, and the jumpers face you
// when the lid comes off, which is where you want them when one works loose. EN and BOOT end
// up against the wall; OTA works, so nothing needs them.
//
// The power cable has a plug on both ends and neither fits a PG7, so it comes up through a
// slot the USB-C plug fits through, and a printed clamp pins the cable to a pad on the back
// wall. The clamp's V-groove grips anything from about 3 to 5 mm and never bottoms out on its
// bosses, so the screws keep pulling until the cable is held, whatever it measures.
//
// Modelled as it hangs: x across, y out from the wall toward the lid, z up. `part` turns each
// piece onto its print face. "all" shows the assembly with the boards and glands as ghosts,
// to check the fit after changing a number.
//
// Print in PETG, not PLA. A greenhouse in summer gets warm enough for PLA to creep, and screw
// bosses are the first thing to let go. No supports anywhere:
//   box:   back on the bed, open side up. The gland and drain holes are teardrops with the
//          point cut flat, so they print without support; the locknut covers the flat.
//   lid:   outside face on the bed, skirt up.
//   clamp: flat on its back, V-groove up.
// 0.2 mm layers, 4 perimeters (the 3 mm walls come out near solid and every boss gets a full
// shell), 20% infill. No brim: each part has a big flat face on the bed.
//
// Hardware: 4 PG7 glands (3 if spare_gland is off). M3 self-tapping (ST2.9) x 12: 4 for the
// lid, 2 for the clamp. M2 self-tapping (ST2.2) x 6: 2 or 4 for the ESP32, 2 for the breakout.
// 4 #8 wood screws, pan or washer head.

part        = "all";  // "all" | "box" | "lid" | "clamp"
spare_gland = true;   // fourth PG7 hole, for a second DS18B20 on the same 1-wire bus later
lid_inserts = false;  // true cuts the lid bosses for M3 heat-set inserts instead of self-tappers
explode     = 30;     // "all" only: how far the lid stands off, so the inside shows

wall     = 3;    // every wall, and the floor at its thinnest
corner_r = 3;    // outside corners, seen from the front
fall_y   = 1;    // the floor drops this much from the back wall to the lid
fall_x   = 2;    // and this much from the probe end to the power end, where the drain is
drain_d  = 3;
td_cap   = 0.6;  // how far past the circle a teardrop hole's flat top sits

// ESP32: Elegoo, 30-pin DOIT layout, USB-C on one end. Measured with calipers 2026-10-09.
board       = [51.6, 28.15]; // length, width
board_holes = [46.9, 23.67]; // 3.0 mm corner holes, centre to centre
stack       = 34.03; // top of the module to the lowest point of the bent jumpers
module_h    = 3.2;   // MEASURE: module top above the PCB. Not measured; a WROOM-32 is 3.1
pcb_t       = 1.6;
standoff    = 6;     // back wall to the PCB, module side. Clears the module and the USB plug
standoff_d  = 4.6;   // post top. At the module end the module's edge is 2.8 from the hole
usb_rise    = 1.6;   // MEASURE if the plug rubs the back wall: USB-C centre above the PCB
usb_clear   = 35;    // board's USB end to where the cable turns straight down: plug, boot and
                     // bend. MEASURE the plug you will use; a long strain-relief boot needs more
lid_clear   = 4;     // bent jumpers to the lid
wire_room   = 10;    // gland threads to the board, for the leads to turn up into the jumpers

// DS18B20 breakout, 4.7k pull-up on board. 22.39 x 20.17, 10.9 tall with terminal and jumpers.
bo_edge    = 22.39;  // MEASURE: length of the edge carrying the header and both holes. If the
                     // holes are on the 20.17 side, set 20.17 and the posts close to 15.67 apart
bo_other   = 22.39 + 20.17 - bo_edge;
bo_hole_in = 2.25;   // hole centres in from the edges
bo_stack   = 10.9;
bo_post_h  = 5;      // clears the solder joints underneath

// PG7 glands. Measured 2026-10-09.
gland_thread = 12.4;
hole_allow   = 0.4;  // printed holes come out small; raise it if the thread will not start
gland_hole   = gland_thread + hole_allow;
thread_len   = 14;
nut_ac       = 17.76; // locknut across corners (15.38 across flats)
nut_t        = 5;     // MEASURE: locknut thickness, not measured. The thread check uses it
washer_t     = 2;     // sealing washer under the gland's flange, outside
finger       = 4;     // locknut to locknut, and locknut to anything else: a spanner jaw
gland_cap_d  = 15.47;

// Power. The USB-C overmold is 10.09 x 5.5 (11.5 across the diagonal).
plug        = [10.09, 5.5];
plug_fit    = 0.6;   // slot clearance, each side
cable_d     = 4.0;   // MEASURE: not measured. The clamp copes with about 3 to 5
clamp_pitch = 20;    // clamp screw centres; the plug passes up between the bosses
clamp_w     = 9;     // along the cable
bar_t       = 4;
tooth_h     = 5;
v_depth     = 2.8;   // deep enough to grip 3 to 5 mm without the tooth landing on the pad
squeeze     = 0.4;

// Lid
lid_t     = 3;
skirt     = 5;       // how far the lid laps back over the top and sides
skirt_t   = 1.6;
skirt_gap = 0.4;
boss_in   = 4.5;     // lid screw centres, in from each inside wall
boss_d    = 9;       // 2 x boss_in, so each boss meets both walls
m3_pilot  = 2.5;     // M3 self-tapping (ST2.9): lid and clamp
m3_clear  = 3.4;
insert_d  = 4.0;     // MEASURE: the hole your heat-set inserts' maker asks for
insert_h  = 6;
m2_pilot  = 1.7;     // M2 self-tapping (ST2.2): both boards

// Mounting
ear_hole  = 4.5;     // #8 wood screw clearance
ear_w     = 14;
ear_t     = 5;
ear_reach = 7;       // box face to the ear hole; clears a #8 pan head
// The ears sit over the gaps between glands, not at a round spacing: anywhere else, a fitted
// gland is in the way of a straight driver to the bottom ears. They come out about 50 apart,
// which lands all four on a 2x4's 89 mm face.

$fn = 48;

// ---------------------------------------------------------------------------------------
// Everything below follows from the numbers above.

D_i   = standoff - module_h + stack + lid_clear;  // inside depth
D_b   = wall + D_i;                               // back face to the rim
reach = wall + boss_in + boss_d/2;                // how far a corner boss comes in
cab_y = wall + standoff - usb_rise;               // power cable, out from the back face
pad_y = cab_y - cable_d/2;                        // face of the clamp pad
slot  = [plug[0] + 2*plug_fit, plug[1] + 2*plug_fit];
cab_x = reach + 0.5 + slot[0]/2;                  // power cable, clear of the corner boss
clamp_z   = reach + 1 + clamp_w/2;
gland_top = thread_len - washer_t;                // thread tip, above the bottom face
board_x   = cab_x + usb_clear;
board_z   = gland_top + wire_room;
bo_z      = gland_top + 8;

g0     = cab_x + slot[0]/2 + finger + nut_ac/2;   // first gland centre
band_w = board_x + board[0] + 4 + bo_edge + 3 + wall;
row_w  = g0 + 3*(nut_ac + finger) + nut_ac/2 + finger + reach;
W_o    = max(band_w, row_w);
H_o    = max(board_z + board[1], bo_z + bo_other) + 4 + wall;
W_i    = W_o - 2*wall;

g3      = W_o - reach - finger - nut_ac/2;         // last gland centre
g_pitch = (g3 - g0)/3;
gland_y = D_b/2;
// Reed, reed, spare, probe. The probe and the spare end up nearest the breakout's terminal.
glands  = [for (i = [0:3]) if (spare_gland || i != 2) g0 + i*g_pitch];
bo_x    = W_o - wall - 3 - bo_edge;

function floor_z(x, y) = wall + fall_x*(x - wall)/W_i + fall_y*(D_b - y)/D_i;
function nut_seat(x) = floor_z(x + nut_ac/2, gland_y - nut_ac/2);  // highest floor under a nut
drain = [reach + 0.5 + drain_d/2, D_b - drain_d/2 - 1];

// Where the V's point sits when it rests on a cable of diameter d lying on the pad.
function v_apex(d) = pad_y + d/2*(1 + sqrt(2));
boss_top = v_apex(cable_d - 1) - v_depth - squeeze + tooth_h - 0.5; // under the bar even at -1 mm
bar_y    = v_apex(cable_d) - v_depth - squeeze + tooth_h + bar_t;   // bar's outer face, nominal

lid_screws = [for (x = [wall + boss_in, W_o - wall - boss_in],
                   z = [wall + boss_in, H_o - wall - boss_in]) [x, z]];
board_c    = [board_x + board[0]/2, board_z + board[1]/2];
board_pts  = [for (sx = [-1, 1], sz = [-1, 1])
                 [board_c[0] + sx*board_holes[0]/2, board_c[1] + sz*board_holes[1]/2]];
bo_pts     = [for (x = [bo_x + bo_hole_in, bo_x + bo_edge - bo_hole_in])
                 [x, bo_z + bo_other - bo_hole_in]];
ear_pts    = [for (x = [g0 + g_pitch/2, g0 + 2.5*g_pitch],
                   z = [-ear_reach, H_o + ear_reach]) [x, z]];

function r1(v) = round(v*10)/10;

thread_need = max([for (x = glands) nut_seat(x)]) + nut_t + washer_t;
echo(str("PG7 thread: floor + locknut + washer = ", r1(thread_need), " of ", thread_len,
         " mm, ", r1(thread_len - thread_need), " spare"));
assert(thread_need <= thread_len,
       "PG7 thread too short for this floor + locknut + washer: thin the wall or the fall");
assert(standoff - module_h >= 0.5, "the module would touch the back wall: raise standoff");
assert(standoff - usb_rise - plug[1]/2 >= 0.5, "the USB plug would rub the back wall");
assert(pad_y >= wall, "cable_d too fat for this standoff: no room for the clamp pad");
assert(g_pitch >= nut_ac + finger, "glands too close: locknuts will not clear");
assert(v_apex(cable_d - 1) - v_depth - squeeze > pad_y, "V too deep: the tooth lands on the pad");
if (g3 < bo_x || g3 > bo_x + bo_edge)
    echo("WARNING: the probe gland is no longer under the breakout");
echo(str("Box: ", r1(W_o), " wide x ", r1(H_o), " tall x ", r1(D_b + lid_t),
         " deep with the lid. Ears add ", ear_reach + ear_w/2, " top and bottom"));
echo(str("Gland pitch ", r1(g_pitch), ", first locknut clears the next by ",
         r1(g_pitch - nut_ac), " mm. Ears ", r1(2*g_pitch), " apart"));

// ---------------------------------------------------------------------------------------

// 2D in the x-z plane (the face you see from the front), extruded out from the wall.
module xz(y0, h) translate([0, y0 + h, 0]) rotate([90, 0, 0]) linear_extrude(h) children();
// Child's +z points out from the wall, at p = [x, z].
module at_y(p, y0) translate([p[0], y0, p[1]]) rotate([-90, 0, 0]) children();

module outline(grow = 0) offset(r = corner_r + grow) offset(delta = -corner_r) square([W_o, H_o]);

// Through the floor, point toward the lid: that is up on the printer.
module teardrop(d, h) linear_extrude(h) intersection() {
    union() { circle(d = d, $fn = 64); rotate(45) square(d/2); }
    translate([-d, -d]) square([2*d, d + d/2 + td_cap]);
}

module cavity() {
    x0 = wall; x1 = W_o - wall; y0 = wall; y1 = D_b + 1; z1 = H_o - wall;
    polyhedron(
        [[x0, y0, floor_z(x0, y0)], [x1, y0, floor_z(x1, y0)],
         [x1, y1, floor_z(x1, y1)], [x0, y1, floor_z(x0, y1)],
         [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
        [[0, 1, 2, 3], [4, 5, 1, 0], [7, 6, 5, 4], [5, 6, 2, 1], [6, 7, 3, 2], [7, 4, 0, 3]]);
}

module ears() for (p = ear_pts) xz(0, ear_t) hull() {
    translate(p) circle(d = ear_w);
    translate([p[0] - ear_w/2, p[1] < 0 ? 0 : H_o - 1]) square([ear_w, 1]);
}

module inside() {
    // Lid screw bosses, full depth and merged into the corners.
    for (p = lid_screws) xz(wall - 0.01, D_i + 0.01) hull() {
        corner = [p[0] < W_o/2 ? wall : W_o - wall, p[1] < H_o/2 ? wall : H_o - wall];
        translate(p) circle(d = boss_d);
        translate([min(p[0], corner[0]), min(p[1], corner[1])]) square(boss_in);
    }
    // ESP32 posts: narrow where they pass the module, flared at the root.
    for (p = board_pts) at_y(p, wall - 0.01) {
        cylinder(d1 = standoff_d + 3, d2 = standoff_d, h = 2.5);
        cylinder(d = standoff_d, h = standoff + 0.01);
    }
    // Breakout posts on the header edge, plus two rests under the terminal edge, which is
    // where the screwdriver pushes. Corners only, clear of the terminal's pins.
    for (p = bo_pts) at_y(p, wall - 0.01) cylinder(d = 5, h = bo_post_h + 0.01);
    for (x = [bo_x + 0.5, bo_x + bo_edge - 3.5])
        translate([x, wall - 0.01, bo_z + 0.5]) cube([3, bo_post_h + 0.01, 3]);
    // Clamp seat: a pad the cable lies on, and two bosses kept short of the bar.
    translate([cab_x - 4.5, wall - 0.01, clamp_z - clamp_w/2])
        cube([9, pad_y - wall + 0.01, clamp_w]);
    for (s = [-1, 1]) at_y([cab_x + s*clamp_pitch/2, clamp_z], wall - 0.01)
        cylinder(d = 7, h = boss_top - wall + 0.01);
}

module holes() {
    through = wall + fall_x + fall_y + 2;
    for (x = glands) translate([x, gland_y, -1]) teardrop(gland_hole, through);
    translate([cab_x - slot[0]/2, pad_y, -1]) cube([slot[0], slot[1], through]);
    translate([drain[0], drain[1], -1]) teardrop(drain_d, through);
    for (p = ear_pts) at_y(p, -1) cylinder(d = ear_hole, h = ear_t + 2);
    for (p = lid_screws)
        at_y(p, D_b - (lid_inserts ? insert_h : 12))
            cylinder(d = lid_inserts ? insert_d : m3_pilot, h = 13);
    // Pilots stop 1.5 short of the back face, so no screw tip reaches the wood.
    for (p = board_pts) at_y(p, wall - 1.5) cylinder(d = m2_pilot, h = standoff + 2);
    for (p = bo_pts)    at_y(p, wall - 1.5) cylinder(d = m2_pilot, h = bo_post_h + 2);
    for (s = [-1, 1]) at_y([cab_x + s*clamp_pitch/2, clamp_z], 1.5)
        cylinder(d = m3_pilot, h = boss_top);
}

module box() difference() {
    union() {
        difference() { xz(0, D_b) outline(); cavity(); }
        ears();
        inside();
    }
    holes();
}

module lid_shape() intersection() {
    outline(skirt_gap + skirt_t);
    translate([-10, 0]) square([W_o + 20, H_o + 10]);   // open along the bottom
}

module lid() difference() {
    union() {
        xz(D_b, lid_t) lid_shape();
        xz(D_b - skirt, skirt + 0.01) difference() { lid_shape(); outline(skirt_gap); }
    }
    for (p = lid_screws) at_y(p, D_b - 1) cylinder(d = m3_clear, h = lid_t + 2);
}

// Modelled flat on its back as it prints: x across, y along the cable, z toward the wall.
module clamp() difference() {
    union() {
        linear_extrude(bar_t) offset(r = 2) square([clamp_pitch + 3, clamp_w - 4], center = true);
        translate([-4.5, -clamp_w/2, 0]) cube([9, clamp_w, bar_t + tooth_h]);
    }
    translate([0, 0, bar_t + tooth_h]) rotate([90, 0, 0])
        linear_extrude(clamp_w + 2, center = true)
            rotate(45) square(v_depth*sqrt(2), center = true);
    for (s = [-1, 1]) translate([s*clamp_pitch/2, 0, -1]) cylinder(d = m3_clear, h = bar_t + 2);
}

// Stand-ins for what goes inside, for checking the fit. Never printed.
module ghosts() {
    py = wall + standoff;
    color("seagreen") translate([board_x, py, board_z]) cube([board[0], pcb_t, board[1]]);
    color("silver") translate([board_x + board[0] - 25.5, py - module_h, board_c[1] - 9])
        cube([25.5, module_h, 18]);
    color("orange", 0.35) translate([board_x, py + pcb_t, board_z])
        cube([board[0], stack - module_h - pcb_t, board[1]]);
    color("dimgray") translate([board_x - 25, cab_y - plug[1]/2, board_c[1] - plug[0]/2])
        cube([25, plug[1], plug[0]]);
    color("dimgray") translate([cab_x, cab_y, -25])
        cylinder(d = cable_d, h = clamp_z + clamp_w/2 + 29);
    color("royalblue") translate([bo_x, wall + bo_post_h, bo_z]) cube([bo_edge, pcb_t, bo_other]);
    color("orange", 0.35) translate([bo_x, wall + bo_post_h + pcb_t, bo_z])
        cube([bo_edge, bo_stack - pcb_t, bo_other]);
    for (x = glands) {
        color("gray") translate([x, gland_y, nut_seat(x)]) cylinder(d = nut_ac, h = nut_t, $fn = 6);
        color("gray") translate([x, gland_y, -washer_t]) cylinder(d = gland_thread, h = thread_len);
        color("dimgray") translate([x, gland_y, -washer_t - 22]) cylinder(d = gland_cap_d, h = 22);
    }
}

if (part == "all") {
    box();
    translate([0, explode, 0]) lid();
    translate([cab_x, bar_y, clamp_z]) rotate([90, 0, 0]) clamp();
    ghosts();
}
if (part == "box")   translate([0, H_o + ear_reach + ear_w/2, 0]) rotate([90, 0, 0]) box();
if (part == "lid")   translate([0, 0, D_b + lid_t]) rotate([-90, 0, 0]) lid();
if (part == "clamp") clamp();
