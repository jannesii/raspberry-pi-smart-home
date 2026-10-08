# Replacement draft validation — 2026-10-08

Status: **saved-artifact checked DRAFT; physical fit remains unverified**.
Runtime: cadgen 0.7.17, isolated from server dependencies. Units: mm.
This report supersedes the old carrier/bridge evidence; old artifacts and
reports have been removed to prevent printing the abandoned concept.

## Exact saved artifacts

`checks/check_redesign.py` and `checks/check_geometry.py` read all six saved
part/coupon STEPs and the eight-solid installed assembly. Each part is valid,
positive volume and one solid. Assembly pairwise intersection volume is below
1e−6 mm³. Exact sizes/volumes are in `geometry.json`.

Checks verify the full 25.4 × 17.78 mm vertical PCB removal corridor through
the base; the 5.842 × 4.953 mm under-tongue thermal keepout; the nominal
1.6 mm PCB allowance; the 8 × 6 × 8 mm pad allowance; both 8 × 8 × 4 mm
mated-plug allowances and straight slack corridors. These are synthetic design
envelopes, **not received component geometry**. Component-free bearing lands,
actual cable gripping, unplugging finger access and bends require physical trials.

All 12 base pilots have a checked 2.55 mm empty core, radial material and a
closed bottom. BT3×6 nominal engagements are 4 mm for cover/retainers and
2 mm for clamps. Screw tips remain at least 2 mm above the base underside;
no screw passes through PCB holes. A 6 mm tool corridor clears cover/clamp
screws independently; retainers are accessible after cover removal. The
cover's four structural stop contacts are checked at Z=15 and its roof top
at Z=17. Retainer contact adjustment accounts for tape/paste/PCB thickness;
cover closure does not determine contact force.

`checks/test_release_gate.py` exercises draft allowance, rejection of missing
release measurements, negative/nonfinite measurements, tape-stack adjustment
and physically impossible received dimensions. Synthetic inputs remain tests;
they never fill the `_measured` fields in the model. Required physical-trial,
layout and bearing-land confirmations prevent release using dimensions alone.

## Printable meshes

`checks/check_mesh.py` reads each of the six STLs. All are watertight, have
consistent winding, positive volume and one connected body. Bounding dimensions
agree with saved exact geometry within 0.001 mm; volume error is below 0.2%
(actual maximum approximately 0.130%). No downward-facing mesh triangles remain
above the bed in the supplied orientations. `mesh.json` records results.

During development the saved-solid test caught disconnected locating tabs;
mesh checks caught unsupported boss lips and collinear tessellation edge
junctions. The source was repaired by using the aperture walls as perimeter locators,
extending base support beneath bosses and rounding boss/shield profiles; outputs were
regenerated. Failed computations were not retained as passing evidence.

No slicer is available in this environment. **No A1/PETG toolpaths were
inspected, no print was sent, and no physical fit is claimed.** Aperture fit,
small holes, extrusion widths, screw grip and actual tape/glass fit
remain preview/coupon checks. Support-free mesh geometry is separate from
slicer evidence. Material temperature testing is excluded by the approved spec.

## Visual and source review

Saved snapshots cover every independently printed part/coupon, assembled
isometric and side, and the glass-facing underside. They expose the open
contact window, inward retainer fingers, two external cable bridges, separate
cover stops and the central airflow shield. Only printed geometry is shown.
The companion viewer was launched once but subsequent display calls reported
no open tab. The local CAD Viewer fallback was started successfully; its
file-specific handoff links cover all six STEP/STL pairs and the assembly.

Ruff lint/format and mypy (checking untyped bodies with unavailable external
CAD stubs ignored) cover CAD sources/checks. The local `.venv/bin/ruff` is
absent, so Ruff uses `uvx`. Repository instruction budgets are checked.
The complete CAD check suite is the relevant validation suite; server pytest
is not a substitute for CAD checks and is outside this runtime-unchanged ticket.

Both review axes found one issue initially: unused dimension parameters
and a clamp opening inconsistent with its allowance. Both were corrected and
rechecked; no consequential standards or spec findings remain. The corrected
saved clamp opening is explicitly checked at 4 mm wide × 3 mm high.

## Remaining gates

#22: inspect delivered board/lands/components, plugs and cables; measure the
actual contact/tape/foam stack; print coupons and trial both sites; demonstrate
retention without bending, independent cable service and clean attachment
removal. #23 finalizes geometry using these measurements. #11 preparation and
later installed bias/lag, bus reliability and attachment commissioning remain
separate. Matching sensor readings do not establish glass-temperature accuracy.
