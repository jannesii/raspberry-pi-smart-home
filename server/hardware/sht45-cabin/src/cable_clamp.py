"""Draft cable_clamp: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import cable_clamp as make_part


@step(out="../STEP/cable_clamp.step")
@stl(out="../STL/cable_clamp.stl", mesh_tolerance=0.0002)
def cable_clamp():
    return make_part()


if __name__ == "__main__":
    cable_clamp()
