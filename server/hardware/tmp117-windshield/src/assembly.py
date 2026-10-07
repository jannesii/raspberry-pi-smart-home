"""Printed parts only: no invented PCB, cable, foam or windshield geometry."""

from cadgen import build123d as bd, step
from carrier import carrier
from foam_bridge import foam_bridge
from lib.geometry import P


@step(out="../STEP/assembly.step")
def assembly():
    return bd.Compound(
        children=[carrier(), foam_bridge().moved(bd.Location((0, 0, P.stop_z)))],
        label="DRAFT_TMP117_holder_measure_before_print",
    )


if __name__ == "__main__":
    assembly()
