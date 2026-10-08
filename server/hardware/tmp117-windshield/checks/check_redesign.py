"""Saved-artifact contract for spec #19, ticket #20."""

from pathlib import Path

from cadgen import read_step

ROOT = Path(__file__).resolve().parents[1]


def main():
    for name in ("base", "cover", "retainer", "cable_clamp", "pilot_coupon", "fit_coupon"):
        path = ROOT / "STEP" / f"{name}.step"
        assert path.exists(), f"Missing redesigned artifact: {name}"
        shape = read_step(str(path))
        assert shape.is_valid and len(shape.solids()) == 1 and shape.volume > 0
    assembly = read_step(str(ROOT / "STEP/assembly.step"))
    assert len(assembly.solids()) == 8, "base, cover, four retainers, two clamps"
    print("Redesign artifact contract passed")


if __name__ == "__main__":
    main()
