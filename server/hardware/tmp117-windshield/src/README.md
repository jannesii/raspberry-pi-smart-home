# Replacement draft model catalog

Each part entrypoint exports matching `../STEP/<stem>.step` and
`../STL/<stem>.stl`; STLs are millimetres and already oriented on the bed.

| Entrypoint | Role | Per mount |
| --- | --- | ---: |
| `base.py` | Taped open-back structural base and blind-pilot stops | 1 |
| `cover.py` | Airflow shield, printed roof down | 1 |
| `retainer.py` | Corner-land finger, printed top down | 4 |
| `cable_clamp.py` | Cable bridge, printed top down | 2 |
| `pilot_coupon.py` | Three pilot/boss trials, left 2.45 / centre 2.55 / right 2.65 | trial |
| `fit_coupon.py` | Reduced-height board/plug/contact-stack frame | trial |
| `assembly.py` | Installed placement of eight printed parts; STEP only | review |

`assembly.py` calls its children and preserves shared geometry via `.moved()`.
`lib/geometry.py` contains parameters, factories and installation transforms;
`lib/board-layout.json` retains manufacturer nominal facts/provenance.

All outputs remain **DRAFT** pending #22 measurements and #23 finalization.
See [assembly, coupons and regeneration](../README.md).

## Saved outputs

| Model | Exact CAD | Printable mesh |
| --- | --- | --- |
| Base | [base.step](../STEP/base.step) | [base.stl](../STL/base.stl) |
| Cover | [cover.step](../STEP/cover.step) | [cover.stl](../STL/cover.stl) |
| Retainer | [retainer.step](../STEP/retainer.step) | [retainer.stl](../STL/retainer.stl) |
| Cable clamp | [cable_clamp.step](../STEP/cable_clamp.step) | [cable_clamp.stl](../STL/cable_clamp.stl) |
| Pilot coupon | [pilot_coupon.step](../STEP/pilot_coupon.step) | [pilot_coupon.stl](../STL/pilot_coupon.stl) |
| Fit coupon | [fit_coupon.step](../STEP/fit_coupon.step) | [fit_coupon.stl](../STL/fit_coupon.stl) |
| Installed assembly | [assembly.step](../STEP/assembly.step) | — |
