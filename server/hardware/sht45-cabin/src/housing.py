"""Draft housing: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import housing as make_part


@step(out="../STEP/housing.step")
@stl(out="../STL/housing.stl", mesh_tolerance=0.0002)
def housing():
    return make_part()


if __name__ == "__main__":
    housing()
