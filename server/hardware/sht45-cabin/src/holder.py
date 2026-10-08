from cadgen import step, stl
from lib.geometry import holder as make_holder


@step(out="../STEP/holder.step")
@stl(out="../STL/holder.stl", mesh_tolerance=0.001)
def holder():
    return make_holder()


if __name__ == "__main__":
    holder()
