"""Installed printed assembly; excludes unmeasured board, cables, tape and screws."""

from adapter import adapter
from cable_clamp import cable_clamp
from cadgen import build123d as bd, step
from cover import cover
from housing import housing
from lib.geometry import (
    CLAMPS,
    P,
    installed_clamp,
    installed_cover,
    installed_housing,
    installed_retainer,
)
from retainer import retainer


@step(out="../STEP/assembly.step")
def assembly():
    parts = [adapter(), installed_housing(housing()), installed_cover(cover())]
    for x, y in P.retainers:
        part = installed_retainer(retainer(), x, y)
        part.label = f"retainer_{x}_{y}"
        parts.append(part)
    for x in CLAMPS:
        part = installed_clamp(cable_clamp(), x)
        part.label = f"cable_clamp_{x}"
        parts.append(part)
    return bd.Compound(children=parts, label="DRAFT_SHT45_6174_measurement_required")


if __name__ == "__main__":
    assembly()
