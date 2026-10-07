# TMP117 windshield holder — measurement-gated draft

Related work: [GitHub issue #11](https://github.com/jannesii/raspberry-pi-smart-home/issues/11).
Design basis: [car-heater defrost review](../../docs/reviews/car-heater-defrost-strategy.md).
The physical mounting task remains open until parts are measured and fitted.

## Design and outputs

Two PETG parts: an open-bottom carrier and a removable foam bridge.
The entire PCB backside is free of printed material, including the isolated
TMP117 tongue. There is no PCB ledge, snap fit, or screw bearing on the board.
The base surrounds the board; the PCB rests against glass through the thin
nonconductive thermal interface described in the review. Small polyimide tape
retainers over the PCB's outer mounting-hole lands hold it laterally; neither
tape nor foam belongs under the sensor tongue.

Four posts support the bridge. Their hard stops set the foam gap; 2.4 mm pegs
locate the bridge in 2.8 mm holes. The pegs are slip locators, not snap locks.
Retain the bridge with narrow polyimide tape strips over its two ends and down
onto the outer rails. Seat it on all four stops; tightening tape cannot reduce
the gap unless the bridge itself bends. Use only enough tape tension to retain
it, and verify that the bridge and tongue stay flat.

An 8 × 8 mm trial E30 pad adheres to the bridge underside, centred above the
sensor. It provides cabin-side insulation and gentle preload. Its footprint
and thickness are adjustable; check components and cut the pad to avoid side
loads on the isolated tongue. The design limits the trial compression to 3%
(0.3 mm for nominal 10 mm stock), but **compression is not a verified force**.
Start with zero preload and increase only if required; do not use a visibly
bending PCB/tongue. E30 force, thermal bias and lag require physical testing.

Use the right-hand STEMMA QT connector (PCB component CONN3) and route the cable
straight out over the external saddle. Two small cable ties pass down one slot
and up its opposite slot to secure the insulated cable bundle to the saddle.
Leave slack between the plug and first tie, and an unforced bend beyond the
second tie. Do not pull the wires tight, crush insulation or use the connector
as the anchor. Secure the heavier extension cable separately to the vehicle.
The left port is unused; this draft does not claim clearance for two plugs.

Attach the carrier's outer rails with thin removable tape appropriate for the
actual glass and temperature. Tape cushions the base; 1 mm outside plan radii
and 0.4 mm rim chamfers remove sharp glass-side edges. Deburr printed parts.
Keep all adhesive outside the PCB footprint and sensor thermal path. Thermal
paste is not an adhesive. Confirm windshield curvature and tape adhesion at
the installation location; the carrier is planar, not fitted to measured glass.

| File | Purpose |
| --- | --- |
| [STEP/carrier.step](STEP/carrier.step) | Exact carrier geometry |
| [STEP/foam_bridge.step](STEP/foam_bridge.step) | Exact foam bridge geometry |
| [STEP/assembly.step](STEP/assembly.step) | Installed placement of the two printed parts |
| [STL/carrier.stl](STL/carrier.stl) | Carrier in print orientation, mm |
| [STL/foam_bridge.stl](STL/foam_bridge.stl) | Bridge flat on bed, mm |

The assembly deliberately excludes board, glass, cable and foam stand-ins.
Print the individual STLs, not the installed assembly. Draft carrier envelope:
56.8 × 33.38 × 13.75 mm; bridge: 17.6 × 33.38 × 2 mm.

## Verified nominal layout versus required measurements

Adafruit's [product 4821](https://www.adafruit.com/product/4821) lists an assembled
25.5 × 17.7 × 4.6 mm envelope. That last dimension is **not PCB thickness or
sensor height**. The [vendor Eagle board](https://github.com/adafruit/Adafruit-TMP117-PCB/blob/874b3a5b27792d4d166547bab075cb963e1bdb32/Adafruit_TMP117.brd)
provides the nominal 25.4 × 17.78 mm routed outline, 2.54 mm outer corner radius,
sensor centre (12.7, 8.89), and tongue slot centre lines with 0.508 mm width.
These layout facts and source SHA-256 are recorded in
[src/lib/board-layout.json](src/lib/board-layout.json). Check the delivered board
revision and outline against them. No board thickness or mated connector
dimensions were inferred from photographs. A reachable step.parts search for
`TMP117` returned zero parts on 2026-10-07.

All functional dimensions are in the `Parameters` dataclass in
[src/lib/geometry.py](src/lib/geometry.py). Coordinates are millimetres, with
the nominal PCB centred in XY and carrier mounting-pad underside at Z=0.

**Measure before final print:**

| Physical measurement | Parameter / action |
| --- | --- |
| Delivered PCB length, width, corner shape and revision | Confirm `board_length`, `board_width`, `board_corner`; modify layout if changed |
| PCB thickness, solder protrusions and backside flatness | Fill `pcb_thickness_measured`; verify an unobstructed backside thermal path |
| Highest sensor/tongue component above PCB backside | Fill `sensor_top_above_back_measured`; do not substitute overall module height |
| Fully mated plug reach beyond PCB edge | Fill `mated_plug_reach_measured`; increase `cable_reach` if needed |
| Lowest plug/cable surface above glass over saddle | Fill `cable_lowest_above_glass_measured`; must clear base plus mounting tape by ≥0.6 mm |
| Insulated wire-bundle width and bend radius | Fill `cable_width_measured`; adjust saddle/ties and route without forced bends |
| Thin thermal layer after installation | Fill `glass_layer_measured` |
| Mounting tape installed thickness | Fill `mounting_tape_measured` |
| E30 actual thickness, contact footprint and compression force | Update `foam_thickness`, `foam_width`, `foam_length`, `foam_compression`; test gentle contact |
| Windshield local curvature and attachment conditions | Trial fit without rocking, scratched glass or board bending |

Unknown physical dimensions remain `None`. For draft geometry only, explicitly
named visualization datums use a 2.5 mm sensor-top offset, 0.1 mm thermal layer,
and 0.15 mm mounting-tape thickness. **Those three numbers are not board or
installation measurements. The current exports are not final-fit print files.**
Set `ready_for_final_print=True` only after completing the table and physical
checks; the model then rejects missing `_measured` values.

The stop height follows:

```text
stop_z = thermal_layer + sensor_top + foam_thickness × (1 - compression)
         - mounting_tape
```

It currently equals 12.15 mm relative to the carrier underside. Adjusting tape
or foam without updating this stack changes pressure. The allowable trial
compression range in the generator is 0–5%; this is a design guard, not an E30
material certification.

## Regenerate and inspect

Run from this directory. `uv` keeps CAD dependencies separate from the server.

```sh
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 python src/assembly.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 python checks/check_geometry.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.15 cadgen step snapshot --job checks/views.json
```

[src/README.md](src/README.md) is the model catalog. Child exports regenerate
with the assembly. Mesh tolerance is 0.001 in the cadgen decorator.

For STL printability measurements, use the text-to-cad plugin's
`dfam-check/scripts/dfam_tool.py` with its dependencies (`trimesh`, `numpy`,
`rtree`, `scipy`, `networkx`, `lxml`): `measure STL/carrier.stl --angle-limit 45`
and `measure STL/foam_bridge.stl --angle-limit 45`. Save JSON under `reports/`.
[checks/check_mesh.py](checks/check_mesh.py) independently asserts watertightness,
one body, correct scale, mesh/STEP volume agreement and the chamfer-angle bound with a documented 0.2° mesh-faceting allowance.
Run it with `uv run --no-project --with trimesh --with numpy --with scipy
python checks/check_mesh.py`.

Inspection views are saved under `views/`: assembled isometric, assembled side,
carrier underside, and separate bridge. Re-render after dimension changes.
[reports/validation.md](reports/validation.md) describes measured findings and
limits. Neither geometry checks nor renders prove contact force or retention.

## PETG print starting point

Use a 0.4 mm nozzle, 0.2 mm layers, four perimeter lines and the individual STL
orientations as supplied. Both parts start at Z=0; the carrier pads face the bed
and the pegs point up. The bridge prints as a separate flat plate, so its installed
span does not become a bridge during printing. Through slots and locator holes
are vertical. The only downward sloping surfaces are shallow 45° rim chamfers
in the first 0.4 mm. Supports are expected to be unnecessary; confirm the actual
slicer preview, first-layer fit and printer hole tolerances before use.

Use the printer/filament PETG profile for temperatures and cooling. A brim is
optional if bed adhesion needs it; keep brim remnants off the glass-facing rim.
Check heat and cold behavior at the selected location, away from heater airflow.
