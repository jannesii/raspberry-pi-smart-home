# Model catalog

| Entrypoint | Outputs | Role |
| --- | --- | --- |
| `holder.py` | `../STEP/holder.step`, `../STL/holder.stl` | Single open-frame PETG draft in print orientation |

Shared dimensions and gates live in `lib/geometry.py`; provenance and
candidate manufacturer layout live in `lib/board-layout.json`.
Millimetres; XY centred on the candidate PCB; +Z points toward cabin air.
Holder tape plane: Z=0; base top: Z=2; PCB backside support plane: Z=12.
Converted hole positions: (±10.16, ±6.35). Right connector: CONN3, exiting +X.

`holder()` is an ordinary parameterized factory. The decorated entrypoint
exports one configuration. All physical interfaces start unmeasured; use the
[main guide](../README.md) before release. No purchased board/fastener/cable
stand-in or separate assembly is exported. STEP and STL represent one part.
