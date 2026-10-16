"""Draft pilot_coupon: maintained STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import pilot_coupon as make_part


@step(out="../STEP/pilot_coupon.step")
@stl(out="../STL/pilot_coupon.stl", mesh_tolerance=0.001)
def pilot_coupon():
    return make_part()


if __name__ == "__main__":
    pilot_coupon()
