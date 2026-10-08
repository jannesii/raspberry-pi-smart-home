# SHT45 cabin holder — measurement-gated draft

For Adafruit **product 6174, SHT45 with PTFE membrane**, in the car-heater
project. Related work: [GitHub issue #11](https://github.com/jannesii/raspberry-pi-smart-home/issues/11).
Design basis: [defrost strategy](../../docs/reviews/car-heater-defrost-strategy.md);
project conventions: [TMP117 holder](../tmp117-windshield/README.md).
These exports are **draft geometry, not final-fit print files**. Parts receipt
and physical fitting remain outstanding; this does not complete issue #11.

## Chosen design

One open PETG frame with four corner mounting posts and a right-hand cable
saddle. The PCB mounts component-side outward, with its backside at Z=12 mm
above the holder's tape plane. The base is 2 mm thick, leaving a 10 mm air gap
between base top and PCB backside. Four 4.9 mm diameter posts contact only the
mounting-hole regions; the rest of the PCB has no direct printed contact.
Their long, small cross-sections and nylon rather than metal screws reduce
the intended conduction path. This is a geometric design choice, **not a
measured reduction in thermal bias**: radiation, adhesive, cable conduction,
PCB self-heating and local airflow still matter.

The base aperture is 26.6 × 7.7 mm. A 6.4 × 6.4 mm vertical column around the
candidate central sensor/tongue is free of printed material from below the
base to above the board. There is no lid, sensor cover, foam or printed
material over the membrane. The surrounding base rails sit below the PCB;
they do not enclose it. Keep the sensor face fully exposed to cabin air.

Four removable **M2 nylon screws** pass through the candidate PCB holes into
blind, tapped post holes. Do not use the sensor tongue as a retention point.
The pilot is a trial 1.6 mm diameter × 6 mm depth; the CAD deliberately does
not model threads. Calibrate the printed pilot, drill/ream as needed, then
tap M2 before fitting the PCB. Support the posts while tapping; remove swarf
and deburr before bringing the board near them. Do not drive nylon screws
into unprepared pilots or use tightening to straighten a bent board.
Use only light seating force and check all four contact regions are clear
of components/traces that could be damaged. Nylon thread retention, PETG
creep and vibration resistance require a physical test.

Select screw length **after measuring PCB thickness**: under-head length
minus PCB thickness must give 3.0–5.4 mm engagement, leaving at least 0.6 mm
before the blind bottom. A nominal 6 mm screw is only a trial candidate, not
a verified part selection. The head must span the PCB hole and stay within
the measured clear top land. Remove the four screws and lift the board
straight off to reposition/service it; no snap latch bends the PCB.

Use right-hand port CONN3 from the candidate layout. The saddle begins at
X=14 mm and ends at X=40 mm; two tie stations are at X=30 and 36 mm. Two
small cable ties pass down one slot and up the opposite slot, holding the
insulated wire bundle. Keep the connector fully mated with slack before the
first tie and relaxed bends down to the saddle. The open tie corridor is
6.8 mm wide. Do not use the connector as the anchor or crush wire insulation.
Anchor the heavier extension separately to the vehicle. This design does
not claim a fitted two-cable installation or validate the complete I²C bus.

Attach **two flat 30 × 6 mm underside tape zones**, centred at Y=±11.3 mm,
with removable double-sided tape compatible with the actual trim and cabin
temperatures. Total specified tape area is 360 mm². Keep tape off the central
opening and cable saddle. The cable-tie return straps run underneath the
saddle: installed/compressed tape thickness must be at least the measured
strap thickness **plus 0.4 mm**. Keep tie locking heads on top or at the side,
never underneath. Verify actual strap, head and trim clearance; thinner tape
requires a geometry/routing change, not forceful seating of the base.

The frame is planar, not matched to measured trim curvature. Mount at a
fixed, shaded cabin position away from the heater outlet and ESP32, without
rocking or bending the PCB. Check removal on the chosen trim/tape pair before
committing to the location. Retaping allows relocation after winter testing;
removal without cosmetic damage has not been established. Record location,
orientation, cable route and any move so winter observations remain comparable.

## Outputs

| File | Purpose |
| --- | --- |
| [src/holder.py](src/holder.py) | Maintained parametric export entrypoint |
| [src/lib/geometry.py](src/lib/geometry.py) | Dimensions, measurement gates and geometry factory |
| [src/lib/board-layout.json](src/lib/board-layout.json) | Candidate manufacturer layout and provenance |
| [STEP/holder.step](STEP/holder.step) | Exact draft solid |
| [STL/holder.stl](STL/holder.stl) | Draft print orientation; millimetres |
| [checks/](checks/) | Saved-artifact geometry/mesh checks and view job |
| [reports/validation.md](reports/validation.md) | Findings, measured CAD facts and limits |
| [views/](views/) | Isometric, top, side, underside and STL inspection views |

Envelope: **56.7 × 30 × 12 mm**. There is only one printed part, so no separate
assembly export is needed. Board, screws, trim, tape and cable are deliberately
excluded from the model: no assumed physical stand-ins masquerade as fit proof.
[src/README.md](src/README.md) is the model catalog.

## Manufacturer facts and applicability

Adafruit's [product 6174 page](https://www.adafruit.com/product/6174) gives
25.5 × 17.7 × 4.8 mm **assembled module dimensions**. The 4.8 mm value does not
establish PCB thickness, membrane height or plugged-cable clearance.

The [shared Adafruit guide](https://learn.adafruit.com/adafruit-sht40-temperature-humidity-sensor/downloads)
links the [SHT45 Eagle board](https://github.com/adafruit/Adafruit-SHT40-PCB/blob/40fe2b3aca1aaa3b2185d3d138e77d5b043a56e1/Adafruit%20SHT45.brd).
At that revision its nominal routed outline is 25.4 × 17.78 mm, corner radius
2.54 mm, four mounting-hole drills 2.5 mm, hole pitch 20.32 × 12.7 mm and
sensor centre (12.7, 8.89) mm from the lower-left board datum. Its isolated
sensor tongue is described by layer-20 slot centre lines. These are **verified
facts about that manufacturer file**; its repository identifies product
5665, not explicitly the PTFE 6174. Check all of these against the delivered
6174 before accepting this layout. If the pattern is asymmetric or the
sensor has moved, revise the factory/keepout, not just the pitch constants.

The guide also links Adafruit's 5665 STEP model. It was not adopted as a
confirmed 6174 assembly. A reachable `step.parts` search for `SHT45` returned
zero items on 2026-10-07. Source revision, board SHA-256, raw outline records,
hole coordinates and connector placements are retained in `board-layout.json`.
No critical dimensions were scaled from product photographs.

## Required measurements and release gate

Unknown physical dimensions are `None` in `Parameters`. The draft has no
invented board-thickness or plug model. Dimensions such as 12 mm standoff,
4.9 mm posts and the cable saddle are **adjustable design allowances**.

Use PCB-centred XY coordinates and PCB backside as the component-height datum.
For each hole, check both faces; use the smallest clear-land radius in the
measurement parameters.

| Measure / confirm on your hardware | Parameter / required action |
| --- | --- |
| Received board revision, length, width, corners, all four hole centres and hole diameters | Confirm/update `board_length`, `board_width`, `hole_pitch_x`, `hole_pitch_y`, `hole_diameter`; set `received_layout_confirmed` only after comparison |
| Actual PCB thickness | `pcb_thickness_measured`; do not use overall module height |
| Lowest backside solder/component protrusion | `backside_protrusion_measured`; keep ≥0.6 mm clearance above base |
| Clear backside land around each mounting hole | `bottom_free_land_radius_measured` must be ≥2.45 mm; otherwise redesign contact posts |
| Clear top land and selected screw-head radius | `top_free_land_radius_measured`, `screw_head_radius_measured`; head must span hole without component interference |
| PTFE package/membrane footprint and centre relative to PCB centre | `membrane_width_measured`, `membrane_depth_measured`, `membrane_x_measured`, `membrane_y_measured`; retain ≥0.6 mm margin inside sensor keepout and visually confirm tongue clearance |
| Fully mated plug reach beyond right PCB edge | `mated_plug_reach_measured`; revise saddle/tie positions if needed |
| Lowest plug surface relative to PCB backside | Signed `plug_lowest_above_back_measured`; must clear base by ≥0.6 mm |
| Insulated wire-bundle width and relaxed routing bend radius | `cable_width_measured`, `cable_bend_radius_measured`; width plus 1.2 mm must fit 6.8 mm corridor |
| Cable-tie strap width and thickness | `tie_width_measured`, `tie_thickness_measured`; fit slots and verify complete loops/locking heads |
| Selected M2 nylon screw thread diameter and under-head length | `screw_thread_diameter_measured`, `screw_under_head_length_measured`; verify 3–5.4 mm engagement |
| Actual drilled/reamed pilot before tapping | `calibrated_pilot_diameter_measured`; calibrate source/print and verify tap/screw retention on a trial part |
| Tape thickness after installation/compression | `mounting_tape_measured`; must clear underside tie straps by ≥0.4 mm |
| Trim curvature, base seating and tape removal | Physical fit trial; then `trim_fit_confirmed` |
| Printed/tapped post integrity and gentle PCB retention | Physical retention trial; then `tapped_retention_confirmed` |

The cable generator guard reserves plug reach plus twice the measured bend
radius before the first tie. This is a conservative horizontal allowance,
**not a swept cable simulation**; the actual downward route and slack must be
trial-fitted. Raise `board_back_z` or extend the saddle if that route needs it.

Set `ready_for_final_print=True` only after entering every measured value and
completing the three physical confirmation flags. Generation then rejects
missing measurements and unsafe combinations. Regenerate and recheck every
output after changing dimensions. This gate prevents incomplete release;
it cannot independently verify the truth of entered values or winter behavior.
The synthetic measurements in the check script test gate logic only.

## Regenerate, validate and inspect

Run from this directory. CAD dependencies stay separate from the server.

```sh
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 python src/holder.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 python checks/check_geometry.py
uv run --no-project --with trimesh --with numpy --with scipy python checks/check_mesh.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 cadgen step snapshot --job checks/views.json
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 cadgen stl snapshot STL/holder.stl views/holder-mesh.png
```

The maintained STL decorator uses `mesh_tolerance=0.001`, a **relative chord
tolerance**, not 0.001 mm. Run the plugin's `dfam-check/scripts/dfam_tool.py`
with its `dfam-check/requirements.txt` dependencies for `measure
STL/holder.stl --angle-limit 45` and `orientations STL/holder.stl
--angle-limit 45`; save JSON under `reports/`. Review snapshots yourself after
geometry edits; geometry checks and a viewer alone do not replace visual review.

## PETG printing and physical commissioning

Print with tape rails on the bed and posts up, as supplied. Starting point:
0.4 mm nozzle, 0.2 mm layers, four perimeter lines and the printer/filament's
PETG profile. A brim is optional. All holes/slots are vertical; no printed
roof spans the board. Outside rims have 1 mm plan radii and shallow 0.4 mm
45° chamfers. The inner rim is square; deburr it and the PCB contact tops.
Supports are expected to be unnecessary; inspect actual slicer toolpaths,
small pilot fidelity and first layers before printing. Tap/drill operations
remain required; the pilot is below the DfAM guide's generic 2 mm hole limit.

Trial fit without power first: confirm level board seating, all four clear
lands, exposed membrane/tongue, connector removal, cable slack and tie return
clearance. Check adhesive hold/removal, vibration and PETG creep at the chosen
location. Compare cabin readings and response against a freely exposed sensor
before relying on the mount; repeat after position changes and during winter
testing. Document bias, lag and location. None of these physical checks have
been performed by the CAD validation.
