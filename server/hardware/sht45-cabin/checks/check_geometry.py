"""Check saved STEP, explicit keepouts, tape planes and measurement-release gate."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

from cadgen import read_step

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lib.geometry import P, box_at, cylinder, layout


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


def must_reject(p):
    try:
        p.validate()
    except AssertionError:
        return
    raise AssertionError("An unsafe or incomplete parameter set was accepted")


def check_gates():
    must_reject(replace(P, ready_for_final_print=True))
    # Synthetic fixture ONLY: demonstrates gate logic, never physical evidence.
    values = {
        "received_layout_confirmed": True,
        "trim_fit_confirmed": True,
        "tapped_retention_confirmed": True,
        "ready_for_final_print": True,
        "pcb_thickness_measured": 1.6,
        "backside_protrusion_measured": 0.4,
        "top_free_land_radius_measured": 2.5,
        "bottom_free_land_radius_measured": 2.5,
        "membrane_width_measured": 2.0,
        "membrane_depth_measured": 2.0,
        "membrane_x_measured": 0.0,
        "membrane_y_measured": 0.0,
        "mated_plug_reach_measured": 4.0,
        "plug_lowest_above_back_measured": -1.0,
        "cable_width_measured": 3.0,
        "cable_bend_radius_measured": 4.0,
        "tie_width_measured": 2.5,
        "tie_thickness_measured": 1.0,
        "screw_thread_diameter_measured": 2.0,
        "screw_under_head_length_measured": 6.0,
        "screw_head_radius_measured": 2.0,
        "calibrated_pilot_diameter_measured": 1.6,
        "mounting_tape_measured": 1.5,
    }
    fixture = replace(P, **values)
    fixture.validate()
    for name in values:
        if name.endswith("_measured"):
            must_reject(replace(fixture, **{name: None}))
    for name in ("received_layout_confirmed", "trim_fit_confirmed", "tapped_retention_confirmed"):
        must_reject(replace(fixture, **{name: False}))
    for changes in (
        {"screw_under_head_length_measured": 10.0},
        {"screw_thread_diameter_measured": 3.0},
        {"bottom_free_land_radius_measured": 1.0},
        {"cable_width_measured": 8.0},
        {"mated_plug_reach_measured": 25.0},
        {"membrane_x_measured": 4.0},
        {"mounting_tape_measured": 0.5},
        {"pcb_thickness_measured": float("nan")},
        {"membrane_width_measured": 0.0},
        {"cable_bend_radius_measured": 0.0},
    ):
        must_reject(replace(fixture, **changes))


def main():
    P.validate()
    check_gates()
    part = read_step(str(ROOT / "STEP/holder.step"))
    assert part.is_valid and len(part.solids()) == 1 and part.volume > 0
    bounds = part.bounding_box()
    assert abs(bounds.min.Z) < 1e-6
    assert abs(bounds.max.Z - P.board_back_z) < 1e-6
    # Unobstructed vertical column includes tongue, membrane and air above it.
    air = box_at(
        P.sensor_keepout_width,
        P.sensor_keepout_depth,
        P.board_back_z + 20,
        P.sensor_x,
        P.sensor_y,
        -0.01,
    )
    air_overlap = overlap(part, air)
    assert air_overlap < 1e-6
    # No printed cover above any part of the board; screws intentionally omitted.
    above = box_at(P.board_length, P.board_width, 20, z=P.board_back_z + 0.001)
    assert overlap(part, above) < 1e-6
    # Below PCB: contacts restricted to the four declared corner post footprints.
    backside = box_at(
        P.board_length, P.board_width, P.board_back_z - P.base - 0.002, z=P.base + 0.001
    )
    for x, y in P.posts:
        backside -= cylinder(P.post_diameter / 2 + 0.001, P.board_back_z + 1, x, y)
    assert overlap(part, backside) < 1e-6
    contact_area = 0.0
    for x, y in P.posts:
        foot = cylinder(P.post_diameter / 2, 0.1, x, y)
        assert abs(overlap(part, foot) - foot.volume) < 1e-6
        pilot = cylinder(
            P.pilot_diameter / 2 - 0.001, P.pilot_depth, x, y, P.board_back_z - P.pilot_depth
        )
        assert overlap(part, pilot) < 1e-6
        contact = box_at(
            P.post_diameter + 0.1, P.post_diameter + 0.1, 0.01, x, y, P.board_back_z - 0.01
        )
        area = overlap(part, contact) / 0.01
        expected = math.pi / 4 * (P.post_diameter**2 - P.pilot_diameter**2)
        assert abs(area - expected) < 1e-4
        contact_area += area
    # Two guaranteed tape rectangles, away from cable-tie loops.
    tape_area = 0.0
    for y in (-11.3, 11.3):
        pad = box_at(30.0, 6.0, 0.1, y=y)
        assert abs(overlap(part, pad) - pad.volume) < 1e-6
        under = box_at(30.0, 6.0, 0.1, y=y, z=-0.101)
        assert overlap(part, under) < 1e-6
        tape_area += 180.0
    corridor = box_at(
        P.saddle_start + P.saddle_length - P.board_length / 2,
        P.cable_channel_width,
        P.board_back_z + 10,
        x=(P.board_length / 2 + P.saddle_start + P.saddle_length) / 2,
        z=P.base + P.clearance,
    )
    assert overlap(part, corridor) < 1e-6
    for x in P.tie_stations:
        for y in (-P.tie_y, P.tie_y):
            assert overlap(part, box_at(2.5, 1.7, P.base + 2, x, y, -1)) < 1e-6
    report = {
        "units": "mm",
        "status": "DRAFT: physical measurements outstanding",
        "step_sha256": hashlib.sha256((ROOT / "STEP/holder.step").read_bytes()).hexdigest(),
        "parts": {
            "holder": {
                "valid": part.is_valid,
                "solids": len(part.solids()),
                "volume_mm3": part.volume,
                "size_mm": list(bounds.size),
            }
        },
        "checks": {
            "sensor_air_column_overlap_mm3": air_overlap,
            "air_column_xy_mm": [P.sensor_keepout_width, P.sensor_keepout_depth],
            "base_aperture_xy_mm": [P.aperture_width, P.aperture_depth],
            "no_printed_cover_above_pcb": True,
            "only_corner_posts_between_base_and_board": True,
            "board_back_above_tape_plane_mm": P.board_back_z,
            "board_back_above_base_top_mm": P.board_back_z - P.base,
            "total_nominal_contact_area_mm2": contact_area,
            "flat_tape_rectangles_mm": [30.0, 6.0],
            "total_tape_area_mm2": tape_area,
            "pilot_holes": {"count": 4, "diameter_mm": P.pilot_diameter, "depth_mm": P.pilot_depth},
            "cable_channel_width_mm": P.cable_channel_width,
            "tie_slots_through": True,
            "release_gate_negative_cases_pass": True,
        },
        "candidate_layout": layout()["applicability"],
        "not_verified": [
            "6174 board revision/fit and membrane geometry",
            "mated cable bend and clearance",
            "printed/tapped pilot tolerance and nylon screw retention",
            "adhesive/trim compatibility, curvature and tape removal",
            "thermal bias, airflow response, PETG creep and vibration",
            "slicer paths and physical print",
        ],
    }
    (ROOT / "reports/geometry.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
