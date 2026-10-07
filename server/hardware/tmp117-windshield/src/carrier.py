from cadgen import step, stl
from lib.geometry import carrier as make_carrier


@step(out="../STEP/carrier.step")
@stl(out="../STL/carrier.stl", mesh_tolerance=0.001)
def carrier():
    return make_carrier()


if __name__ == "__main__":
    carrier()
