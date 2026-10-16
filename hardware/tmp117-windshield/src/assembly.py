"""Installed printed parts; excludes unmeasured board, tape, foam and cables."""

from base import base
from cable_clamp import cable_clamp
from cadgen import build123d as bd, step
from cover import cover
from lib.geometry import CLAMPS, RETAINERS, installed_clamp, installed_cover, installed_retainer
from retainer import retainer


@step(out="../STEP/assembly.step")
def assembly():
    parts = [base(), installed_cover(cover())]
    for x, y in RETAINERS:
        part = installed_retainer(retainer(), x, y)
        part.label = f"retainer_{x}_{y}"
        parts.append(part)
    for x, _ in CLAMPS:
        part = installed_clamp(cable_clamp(), x)
        part.label = f"clamp_{x}"
        parts.append(part)
    return bd.Compound(children=parts, label="DRAFT_TMP117_measurement_required")


if __name__ == "__main__":
    assembly()
