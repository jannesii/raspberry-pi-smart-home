"""Draft adapter_socket_coupon: maintained exact STEP and bed-oriented millimetre STL."""

from cadgen import step, stl
from lib.geometry import adapter_socket_coupon as make_part


@step(out="../STEP/adapter_socket_coupon.step")
@stl(out="../STL/adapter_socket_coupon.stl", mesh_tolerance=0.0002)
def adapter_socket_coupon():
    return make_part()


if __name__ == "__main__":
    adapter_socket_coupon()
