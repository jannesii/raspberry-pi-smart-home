"""Draft cover: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import cover as make_part


@step(out="../STEP/cover.step")
@stl(out="../STL/cover.stl", mesh_tolerance=0.0002)
def cover():
    return make_part()


if __name__ == "__main__":
    cover()
