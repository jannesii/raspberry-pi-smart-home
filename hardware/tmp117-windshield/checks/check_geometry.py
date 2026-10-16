"""Saved STEP dimensional/keepout checks. Envelopes are allowances, not devices."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cadgen import read_step

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lib.geometry import bore, box_at

NAMES = ("base", "cover", "retainer", "cable_clamp", "pilot_coupon", "fit_coupon")
TOL = 1e-6


def overlap(a, b):
    volume = 0.0
    for first in a.solids():
        for second in b.solids():
            result = first.intersect(second)
            if result is not None:
                volume += (
                    sum(s.volume for s in result) if isinstance(result, list) else result.volume
                )
    return volume


def clear(a, b, label):
    value = overlap(a, b)
    assert value < TOL, f"{label}: {value} mm3 interference"
    return value


def main():
    shapes = {name: read_step(str(ROOT / f"STEP/{name}.step")) for name in NAMES}
    assembly = read_step(str(ROOT / "STEP/assembly.step"))
    report: dict[str, Any] = {
        "units": "mm",
        "status": "DRAFT: physical measurements outstanding",
        "overlap_tolerance_mm3": TOL,
        "parts": {},
        "checks": {},
    }
    for name, shape in shapes.items():
        assert shape.is_valid and len(shape.solids()) == 1 and shape.volume > 0
        assert abs(shape.bounding_box().min.Z) < TOL
        report["parts"][name] = {
            "valid": True,
            "solids": 1,
            "volume_mm3": shape.volume,
            "size_mm": list(shape.bounding_box().size),
        }
    solids = list(assembly.solids())
    assert assembly.is_valid and len(solids) == 8
    for i, a in enumerate(solids):
        for b in solids[i + 1 :]:
            clear(a, b, "printed-part mating")
    # Whole PCB lift prism with service parts removed, including under-tongue path.
    clear(shapes["base"], box_at(25.4, 17.78, 30, z=-0.1), "PCB vertical service path")
    clear(assembly, box_at(5.842, 4.953, 1, y=0.9525, z=-0.2), "backside tongue thermal path")
    clear(assembly, box_at(25.4, 17.78, 1.6, z=-0.05), "nominal PCB allowance")
    clear(assembly, box_at(8, 6, 8, z=2.5), "replaceable E30 pad allowance")
    # Both plugs: nominal board edge through x=20.7. Bend only beyond clamps.
    for sign in (-1, 1):
        clear(assembly, box_at(8, 8, 4, x=sign * 16.7, z=2.6), "mated plug allowance")
        clear(
            assembly, box_at(5.3, 4, 2.8, x=sign * 23.35, z=2.1), "straight pigtail slack allowance"
        )
    base = shapes["base"]
    # Independent ticket dimensions: 2.55 mm blind pilots, stock BT3x6.
    joints: list[tuple[float, float, float, float]] = [
        (x, y, 15, 17) for x in (-19, 19) for y in (-13, 13)
    ]
    joints += [(x, y, 6, 8) for x in (-10.16, 10.16) for y in (-13, 13)]
    joints += [(x, y, 4, 8) for x in (-27, 27) for y in (-5, 5)]
    for x, y, stop, top in joints:
        depth = min(4, stop - 1)
        clear(base, bore(2.55, depth - 0.1, x, y, stop - depth + 0.1), "pilot core")
        # Bottom of blind hole has a closed floor; radial wall exists.
        assert overlap(base, bore(2, 0.1, x, y, stop - depth - 0.2)) > 0.3
        assert overlap(base, bore(3.2, 1, x, y, stop - 1)) > 2
        assert top - 6 >= 1 and 2 <= stop - (top - 6) <= 4
    # Tools for cover/clamps clear all printed parts above screw heads.
    for x, y, _, top in joints[:4] + joints[8:]:
        clear(assembly, bore(6, 20, x, y, top + 0.01), "independent tool access")
    # Retainer tools require cover removal, a documented service step.
    cover_volume = shapes["cover"].volume
    installed_cover = max(
        (s for s in solids if abs(s.volume - cover_volume) < 0.001),
        key=lambda s: s.bounding_box().max.Z,
    )
    for x, y, _, top in joints[4:8]:
        for solid in solids:
            if solid is not installed_cover:
                clear(
                    solid, bore(6, 20, x, y, top + 0.01), "retainer tool access after cover removal"
                )
    # Actual saved clamp opening: floor Z=2, central roof underside Z=5.
    for x in (-27, 27):
        clear(assembly, box_at(7.8, 3.8, 2.98, x=x, z=2.01), "3 mm clamp opening")
        assert overlap(assembly, box_at(7.8, 3.8, 0.1, x=x, z=5.01)) > 2.9
        assert overlap(base, box_at(7.8, 3.8, 0.1, x=x, z=1.89)) > 2.9
        for y in (-2.1, 2.1):
            assert overlap(assembly, box_at(7.8, 0.1, 0.8, x=x, y=y, z=4.1)) > 0.6
    # Ensure actual installed solids correspond to exported parts at required datums.
    assert (
        abs(
            assembly.volume
            - (
                base.volume
                + cover_volume
                + 4 * shapes["retainer"].volume
                + 2 * shapes["cable_clamp"].volume
            )
        )
        < 0.001
    )
    assert abs(installed_cover.bounding_box().max.Z - 17) < TOL
    assert abs(installed_cover.bounding_box().min.Z - 2) < TOL
    for x, y in ((-19, -13), (-19, 13), (19, -13), (19, 13)):
        assert overlap(installed_cover, box_at(1, 1, 0.1, x=x + 2.5, y=y, z=15)) > 0.09
        assert overlap(base, box_at(1, 1, 0.1, x=x + 2.5, y=y, z=14.9)) > 0.09
    report["checks"] = {
        "eight_valid_assembly_solids": True,
        "printed_mating_overlap_mm3": 0,
        "vertical_PCB_lift_base_clear": True,
        "thermal_path_clear": True,
        "both_plug_and_slack_allowances_clear": True,
        "foam_allowance_clear": True,
        "pilot_diameter_mm": 2.55,
        "through_clearance_mm": 3.4,
        "BT3_length_mm": 6,
        "engagement_mm": [4, 4, 2],
        "minimum_screw_tip_above_base_underside_mm": 2,
        "cover_structural_stop_z_mm": 15,
        "tool_diameter_allowance_mm": 6,
        "clamp_opening_height_mm": 3,
        "clamp_opening_width_mm": 4,
    }
    report["not_verified"] = [
        "delivered PCB/plug/component/bearing-land fit",
        "cable gripping and actual bends",
        "contact force and physical retention",
        "glass curvature and tape attachment/removal",
        "slicer toolpaths",
        "installed thermal bias/lag and bus reliability",
    ]
    (ROOT / "reports/geometry.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
