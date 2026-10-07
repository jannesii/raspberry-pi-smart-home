# Model catalog

- `carrier.py`: open-back carrier, stop posts, slip pegs and cable saddle;
  exports `../STEP/carrier.step` and `../STL/carrier.stl`.
- `foam_bridge.py`: flat removable foam bridge;
  exports `../STEP/foam_bridge.step` and `../STL/foam_bridge.stl`.
- `assembly.py`: positions both printed parts on their mating hard stops;
  exports `../STEP/assembly.step`; calls both child models.
- `lib/geometry.py`: shared millimetre parameters and shape factories.
- `lib/board-layout.json`: extracted vendor layout facts and provenance.

See [the mounting and measurement guide](../README.md) before printing.
