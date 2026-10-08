"""Draft retainer: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import retainer as make_part


@step(out="../STEP/retainer.step")
@stl(out="../STL/retainer.stl", mesh_tolerance=0.0002)
def retainer():
    return make_part()


if __name__ == "__main__":
    retainer()
