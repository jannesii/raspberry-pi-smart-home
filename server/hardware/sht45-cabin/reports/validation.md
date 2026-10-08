# Validation — SHT45 cabin holder

Reviewed 2026-10-07. **Measurement-gated draft; no physical fit, print or
winter-temperature test performed.** This report applies to the supplied
`holder.step` and `holder.stl`, in millimetres. Artifact SHA-256 values are
recorded in `geometry.json` and `mesh.json` respectively.

## Checks actually run

- cadgen 0.7.15 source generation and STEP/STL export.
- `checks/check_geometry.py` against the saved STEP, including measurement
  gate rejection tests. Synthetic passing inputs test the gate, not hardware.
- `checks/check_mesh.py` against the actual STL, using trimesh/numpy/scipy.
- DfAM `measure` and six axis-aligned `orientations` on that STL, at 45°.
- STEP isometric, top, front and underside snapshots; STL isometric snapshot.
  Each was opened and visually reviewed after final geometry changes.
- Ruff check and format check on CAD source/check scripts (via `uvx ruff`;
  the repository `.venv/bin/ruff` is unavailable in this environment).
- Repository `scripts/check_agent_instructions.py`.

No server behavior changed; no live devices, production databases or
integration calls were used for validation. No application pytest suite was
needed for this isolated hardware/documentation addition.

## Saved geometry findings

| Selected geometry / check | Result and threshold |
| --- | --- |
| Holder STEP | Valid, one solid, positive volume 2957.242 mm³ |
| Envelope / bed datum | 56.7 × 30 × 12 mm; minimum Z=0 within 0.000001 mm |
| Sensor/tongue air column | 6.4 × 6.4 mm XY, from Z=−0.01 to above PCB; overlap <0.000001 mm³ (reported zero) |
| Above whole nominal PCB | No printed cover; overlap <0.000001 mm³ |
| Between base and board | Printed material restricted to four declared corner-post footprints |
| PCB support plane | Z=12 mm; 10 mm above 2 mm base top |
| Four printed post tops | Total annular area 67.387 mm²; saved-section area agrees with design formula. Actual PCB contact is smaller because its candidate holes are 2.5 mm, versus 1.6 mm pilots; physical contact is unverified |
| Post feet | Full footprints supported continuously from the bed; no cantilevered post bottoms |
| Blind pilots | Four open 1.6 mm × 6 mm cylindrical bores, checked at declared centres/depth |
| Tape zones | Two complete 30 × 6 mm planar underside rectangles, 360 mm² total; no material below Z=0 |
| Cable corridor | 6.8 mm wide above saddle, unobstructed; design corridor, not measured plug envelope |
| Tie slots | Four through slots, with an unobstructed 2.5 × 1.7 mm inner test section |
| Release gate | Rejects missing measurements, false physical confirmations, long/wide screws, inadequate contact land, cable crowding, membrane offset, thin tape and NaN inputs |

Retained reports: [geometry.json](geometry.json), [mesh.json](mesh.json),
[holder-dfam.json](holder-dfam.json), [orientations.json](orientations.json).
The manufacturer candidate layout and SHA-256 are in
[board-layout.json](../src/lib/board-layout.json); it is explicitly not a
confirmed 6174-specific mechanical drawing.

## Mesh and FDM findings

The STL is watertight, consistently wound and one connected body. Its
2953.963 mm³ volume differs from the exact STEP by 0.111%, below the check's
0.2% threshold. Dimensions agree within 0.001 mm. DfAM reports millimetre-scale
geometry (`scale.units_suspect=false`). These results support slicing the
draft; they do not establish hardware fit.

Compare against the pinned text-to-cad 0.7.15 plugin's FDM defaults in
`skills/dfam-check/references/process-limits.md` (relative to plugin root):

| Finding | Evidence and interpretation |
| --- | --- |
| Supported wall ≥1.2 mm | `wall_thickness.min_mm=1.641`; passes sampled limit |
| Unsupported wall ≥1.6 mm | `min_mm=1.641`, `p05_mm=1.642`, 2,000 valid samples; passes sampled limit. Sampled minima are not an exhaustive proof |
| Self-supporting angle ≥45° | DfAM bins 56.69 mm² below the strict boundary. Independent mesh check localizes all downward faces above the bed to the first 0.4 mm exterior rim and measures a minimum 44.896°. This is within the documented 0.2° mesh-faceting allowance for exact 45° chamfers; a strict no-tolerance pass is **not** claimed |
| Orientation | Supplied +Z-up orientation has lowest binned area: 56.69 mm² (1.6%), 12 mm height. The other five have 214.25–1062.12 mm² and 12–56.7 mm height |
| Support estimate | `estimated_support_volume_mm3=8.0`, 0.3% coarse upper bound; corresponds to shallow rim faceting, not a slicer instruction to add supports |
| Hole rule ≥2 mm | DfAM does not measure hole diameter. Saved STEP explicitly checks 1.6 mm pilots; they are below the generic FDM hole recommendation and require calibration/drilling/tapping before assembly |
| Bridge/positive-feature rules | Not measured by this DfAM tool. No spanning roof is designed; actual paths still require slicer review |

The initial model had collinear tessellation T-junctions where corner posts
crossed the aperture's bottom boundary. Final source places the entire post
footprint on the rail, uses a 26.6 × 7.7 mm base aperture and exterior-only
chamfers. The sensor air column remains clear. The saddle was widened to
16 mm and posts to 4.9 mm after sampled wall checks; final STL was regenerated
and passed the independent closure and wall checks. No mesh hole-filling or
post-export repair hides source geometry defects.

## Visual inspection and limits

The final isometric and top STEP views show four mounting posts, open central
aperture, no lid and two paired tie stations on the external saddle. The
front view confirms the raised support plane and flat base. The underside
shows the open aperture, solid post feet and unbroken tape rails. The STL
view confirms the exported part has the same visible features. No fabricated
board/plug/fastener model was used to suggest a verified assembly fit.

Still physically unverified: delivered board revision, all mounting lands,
membrane/package location and height, connector fit/removal, cable bends and
strain relief, drilled/tapped holes, screw seating and retention, tape
thickness/adhesion/removal, trim curvature, vibration, heat/cold creep, airflow,
thermal bias/lag and actual print/slicer toolpaths. Enter measurements and
perform the physical trials in the README before final-print release.
