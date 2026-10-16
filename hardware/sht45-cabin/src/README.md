# Maintained draft model catalog

Millimetres. Each entrypoint declares same-stem STEP/STL outputs relative to
its source. `assembly.py` is STEP only and calls the maintained child models.
`lib/geometry.py` owns explicit allowances and the physical-measurement gate;
`lib/board-layout.json` preserves unconfirmed 5665-layout provenance for 6174.
No purchased-part proxy is asserted to fit received hardware.

| Entry | Outputs / role |
| --- | --- |
| `adapter.py` | `STEP/adapter.step`, `STL/adapter.stl`: taped console interface |
| `housing.py` | `STEP/housing.step`, `STL/housing.stl`: removable board support/strain-relief structure |
| `cover.py` | `STEP/cover.step`, `STL/cover.stl`: top-down ventilated guard |
| `retainer.py` | `STEP/retainer.step`, `STL/retainer.stl`: top-down printed PCB land retainer; print four |
| `cable_clamp.py` | `STEP/cable_clamp.step`, `STL/cable_clamp.stl`: top-down insulated cable clamp; print two |
| `assembly.py` | `STEP/assembly.step`: installed nine-solid printed assembly |
| `pilot_coupon.py` | `STEP/pilot_coupon.step`, `STL/pilot_coupon.stl`: 2.45/2.55/2.65 mm blind pilot/boss ladder |
| `fit_coupon.py` | `STEP/fit_coupon.step`, `STL/fit_coupon.stl`: reduced-height board/dual-plug trial |
| `adapter_coupon.py` | `STEP/adapter_coupon.step`, `STL/adapter_coupon.stl`: adapter locating boss trial |
| `adapter_socket_coupon.py` | `STEP/adapter_socket_coupon.step`, `STL/adapter_socket_coupon.stl`: mating recess trial |

Local part XY shares installed XY except the generic retainer/clamp and small
adapter coupons. Assembly placements are in the geometry library. Its bottom
is the tape plane Z=0, housing underside Z=8, PCB backside allowance Z=18 and
roof top Z=28. Board, membrane, screws, cables and tape are omitted.

[Project guide](../README.md) records assembly, measurement, coupon, printing
and regeneration instructions. `checks/` reads saved artifacts independently;
`reports/` records geometry/mesh evidence; `views/` contains inspected snapshots.
