"""Open PETG holder, mm; PCB centre XY, tape-pad underside Z=0.

Candidate manufacturer layout belongs to 5665; compare with received 6174.
None values are measurement gates, never silently claimed as hardware facts.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from cadgen import build123d as bd


def layout():
    return json.loads(Path(__file__).with_name("board-layout.json").read_text())


@dataclass(frozen=True)
class Parameters:
    # Nominal candidate layout: update these if the delivered board differs.
    board_length: float = 25.4
    board_width: float = 17.78
    hole_pitch_x: float = 20.32
    hole_pitch_y: float = 12.7
    hole_diameter: float = 2.5
    base: float = 2.0
    frame_width: float = 33.4
    frame_depth: float = 30.0
    aperture_width: float = 26.6
    aperture_depth: float = 7.7
    corner: float = 1.0
    edge_break: float = 0.4
    board_back_z: float = 12.0  # design standoff, NOT board height
    post_diameter: float = 4.9
    pilot_diameter: float = 1.6  # trial M2 tap-drill; calibrate printed hole
    pilot_depth: float = 6.0
    saddle_start: float = 14.0
    saddle_length: float = 26.0
    saddle_width: float = 16.0
    tie_stations: tuple[float, float] = (30.0, 36.0)
    tie_y: float = 4.6
    tie_slot_width: float = 3.2
    tie_slot_depth: float = 2.4
    tie_slot_radius: float = 0.6
    clearance: float = 0.6
    sensor_keepout_width: float = 6.4  # design corridor around entire tongue
    sensor_keepout_depth: float = 6.4
    sensor_x: float = 0.0
    sensor_y: float = 0.0
    # Physical interfaces. Contact radii refer to the smallest of all four lands.
    received_layout_confirmed: bool = False
    pcb_thickness_measured: float | None = None
    backside_protrusion_measured: float | None = None
    top_free_land_radius_measured: float | None = None
    bottom_free_land_radius_measured: float | None = None
    membrane_width_measured: float | None = None
    membrane_depth_measured: float | None = None
    membrane_x_measured: float | None = None
    membrane_y_measured: float | None = None
    mated_plug_reach_measured: float | None = None
    plug_lowest_above_back_measured: float | None = None  # signed offset
    cable_width_measured: float | None = None
    cable_bend_radius_measured: float | None = None
    tie_width_measured: float | None = None
    tie_thickness_measured: float | None = None
    screw_thread_diameter_measured: float | None = None
    screw_under_head_length_measured: float | None = None
    screw_head_radius_measured: float | None = None
    calibrated_pilot_diameter_measured: float | None = None
    mounting_tape_measured: float | None = None
    trim_fit_confirmed: bool = False
    tapped_retention_confirmed: bool = False
    ready_for_final_print: bool = False

    @property
    def posts(self):
        return [
            (x, y)
            for x in (-self.hole_pitch_x / 2, self.hole_pitch_x / 2)
            for y in (-self.hole_pitch_y / 2, self.hole_pitch_y / 2)
        ]

    @property
    def cable_channel_width(self):
        return 2 * self.tie_y - self.tie_slot_depth

    def validate(self):
        if self.ready_for_final_print:
            assert self.received_layout_confirmed, "Compare received 6174 with candidate layout"
            assert self.trim_fit_confirmed and self.tapped_retention_confirmed
            assert all(
                value is not None
                for name, value in vars(self).items()
                if name.endswith("_measured")
            ), "Complete all physical measurements before final-print release"
        assert all(math.isfinite(v) for v in vars(self).values() if isinstance(v, (int, float)))
        assert self.base >= 1.6 and self.corner > self.edge_break > 0
        assert self.board_back_z > self.base + self.clearance
        assert 0 < self.pilot_depth < self.board_back_z - self.base
        assert self.pilot_diameter > 0
        assert (self.post_diameter - self.pilot_diameter) / 2 >= 1.6 - 1e-9
        assert self.aperture_width > self.board_length
        assert self.aperture_depth > self.sensor_keepout_depth + 2 * self.clearance
        assert self.frame_width - self.aperture_width >= 2 * 1.6
        assert self.frame_depth - self.aperture_depth >= 2 * 1.6
        assert self.saddle_start < self.frame_width / 2
        assert self.saddle_width / 2 - self.tie_y - self.tie_slot_depth / 2 >= 1.2
        for x, y in self.posts:
            assert abs(x) + self.post_diameter / 2 <= self.board_length / 2
            assert abs(y) + self.post_diameter / 2 <= self.board_width / 2
            assert abs(y) + self.post_diameter / 2 > self.aperture_depth / 2 + 0.8
            assert abs(y) - self.post_diameter / 2 >= self.aperture_depth / 2, (
                "Keep each post entirely on a rail for clean, support-free meshing"
            )
        for x in self.tie_stations:
            assert x - self.tie_slot_width / 2 > self.saddle_start + 1.6
            assert x + self.tie_slot_width / 2 < self.saddle_start + self.saddle_length - 1.6
        for name, value in vars(self).items():
            if name.endswith("_measured") and value is not None:
                assert math.isfinite(value)
                if name not in (
                    "membrane_x_measured",
                    "membrane_y_measured",
                    "plug_lowest_above_back_measured",
                ):
                    assert value >= 0, name
                if name in (
                    "pcb_thickness_measured",
                    "membrane_width_measured",
                    "membrane_depth_measured",
                    "cable_width_measured",
                    "cable_bend_radius_measured",
                    "tie_width_measured",
                    "tie_thickness_measured",
                    "screw_thread_diameter_measured",
                    "screw_under_head_length_measured",
                    "screw_head_radius_measured",
                    "calibrated_pilot_diameter_measured",
                    "mounting_tape_measured",
                ):
                    assert value > 0, name
        if self.pcb_thickness_measured is not None:
            assert self.pcb_thickness_measured > 0
        if self.backside_protrusion_measured is not None:
            assert (
                self.board_back_z - self.backside_protrusion_measured >= self.base + self.clearance
            )
        if self.bottom_free_land_radius_measured is not None:
            assert self.bottom_free_land_radius_measured >= self.post_diameter / 2
        if (
            self.top_free_land_radius_measured is not None
            and self.screw_head_radius_measured is not None
        ):
            assert self.top_free_land_radius_measured >= self.screw_head_radius_measured
        if self.screw_head_radius_measured is not None:
            assert (
                self.hole_diameter / 2 < self.screw_head_radius_measured <= self.post_diameter / 2
            )
        if self.screw_thread_diameter_measured is not None:
            assert abs(self.screw_thread_diameter_measured - 2.0) <= 0.1, "M2 tapped interface only"
            assert self.screw_thread_diameter_measured + 0.2 <= self.hole_diameter
        if (
            self.pcb_thickness_measured is not None
            and self.screw_under_head_length_measured is not None
        ):
            engagement = self.screw_under_head_length_measured - self.pcb_thickness_measured
            assert 3.0 <= engagement <= self.pilot_depth - self.clearance
        if self.calibrated_pilot_diameter_measured is not None:
            assert abs(self.calibrated_pilot_diameter_measured - self.pilot_diameter) <= 0.1
        if self.cable_width_measured is not None:
            assert self.cable_width_measured + 2 * self.clearance <= self.cable_channel_width
        if self.tie_width_measured is not None:
            assert self.tie_width_measured + 0.4 <= self.tie_slot_width
        if self.tie_thickness_measured is not None:
            assert self.tie_thickness_measured + 0.4 <= self.tie_slot_depth
        if self.mounting_tape_measured is not None and self.tie_thickness_measured is not None:
            assert self.mounting_tape_measured >= self.tie_thickness_measured + 0.4, (
                "Tape must clear underside tie straps; keep locking heads on top/side"
            )
        if self.plug_lowest_above_back_measured is not None:
            assert (
                self.board_back_z + self.plug_lowest_above_back_measured
                >= self.base + self.clearance
            )
        if (
            self.mated_plug_reach_measured is not None
            and self.cable_bend_radius_measured is not None
        ):
            # Conservative horizontal routing allowance, not a cable bend simulation.
            assert (
                self.board_length / 2
                + self.mated_plug_reach_measured
                + 2 * self.cable_bend_radius_measured
                + self.clearance
                <= self.tie_stations[0] - self.tie_slot_width / 2
            ), "Extend saddle and move ties if relaxed cable routing needs more space"
        for axis, limit, centre in (
            ("width", self.sensor_keepout_width, self.sensor_x),
            ("depth", self.sensor_keepout_depth, self.sensor_y),
        ):
            size = getattr(self, f"membrane_{axis}_measured")
            coord = getattr(self, f"membrane_{'x' if axis == 'width' else 'y'}_measured")
            if size is not None and coord is not None:
                assert abs(coord - centre) + size / 2 + self.clearance <= limit / 2


P = Parameters()


def box_at(w, d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z + h / 2) * bd.Box(w, d, h)


def rounded(w, d, h, x=0, y=0, z=0, radius=1):
    return bd.Pos(x, y, z) * bd.extrude(bd.RectangleRounded(w, d, radius), amount=h)


def cylinder(radius, height, x=0, y=0, z=0):
    return bd.Pos(x, y, z) * bd.Cylinder(
        radius, height, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )


def holder(p=P):
    p.validate()
    body = rounded(p.frame_width, p.frame_depth, p.base, radius=p.corner)
    body -= box_at(p.aperture_width, p.aperture_depth, p.base + 2, z=-1)
    body += rounded(
        p.saddle_length,
        p.saddle_width,
        p.base,
        x=p.saddle_start + p.saddle_length / 2,
        radius=p.corner,
    )

    # Chamfer exterior rims only. Inner bevel/post coplanar junctions can produce
    # T-junctions in tessellation; a square inner rim preserves a closed mesh.
    def exterior_horizontal(edge):
        b = edge.bounding_box()
        inside_aperture = (
            -p.aperture_width / 2 - 1e-6 <= b.min.X
            and p.aperture_width / 2 + 1e-6 >= b.max.X
            and -p.aperture_depth / 2 - 1e-6 <= b.min.Y
            and p.aperture_depth / 2 + 1e-6 >= b.max.Y
        )
        return b.size.Z < 1e-6 and not inside_aperture

    body = bd.chamfer([e for e in body.edges() if exterior_horizontal(e)], length=p.edge_break)
    for x, y in p.posts:
        body += cylinder(p.post_diameter / 2, p.board_back_z - p.base + 0.2, x, y, p.base - 0.2)
        body -= cylinder(
            p.pilot_diameter / 2, p.pilot_depth + 1, x, y, p.board_back_z - p.pilot_depth
        )
    for x in p.tie_stations:
        for y in (-p.tie_y, p.tie_y):
            body -= rounded(
                p.tie_slot_width,
                p.tie_slot_depth,
                p.base + 2,
                x,
                y,
                -1,
                radius=p.tie_slot_radius,
            )
    body.label = "DRAFT_SHT45_open_holder_measure_before_print"
    return body
