from cadgen import step, stl
from lib.geometry import bridge


@step(out="../STEP/foam_bridge.step")
@stl(out="../STL/foam_bridge.stl", mesh_tolerance=0.001)
def foam_bridge():
    return bridge()


if __name__ == "__main__":
    foam_bridge()
