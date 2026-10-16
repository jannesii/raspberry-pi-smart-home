"""Draft fit_coupon: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import fit_coupon as make_part


@step(out="../STEP/fit_coupon.step")
@stl(out="../STL/fit_coupon.stl", mesh_tolerance=0.0002)
def fit_coupon():
    return make_part()


if __name__ == "__main__":
    fit_coupon()
