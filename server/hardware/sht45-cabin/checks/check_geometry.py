"""Independent checks of saved draft solids, installed fits and service allowances."""

from __future__ import annotations

import json
from pathlib import Path

from cadgen import build123d as bd, read_step
from check_redesign import NAMES

ROOT = Path(__file__).resolve().parents[1]
TOL = 1e-6


def box(w, d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z + h / 2) * bd.Box(w, d, h)


def cylinder(d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z) * bd.Cylinder(
        d / 2, h, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )


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
    volume = overlap(a, b)
    assert volume < TOL, f"{label}: {volume:.6f} mm3 interference"


def occupied(a, b, minimum, label):
    assert overlap(a, b) > minimum, f"Missing material: {label}"


def main():
    shapes = {name: read_step(str(ROOT / f"STEP/{name}.step")) for name in NAMES}
    assembly = read_step(str(ROOT / "STEP/assembly.step"))
    report = {
        "units": "mm",
        "status": "DRAFT; measured fit outstanding",
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
    assert assembly.is_valid and len(solids) == 9
    for i, first in enumerate(solids):
        for second in solids[i + 1 :]:
            clear(first, second, "printed assembly mating")
    # Compare each expected saved part at independently specified installed datums.
    expected = [
        shapes["adapter"],
        shapes["housing"].moved(bd.Location((0, 0, 8))),
        shapes["cover"].moved(bd.Location((0, 0, 28), (180, 0, 0))),
    ]
    for x in (-10.16, 10.16):
        for y in (-13, 13):
            expected.append(
                shapes["retainer"].moved(bd.Location((x, y, 24), (180, 0, 180 if y > 0 else 0)))
            )
    for x in (-33, 33):
        expected.append(shapes["cable_clamp"].moved(bd.Location((x, 0, 16), (180, 0, 0))))
    assert abs(assembly.volume - sum(part.volume for part in expected)) < 0.001
    for part in expected:
        assert (
            abs(overlap(assembly, part) - part.volume) < 0.001
        ), "Installed part differs from saved export"
    housing, cover = expected[1:3]
    # Membrane/tongue air column traverses adapter, housing and installed roof.
    clear(assembly, box(16, 12, 40, z=-1), "through membrane ventilation column")
    # Independent allowance for PCB includes candidate drill holes occupied by pins.
    pcb = box(25.4, 17.78, 1.6, z=18)
    for x in (-10.16, 10.16):
        for y in (-6.35, 6.35):
            pcb -= cylinder(2.5, 3, x, y, 17.5)
            occupied(housing, cylinder(3.9, 0.1, x, y, 17.9), 1, "PCB backside support")
    clear(assembly, pcb, "nominal drilled PCB")
    clear(assembly, box(16, 10, 4.8, z=18), "central component allowance")
    # Board lifts vertically after cover/retainers unplugged; short pins release first.
    clear(housing, box(25.4, 17.78, 30, z=18.81), "PCB lift toward cabin")
    clear(expected[0], box(25.4, 17.78, 30, z=18), "adapter clear of PCB lift")
    # Connector + mated-plug allowances on both ends, plus straight slack to clamps.
    for sign in (-1, 1):
        clear(assembly, box(8, 8, 6, x=sign * 16.7, z=18.1), "both mated plug allowances")
        clear(assembly, box(8.3, 4, 2.8, x=sign * 24.85, z=10.1), "straight cable exit allowance")
        # Downward transition is outside PCB/plug; allowance is not a swept bend.
        clear(assembly, box(6, 4, 14, x=sign * 25, z=10.1), "open transition corridor")
    # No stagnant closed shell: roof windows and lateral openings stay connected to air.
    for x, y, w, d in (
        (0, 0, 16, 12),
        (-14, 0, 8, 19.6),
        (14, 0, 8, 20),
        (0, -13, 15.6, 5.6),
        (0, 13, 15.6, 5.6),
    ):
        clear(cover, box(w, d, 2.1, x, y, 25.95), "roof ventilation window")
    for y in (-18, 18):
        clear(assembly, box(14, 4, 5, y=y, z=10.5), "side air inlet")
    # Pilot bore/core and radial wall in each recipient. Screw lengths are BT3x6.
    joints = [(expected[0], x, y, 8, 10, 5) for x in (-30, 30) for y in (-15, 15)]
    joints += [(housing, x, y, 26, 28, 5) for x in (-19, 19) for y in (-16, 16)]
    joints += [(housing, x, y, 22, 24, 5) for x in (-10.16, 10.16) for y in (-13, 13)]
    joints += [(housing, x, y, 12, 16, 3) for x in (-33, 33) for y in (-6, 6)]
    for recipient, x, y, stop, top, depth in joints:
        clear(
            recipient,
            cylinder(2.55, depth - 0.02, x, y, stop - depth + 0.01),
            "2.55 mm blind pilot",
        )
        occupied(recipient, cylinder(2, 0.1, x, y, stop - depth - 0.1), 0.3, "closed pilot floor")
        occupied(recipient, cylinder(3.2, 0.5, x, y, stop - 0.5), 1, "pilot radial wall")
        assert top - 6 > stop - depth and 2 <= stop - (top - 6) <= 4
        # 3 mm screw thread envelope never reaches the board or membrane.
        clear(pcb, cylinder(3, 6, x, y, top - 6), "screw clear of PCB")
    # Tool and head allowances: clamps independent, retainers/mounts after cover removal.
    without_cover = bd.Compound(children=[part for part in expected if part is not cover])
    for i, (_, x, y, _, top, _) in enumerate(joints):
        service = without_cover if i < 4 or 8 <= i < 12 else assembly
        clear(service, cylinder(6, 20, x, y, top + 0.01), "driver access")
        clear(service, cylinder(7, 2, x, y, top + 0.01), "screw head allowance")
    for x in (-33, 33):
        clear(assembly, box(7.8, 5.8, 2.98, x=x, z=10.01), "clamp 6 x 3 mm opening")
        occupied(assembly, box(7.8, 5.8, 0.1, x=x, z=13.01), 4, "clamp roof")
        occupied(housing, box(7.8, 5.8, 0.1, x=x, z=9.89), 4, "clamp floor")
    # Adapter locating spigots fit recessed housing without collision or tape access.
    for x in (-30, 30):
        for y in (-15, 15):
            occupied(expected[0], cylinder(3.9, 0.7, x, y, 8), 1, "adapter locating spigot")
            clear(housing, cylinder(4.4, 0.99, x, y, 8), "housing locating recess")
            occupied(housing, cylinder(4.4, 0.1, x, y, 9.01), 0.5, "housing recess shoulder")
    for y in (-14.5, 14.5):
        occupied(expected[0], box(64, 7, 0.1, y=y), 44, "underside tape rail")
    # Mating coupon pair carries the same recess/spigot and stock fastener stack.
    clear(
        shapes["adapter_coupon"],
        shapes["adapter_socket_coupon"].moved(bd.Location((0, 0, 8))),
        "adapter coupons mating",
    )
    # Board coupon preserves functional retainer/clamp stacks; only cover towers go.
    coupon = shapes["fit_coupon"]
    assert abs(coupon.bounding_box().max.Z - 14) < TOL
    coupon_parts = [coupon]
    for x in (-10.16, 10.16):
        for y in (-13, 13):
            coupon_parts.append(
                shapes["retainer"].moved(bd.Location((x, y, 16), (180, 0, 180 if y > 0 else 0)))
            )
            clear(coupon, cylinder(2.55, 4.98, x, y, 9.01), "coupon retainer pilot")
            occupied(coupon, cylinder(3.2, 0.1, x, y, 13.9), 0.2, "coupon retainer stop")
    for x in (-33, 33):
        coupon_parts.append(shapes["cable_clamp"].moved(bd.Location((x, 0, 8), (180, 0, 0))))
    for i, first in enumerate(coupon_parts):
        for second in coupon_parts[i + 1 :]:
            clear(first, second, "coupon service parts mating")
    clear(coupon, box(25.4, 17.78, 30, z=10.81), "coupon board lift")
    # Coupon pilot ladder is independently checked, not just main source constants.
    for x, diameter in ((-12, 2.45), (0, 2.55), (12, 2.65)):
        clear(shapes["pilot_coupon"], cylinder(diameter, 4.98, x=x, z=3.01), "coupon pilot")
    report["checks"] = {
        "assembly_solids": 9,
        "mating_and_saved_part_agreement": True,
        "board_lift_and_two_plug_allowances": True,
        "membrane_through_air_window_mm": [16, 12],
        "roof_window_area_mm2": 683.52,
        "roof_window_fraction": 683.52 / (44 * 40),
        "board_back_above_adapter_underside_mm": 18,
        "housing_air_gap_above_adapter_mm": 6,
        "pilot_mm": 2.55,
        "through_hole_mm": 3.4,
        "BT3_length_mm": 6,
        "engagement_mm_adapter_cover_retainer_clamp": [4, 4, 4, 2],
        "pilot_bottom_margin_mm": 1,
        "tool_diameter_mm": 6,
        "head_diameter_mm": 7,
        "adapter_recess_radial_clearance_mm": 0.2,
        "adapter_spigot_end_clearance_mm": 0.2,
        "clamp_opening_mm": [6, 3],
        "tape_zones_mm": [64, 7, 2],
    }
    report["not_verified"] = [
        "6174 revision, lands, membrane, underside and plugged cable fit",
        "actual cable bends, clamp grip and retention force",
        "trim/site airflow and tape removal",
        "slicer toolpaths",
        "physical fit, service cycles and installed sensing bias/lag",
    ]
    (ROOT / "reports/geometry.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Saved STEP mating, ventilation, plug, service, fastener and coupon checks passed")


if __name__ == "__main__":
    main()
