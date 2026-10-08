# SHT45 6174 redesign validation — DRAFT

Spec #19 / ticket #21, 2026-10-08. Millimetres. No delivered hardware was
measured; no vehicle, printer, live device, production data or webhook was used.
The previous M2/tie-saddle holder and its evidence have been replaced.

## Saved artifact evidence

- All nine independently printable STEP parts are valid, one-solid,
  positive-volume bodies. The installed STEP has nine valid printed solids:
  adapter, housing, cover, four retainers and two clamps.
- Each installed part matches its separately exported solid at the required
  pose, with <0.001 mm³ volume disagreement. Pairwise printed mating has
  <1e-6 mm³ interference. Coupon service parts also mate without overlap.
- PCB backside allowance is Z=18 from adapter underside; housing underside
  Z=8 and adapter plate top Z=2 leave a 6 mm air gap. Roof exterior is Z=28.
  Installed printed envelope is 80 × 40 × 28 mm.
- A 16 × 12 mm through-air prism clears every installed printed solid from
  below the tape plane through the roof. Five roof windows total 683.52 mm²,
  about 38.8% of the 44 × 40 mm rectangular roof envelope. Side inlets and
  both open plug/transition corridors are checked. This validates geometric
  air paths, not actual airflow, thermal isolation, bias or response time.
- A drilled 25.4 × 17.78 × 1.6 mm candidate PCB allowance clears installed
  printed parts. Four support tops and short locating pins are present.
  The whole PCB lift prism above the 0.8 mm pins clears housing/adapter
  after releasing cover/retainers/cables. Candidate component, two mated
  plug (8 × 8 × 6 mm), slack and transition allowances clear printed material.
  These are design envelopes, not models of a received board or flexible cable.
- Blind 2.55 mm pilot cores, surrounding material and closed floors are checked
  in the saved adapter/housing. BT3x6 gives 4/4/4/2 mm engagement for adapter,
  cover, retainer and clamp joints, with 1 mm pilot-bottom margin. Screw
  envelopes clear the PCB; 6 mm driver and 7 mm head envelopes clear service
  parts. Cover removal is required for retainers/adapter joints; clamp access
  remains independent of the cover.
- Locating spigots are 4 mm diameter ×0.8 mm high; mating recesses are 4.4 mm
  diameter ×1 mm deep. Saved mating/shoulders provide 0.2 mm radial/axial
  allowances. Two 64 ×7 mm tape rails contain material at the adapter underside.
- Board/plug coupon preserves actual retainer/clamp stops, pilots and PCB-lift
  geometry; only cover towers are removed. Separate adapter/socket coupons
  mate at the same stack. Pilot coupon checks 2.45/2.55/2.65 mm bores.

See [geometry.json](geometry.json) and [mesh.json](mesh.json). The exact check
programs read saved artifacts; they do not accept source constants as evidence.

## Mesh and print geometry

All nine saved millimetre STLs are watertight, consistently wound, one-body and
positive-volume. Bounding sizes agree with STEP within 0.001 mm, volumes within
0.2%; the maximum measured volume error is below 0.08%. STL chord tolerance
is **relative 0.0002**, not a millimetre distance. Mesh hashes are recorded.

Print orientations put flat undersides on the bed for adapter, housing and
coupons; flat exterior/top faces on the bed for cover, retainers and clamps.
Post bottoms and fences extend to the bed, avoiding unsupported initial
cantilevers. Mesh normals use a 45° limit with 0.2° faceting allowance.
The only steeper downward features are the explicitly bounded **0.5 mm radial
recess ledges at Z=1** in housing/board coupon and adapter socket coupon:
about 24.48 mm² for four recesses, 6.12 mm² for one. Other tiny downward-normal
components along tessellated curved walls are counted in total downward area,
not labelled support requirements. Source changes to separate collinear window
and fence boundaries resolved mismatched STL edges without weakening topology
checks. The two end windows differ by 0.4 mm in depth for reliable tessellation.

## Slicer attempt and limit

The installed OrcaSlicer 2.4.0-dev AppImage was attempted with complete built-in
**Bambu Lab A1 0.4 nozzle / 0.20mm Strength @BBL A1 / Generic PETG @BBL A1**
presets. The process had `enable_support=0` and `wall_loops=4`. It exited with
**139 (segmentation fault) before producing G-code**; even its help invocation
exited 139. No toolpaths or layers could be inspected. No print was sent.
**Support-free slicer verification remains outstanding.** Mesh-normal checks
and small recess-ledges are not a substitute for that inspection.

To reproduce the attempt without using personal profiles, resolve full presets
using the CAD plugin's `gcode/scripts/orca_presets.py`, copy them to an ignored
`tmp/` directory, set the two process fields above, then run the installed Orca:

```sh
ORCA=/home/jannesi/Applications/OrcaSlicer_Linux_V2.4.0-dev.AppImage
"$ORCA" STL/adapter.stl \
  --load-settings 'tmp/slicer/process.json;tmp/slicer/printer.json' \
  --load-filaments tmp/slicer/filament-1.json --arrange 1 --slice 0 \
  --outputdir "$(pwd)/tmp/slicer" --export-3mf adapter.gcode.3mf
```

Repeat for each STL when a working slicer is available. Inspect recess shoulders,
blind pilots, locating pins, small retainer pads and the cover ribs in the
supplied orientations with supports disabled. Use the actual filament profile
for printing; Generic PETG here was a diagnostic preset, not a physical result.

## Visual review and automated checks

The implementer rendered and inspected installed isometric, top, front and
underside views, individual part print orientations and the housing mesh.
The views expose the membrane opening, lateral inlets, independent clamps,
retainer pads, tape opening and raised adapter interface. The PCB and cables
are omitted deliberately, so visual review cannot establish delivered fit.
A complete snapshot job is maintained at `checks/views.json`; mesh snapshot:
`cadgen stl snapshot STL/housing.stl views/housing-mesh.png` using runtime 0.7.17.
Models are also shown in the CAD viewer; viewer display does not replace this
saved-image review. [evidence.json](evidence.json) binds reviewed artifacts/views
to SHA-256 hashes.

Run all checks using the commands in [the project guide](../README.md):
`check_redesign.py`, `check_geometry.py`, `test_release_gate.py` and
`check_mesh.py`. Seven release tests cover missing measurements/confirmations,
unsafe interfaces, invalid values, retainer thickness adjustment undersized
screw heads and matching-but-inconsistent received layouts. They use synthetic data only. CAD checks plus Ruff, optional Mypy
(`--ignore-missing-imports --follow-imports=skip`) and repository instruction
budgets were run. Mypy does not typecheck the CAD kernel or unannotated bodies.
The requested `.venv/bin/ruff` is absent; `uvx ruff` provides the fallback.
No unrelated server suite substitutes for CAD validation; spec #19 explicitly
limits this work to hardware geometry and documentation.

## Release still requires physical evidence

Confirm the 6174 revision/layout, thickness, safe lands, underside, component and
membrane positions, both plugged cable envelopes, relaxed bends, clamp grip,
BT3 pilots/head/tools, tape, trim curvature/site airflow and physical retention.
Inspect normal connector/header service and several removal/reseat cycles.
Confirm seating, attachment and nondamaging removal of the taped adapter.
Unknown dimensions remain `None`; no generic 5665 layout is final-fit evidence.
Measure and revise before setting final release flags. No material temperature
testing, year-round adhesion certification or installed sensor/bus validation
was performed or is inferred from these exports.
