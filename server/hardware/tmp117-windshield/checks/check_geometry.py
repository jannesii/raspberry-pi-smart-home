"""Check saved STEP artifacts; keepouts are design tests, not physical fit claims."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cadgen import read_step

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lib.geometry import P, box_at, layout


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


def main():
    P.validate()
    carrier = read_step(str(ROOT / "STEP/carrier.step"))
    bridge = read_step(str(ROOT / "STEP/foam_bridge.step"))
    assembly = read_step(str(ROOT / "STEP/assembly.step"))
    report = {"units": "mm", "status": "DRAFT: physical measurements outstanding", "parts": {}}
    for name, shape, count in [
        ("carrier", carrier, 1),
        ("foam_bridge", bridge, 1),
        ("assembly", assembly, 2),
    ]:
        assert shape.is_valid and len(shape.solids()) == count and shape.volume > 0
        bounds = shape.bounding_box()
        report["parts"][name] = {
            "valid": shape.is_valid,
            "solids": len(shape.solids()),
            "volume_mm3": shape.volume,
            "size_mm": list(bounds.size),
        }
    assert abs(carrier.bounding_box().min.Z) < 1e-6
    assert abs(bridge.bounding_box().min.Z) < 1e-6
    # Entire nominal PCB footprint is unobstructed, stronger than tongue-only clearance.
    keepout = box_at(P.board_length, P.board_width, P.stop_z, z=-0.01)
    pcb_overlap = overlap(carrier, keepout)
    assert pcb_overlap < 1e-6
    installed_bridge = bridge.translate((0, 0, P.stop_z))
    mate_overlap = overlap(carrier, installed_bridge)
    assert mate_overlap < 1e-6
    board = layout()
    tongue_w = (
        board["tongue_slot_centerlines"][1][2]
        - board["tongue_slot_centerlines"][1][0]
        + board["slot_width"]
    )
    tongue_d = (
        board["tongue_slot_centerlines"][0][3]
        - board["tongue_slot_centerlines"][0][1]
        + board["slot_width"]
    )
    tongue_y = (12.065 + 7.62) / 2 - board["sensor_xy"][1]
    tongue = box_at(tongue_w, tongue_d, P.stop_z, y=tongue_y, z=-0.01)
    assert overlap(carrier, tongue) < 1e-6
    # Exact open corridor above saddle: arbitrary design envelope, NOT a measured cable.
    corridor = box_at(
        P.cable_reach + P.rail,
        6.0,
        6.0,
        x=P.board_length / 2 + (P.cable_reach + P.rail) / 2,
        z=P.base + P.clearance,
    )
    cable_overlap = overlap(carrier, corridor) + overlap(installed_bridge, corridor)
    assert cable_overlap < 1e-6
    foam = box_at(
        P.foam_width,
        P.foam_length,
        P.foam_thickness * (1 - P.foam_compression),
        z=P.stop_z - P.foam_thickness * (1 - P.foam_compression),
    )
    assert overlap(carrier, foam) < 1e-6
    assert overlap(installed_bridge, foam) < 1e-6
    report["checks"] = {
        "pcb_keepout_overlap_mm3": pcb_overlap,
        "mating_overlap_mm3": mate_overlap,
        "tongue_keepout_clear": True,
        "trial_cable_corridor_overlap_mm3": cable_overlap,
        "foam_space_clear": True,
        "bridge_hole_diameter_mm": P.peg_diameter + P.peg_clearance,
        "peg_diameter_mm": P.peg_diameter,
        "glass_edge_chamfer_mm": P.edge_break,
        "base_thickness_mm": P.base,
        "bridge_thickness_mm": P.bridge_thickness,
        "hard_stop_height_mm": P.stop_z,
        "trial_foam_compression_mm": P.foam_thickness * P.foam_compression,
    }
    report["not_verified"] = [
        "physical board/cable fit",
        "foam contact force",
        "windshield curvature",
        "adhesive retention and thermal bias/lag",
        "slicer toolpaths",
    ]
    (ROOT / "reports/geometry.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
