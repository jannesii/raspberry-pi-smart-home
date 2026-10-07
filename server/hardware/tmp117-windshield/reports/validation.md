# Validation — 2026-10-07

Status: **geometrically checked draft; physical fit and pressure unverified**.
All dimensions below are millimetres. Artifacts were generated with cadgen 0.7.15.

## Exact saved STEP checks

`checks/check_geometry.py` read the saved STEP files, rather than relying only on
source formulas. Both printed parts are valid positive-volume single solids;
the installed assembly contains two solids. Carrier volume is 3338.513 mm³,
bridge volume is 1123.999 mm³. Their installed solid intersection is zero.

The entire nominal PCB XY rectangle is clear of the carrier from the glass
plane through the bridge height. The source-derived tongue slot envelope is
also clear. The compressed trial 8 × 8 × 9.7 mm foam space has zero overlap
with either printed part. The 6 mm wide × 6 mm tall trial cable corridor starts
0.6 mm above the 2 mm base and extends out through the right side; neither part
intersects it. That is a design corridor, not a measured plug or cable envelope.
The carrier and separate bridge both start at Z=0 in their print exports.

The four bridge holes clear the 2.4 mm pegs by 0.4 mm diametrally. Installed
bridge underside is at Z=12.15 relative to the mounting-pad underside; its
2 mm plate sits on the four hard stops with no solid interference.

## STL printability measurements

The supporting text-to-cad DfAM skill's `dfam_tool.py measure` ran on both final
STLs at its FDM 45° angle limit, with all required mesh dependencies. Complete
JSON reports are retained beside this file; no fact family failed to compute.
Independent checks in `checks/check_mesh.py` also verified topology and scale.

| Measured fact | Carrier | Foam bridge |
| --- | --- | --- |
| Watertight / consistent winding | Yes / yes | Yes / yes |
| Connected bodies | 1 | 1 |
| Bounding box | 56.8 × 33.38 × 13.75 | 17.6 × 33.38 × 2 |
| Sampled minimum thickness | 1.6 | 2.0 |
| Thickness fifth percentile | 2.0 | 2.0 |
| STL/STEP volume difference | 0.139% | 0.0035% |
| Downward surface area below strict 45° threshold | 59.29 mm² | 0 |

The DfAM skill's `references/process-limits.md` lists 1.2 mm supported walls,
1.6 mm unsupported walls, and 45° self-supporting slopes for FDM. The measured
minimum and fifth-percentile thicknesses satisfy those wall defaults. Ray
sampling is not exhaustive; the carrier's 1.6 mm cable-slot webs set the minimum.

**Angle qualification:** the carrier's strict mesh report flags shallow rim
chamfers, so it is not a zero-overhang result. The CAD chamfers are nominally
45°; triangle facets on curved corners reach 44.8963°. The independent check
allows 0.2° of faceting deviation and confirms that every downward surface
above the bed is within the first 0.4 mm of build height. It does not conceal
or replace the strict report. These shallow first-layer chamfers are expected
to print without supports; a slicer preview and printer-specific trial remain
necessary. The separate flat bridge has no downward surfaces above the bed.
No toolpaths were generated and no printer was contacted.

## Visual and source review

Four saved STEP snapshots were rendered and read: assembly isometric, assembly
side, carrier underside, and separate bridge. They expose the open PCB thermal
path, foam gap, rounded mounting perimeter, cable saddle/slots and locating
holes. Views show only printed parts, avoiding invented component solids.

Ruff check and format check cover the CAD Python source and checks. The server's
`.venv/bin/ruff` was absent, so Ruff ran via `uvx`. Repository agent-instruction
budgets pass. The server runtime was unchanged; its pytest suite was not run.

The README documents vendor layout provenance, regeneration, installation,
print orientation and physical measurement gates. Existing GitHub issue #11
tracks physical carrier preparation; it was read, not closed or edited.

## Outstanding physical checks

Measure every interface listed in the holder README, including backside
flatness, actual sensor height, mated plug/cable envelope, E30 thickness and
force, thermal layer and mounting tape. Confirm that tape and foam retain the
PCB without bending its isolated tongue. Confirm local windshield curvature,
adherence, PETG temperature suitability, cable strain relief and installed
thermal bias/lag. Geometry and snapshots do not establish any of these facts.
