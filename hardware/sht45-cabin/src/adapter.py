"""Draft adapter: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import adapter as make_part


@step(out="../STEP/adapter.step")
@stl(out="../STL/adapter.stl", mesh_tolerance=0.0002)
def adapter():
    return make_part()


if __name__ == "__main__":
    adapter()
