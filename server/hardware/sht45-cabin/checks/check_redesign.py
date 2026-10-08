"""Agreed saved-artifact seam for spec #19 / ticket #21."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "adapter",
    "housing",
    "cover",
    "retainer",
    "cable_clamp",
    "pilot_coupon",
    "fit_coupon",
    "adapter_coupon",
    "adapter_socket_coupon",
)


def main():
    from cadgen import read_step

    for name in NAMES:
        path = ROOT / "STEP" / f"{name}.step"
        assert path.exists(), f"Missing redesigned artifact: {name}"
        shape = read_step(str(path))
        assert shape.is_valid and len(shape.solids()) == 1 and shape.volume > 0
    assembly = read_step(str(ROOT / "STEP/assembly.step"))
    assert assembly.is_valid and len(assembly.solids()) == 9
    print("Nine-part installed assembly and nine printable artifacts passed")


if __name__ == "__main__":
    main()
