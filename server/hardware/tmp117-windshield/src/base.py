"""Draft base: maintained STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import base as make_part


@step(out="../STEP/base.step")
@stl(out="../STL/base.stl", mesh_tolerance=0.001)
def base():
    return make_part()


if __name__ == "__main__":
    base()
