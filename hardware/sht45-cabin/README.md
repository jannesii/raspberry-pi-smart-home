# SHT45 cabin holder — measurement-gated draft

Parametric PETG housing for the ordered **Adafruit SHT45 PTFE 6174**.
Implements [ticket #21](https://github.com/jannesii/raspberry-pi-smart-home/issues/21)
and [spec #19](https://github.com/jannesii/raspberry-pi-smart-home/issues/19).
All STEP/STL exports are **DRAFT**, not final-fit files. Physical receipt and
fit remain gates under #11. No server/firmware behavior changes.

## Air-sensing design

A separate flat taped console adapter supports a mechanically removable housing.
Four adapter bosses place the housing underside 8 mm above the adapter underside,
leaving 6 mm of open air above its 2 mm plate. The PCB backside is a further
10 mm above the housing underside: **18 mm above the tape plane**. Coordinates
use PCB-centred XY, +Z toward cabin, adapter underside Z=0. The installed envelope
is **80 × 40 × 28 mm**, excluding screws, tape, board and cables.

A 28 × 20 mm opening passes through both structural plates. The cover has a
16 × 12 mm unobstructed central air window over the candidate membrane/tongue,
8 × 19.6 mm and 8 × 20 mm end windows, and two 15.6 × 5.6 mm side windows. Their combined
683.52 mm² area is about 39% of the rectangular roof envelope (before screw
holes). Side shields protect the board edges from incidental knocks; the area
below them remains open to cross-flow. Cover ribs shield portions of the board
and connector region without creating a closed air pocket. Openings cannot
exclude every object; this is not an impact-rated or sealed housing.

Four 4 mm support posts bear on candidate hole lands. Short **1.8 mm locating
pins**, 0.8 mm high, enter candidate 2.5 mm holes. These are printed pins, not
fasteners. Four removable retainers put small 3 × 0.7 mm pads at X=±10.16,
Y=±8 mm on candidate clear top lands. A nominal 0.1 mm axial gap avoids using
screw closure to force the board down. Pad clearance and positive retention
must be trial-fitted, not inferred from the gap. Cover and retainers seat on
structural bosses. No BT3 screw passes through the PCB. The PCB, connectors,
sensor tongue and PTFE membrane remain intact.

Two compact 8 × 18 mm clamps sit at X=±33 mm outside the cover. Each has two
BT3 screws and a nominal 6 mm wide × 3 mm high cable corridor. They must grip
insulated cable only, with slack at both mated plugs. Clamp screws remain
accessible with the cover fitted. Eight millimetres of plug reach beyond each
candidate board end are reserved; below-plug transition corridors are open.
The model does **not** simulate a swept flexible cable. Straight plug exits,
downward routing to the lower clamps and bends beyond the housing require
inspection and a trial. Change routing or clamp location if relaxed bends
cannot fit; never pull a plug into position using the clamp.

Initial site: **driver-facing upper side of the centre console below the centre
stack near the forward gear lever**, away from the documented passenger-footwell
heater. This is provisional: local airflow, shifter/hand clearance, trim shape
and mounting surface are unverified. Keep shaded and away from heat sources.
This mount senses cabin air independently of the TMP117 glass-holder geometry.
Do not put foam, paste or adhesive at the membrane.

## Nominal facts, allowances and unknowns

[Adafruit's 6174 product page](https://www.adafruit.com/product/6174) lists
25.5 × 17.7 × 4.8 mm assembled module dimensions (rechecked 2026-10-08).
**4.8 mm is not PCB thickness.** Candidate board geometry is retained in
[src/lib/board-layout.json](src/lib/board-layout.json): the pinned manufacturer
SHT45 Eagle layout identifies **5665, not explicitly 6174**. It supplies only a
candidate 25.4 × 17.78 mm outline, 20.32 × 12.7 mm hole pitch, 2.5 mm drills and
opposed connectors. No generic 5665 STEP is presented as a confirmed 6174 assembly.
The recorded prior step.parts search found no SHT45 model; no vendor substitute
has been added. Nothing is dimensioned by scaling photographs.

Every other default in [Parameters](src/lib/geometry.py) is a design allowance,
including the 1.6 mm **draft-only** PCB thickness used to position the retainer
finger. All `*_measured` fields remain `None`. The installed STEP contains only
printed parts, with no invented board, cable, screw, membrane or tape stand-ins.

For final-fit release, record the delivered revision and the following evidence:

| Evidence | Release parameters / action |
| --- | --- |
| Outline, hole pitches/drills, revision and connector orientation | Five board/hole `*_measured` dimensions; update nominal parameters to agree within 0.05 mm; `received_revision_confirmed` includes rounded corners, pin seating, tongue and connector comparison |
| Actual PCB thickness | `pcb_thickness_measured`; changes retainer finger depth independently of cover stops |
| Backside protrusions and component top above PCB backside | `backside_protrusion_measured`, `component_top_above_back_measured`; preserve ≥0.6 mm structural clearance |
| Safe bearing lands | `bottom_land_radius_measured` ≥2 mm; confirm all four bottom contacts and top pad rectangles at X=±10.16/Y=±8, plus fences/pins, clear of solder/components/traces; `bearing_lands_confirmed` |
| PTFE footprint, centre and top above backside | `membrane_{x,y,width,depth}_measured`, `membrane_top_above_back_measured`; keep ≥0.6 mm margin inside the 16 × 12 air column |
| Both fully mated plug envelopes | `plug_{reach,width,height}_measured` and signed `plug_lowest_above_back_measured`; use the larger envelope across both ports; trial actual plug/latch extraction |
| Cable insulation and relaxed bends | `cable_diameter_measured`, `bend_radius_measured`, `free_exit_measured`; resize clamp gap to the actual cable and prove grip; external exit clearance ≥bend radius +2 mm does not prove the internal transition bend |
| Tape and actual trim dimensions | `tape_thickness_measured`, `trim_{width,length}_measured`; record trim curvature and test seating, screw/tool space, removal and airflow |
| Fastener and driver | `screw_length_measured` (draft joints use BT3x6), `screw_head_radius_measured` 1.9–3.5 mm, `tool_diameter_measured` ≤6 mm, `printed_pilot_measured` 2.45–2.65 mm plus trial retention |
| Physical trials | `trim_airflow_clearance_confirmed`, `retention_clamps_service_trial_passed`, `tape_attachment_removal_trial_passed` |

Only then set `ready_for_final_print=True`. Generation rejects missing,
nonfinite, inconsistent or out-of-allowance measurements. Confirmation flags
require recorded evidence, not merely successful CAD generation. Revise geometry
and checks when a received interface exceeds an allowance; the gate cannot
establish the truth of entered values. Test fixtures are explicitly synthetic.
Physical fit, sensor bias/lag and bus reliability are separate evidence stages.

## Assembly and service

1. Print/calibrate coupons first. Measure both faces of the delivered board,
   all four lands, plugs, relaxed cables, screw heads/tips and tape/trim.
   Trial pilots away from electronics and remove all swarf/burrs.
2. Apply available double-sided tape to two **64 × 7 mm** underside zones,
   centred at Y=±14.5 mm. Tape area is 896 mm². Keep the central opening and
   screws clear. Validate tape/trim adhesion, flat seating and clean removal;
   no year-round performance or nondamaging removal is claimed.
3. Seat the housing on the four adapter bosses. The 4 mm spigots enter 4.4 mm
   diameter, 1 mm deep underside recesses: 0.2 mm radial and axial allowances.
   Use four BT3x6 through the housing into the adapter. Cover removal gives
   full driver access. Adapter screws/tape remain outside the PCB footprint.
4. Lower the inspected board onto the locating pins/supports, component-side
   toward cabin. Fit four retainers with BT3x6, seating against their bosses.
   Confirm free membrane/tongue, normal header/jumper service and no board
   bending. SHT45 needs no address modification for the provisional shared bus;
   cover removal exposes board service, and the board lifts out for solder work.
5. Mate both cables for the provisional ESP32 → SHT45 → reference TMP117 →
   lower-band TMP117 route. Route relaxed slack through the open transition
   space, fit each clamp using two BT3x6, and confirm grip without insulation
   damage. Anchor heavier extensions to the vehicle separately. No installed
   I²C reliability claim is made.
6. Fit the ventilated cover using four BT3x6, seating on its structural stops.
   Check screw heads and tools remain clear of board/components/membrane.

All source pilots are **2.55 mm**; through holes are **3.4 mm**. Draft adapter,
cover and retainer screws have 4 mm engagement in 5 mm blind pilots; clamp
screws have 2 mm engagement in 3 mm blind pilots. Each has 1 mm bottom margin.
Total: **16 BT3x6 screws**. Longer stocked screws need redesigned joint stacks
and checks; never substitute blindly. BT3x5 and tapped M2 PCB joints are not
required. Repeated service and PETG thread wear/creep remain physical checks.

For board service: loosen/remove clamps as necessary, release cable plugs,
remove the cover, then remove all four retainers and lift the PCB along +Z.
The first 0.8 mm lift clears locating pins. Leave the adapter taped in place.
Housing service additionally releases its four adapter screws; the spigots are
straight lift-off features. Relocation requires a separately validated tape
removal/replacement procedure.

## Coupons, print orientations and evidence

Print PETG on **Bambu A1, 0.4 mm**, starting with 0.2 mm layers and four walls.
All STLs are saved at Z=0 in their intended orientation; do not auto-orient.

| STL / quantity | Bed face / use |
| --- | --- |
| `adapter` ×1, `housing` ×1 | Underside on bed; posts/pins grow upward |
| `cover` ×1 | Exterior roof on bed; side shields upward |
| `retainer` ×4 | Flat top on bed; finger upward |
| `cable_clamp` ×2 | Flat top on bed; feet upward |
| `pilot_coupon` ×1 | Flat base down; left/centre/right bores 2.45/2.55/2.65 mm |
| `fit_coupon` ×1 | Housing underside down; reduced-height board/two-plug coupon |
| `adapter_coupon` ×1 + `adapter_socket_coupon` ×1 | Bases down; pair tests locating spigot/recess and BT3x6 joint |

Use a retainer and clamp with the pilot coupon to test clearance holes, head
seating and thread engagement. Inspect/measure the centre 2.55 mm pilot, insert
BT3x6 through the actual mating part, check remaining depth and repeated
removal. Use the board/plug coupon with retainers and both clamps to inspect
all lands, pins, plug removal, insulation grip and actual transition bends.
Pair the adapter coupons with BT3x6; measure lateral play, seating, recess
bridging fidelity and several lift/reseat cycles. Record print settings,
dimensions, screw type, insertion observations and photographs for the later
owner measurement ticket. Never tighten screws to compensate for a bad fit.

Vertical features and top-down service parts minimize supports. Housing and
socket coupons have **0.5 mm radial recess ledges at Z=1 mm**; these small
unsupported shoulders need slicer/print inspection. See
[validation report](reports/validation.md) for actual evidence and limits.
No material temperature qualification is required or claimed.

## Reproduce

Run from this directory; CAD dependencies stay separate from server dependencies.

```sh
CAD_RUNTIME='cadgen==0.7.17'
for model in assembly pilot_coupon fit_coupon adapter_coupon adapter_socket_coupon; do
  uvx --no-config --managed-python --python 3.13 --from "$CAD_RUNTIME" python "src/$model.py"
done
uvx --no-config --managed-python --python 3.13 --from "$CAD_RUNTIME" python checks/check_redesign.py
uvx --no-config --managed-python --python 3.13 --from "$CAD_RUNTIME" python checks/check_geometry.py
uv run --no-project --with trimesh --with numpy --with scipy python checks/check_mesh.py
uvx --no-config --managed-python --python 3.13 --from "$CAD_RUNTIME" cadgen step snapshot --job checks/views.json
```

Assembly calls its maintained child models; coupon entrypoints generate the
remaining outputs. Relative STL chord tolerance is 0.0002, not 0.0002 mm.
Saved artifact checks use 1e-6 mm³ interference tolerance, 0.001 mm bounding
agreement and <0.2% mesh/exact volume error. Edit source, regenerate, rerun all
checks and visually inspect snapshots after any geometry change. The model
catalog is [src/README.md](src/README.md); exported exact files live in
[STEP/](STEP/) and individual print meshes in [STL/](STL/).
