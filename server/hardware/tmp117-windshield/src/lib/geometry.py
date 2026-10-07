"""Millimetres; PCB centre XY datum; mounting pads start at Z=0.

Draft visualization values are deliberately NOT asserted board dimensions.
Measured values must replace None before the final print.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cadgen import build123d as bd


@dataclass(frozen=True)
class Parameters:
    board_length: float = 25.4  # vendor Eagle outline, verify received revision
    board_width: float = 17.78
    board_corner: float = 2.54
    clearance: float = 0.6
    rail: float = 7.2
    base: float = 2.0
    edge_break: float = 0.4
    corner: float = 1.0
    post_diameter: float = 5.6
    peg_diameter: float = 2.4
    peg_height: float = 1.6
    peg_clearance: float = 0.4  # diametral
    post_x: float = 5.0
    bridge_width: float = 17.6
    bridge_thickness: float = 2.0
    foam_width: float = 8.0
    foam_length: float = 8.0
    foam_thickness: float = 10.0  # existing E30 nominal stock; measure actual
    foam_compression: float = 0.03  # trial target; not a pressure specification
    cable_reach: float = 20.0  # design allowance beyond right PCB edge
    cable_tab_width: float = 12.0
    tie_slot_width: float = 3.2
    tie_slot_length: float = 2.4
    tie_slot_radius: float = 0.6
    tie_spacing: float = 6.0
    pcb_thickness_measured: float | None = None
    sensor_top_above_back_measured: float | None = None
    mated_plug_reach_measured: float | None = None
    cable_lowest_above_glass_measured: float | None = None
    cable_width_measured: float | None = None
    glass_layer_measured: float | None = None
    mounting_tape_measured: float | None = None
    ready_for_final_print: bool = False
    draft_sensor_top: float = 2.5
    draft_glass_layer: float = 0.1
    draft_mounting_tape: float = 0.15

    @property
    def rail_y(self):
        return self.board_width / 2 + self.clearance + self.rail / 2

    @property
    def left_x(self):
        return -self.board_length / 2 - self.clearance - self.rail / 2

    @property
    def right_x(self):
        return self.board_length / 2 + self.cable_reach

    @property
    def sensor_top(self):
        return (
            self.draft_sensor_top
            if self.sensor_top_above_back_measured is None
            else self.sensor_top_above_back_measured
        )

    @property
    def contact_layer(self):
        return (
            self.draft_glass_layer
            if self.glass_layer_measured is None
            else self.glass_layer_measured
        )

    @property
    def mount_tape(self):
        return (
            self.draft_mounting_tape
            if self.mounting_tape_measured is None
            else self.mounting_tape_measured
        )

    @property
    def stop_z(self):
        return (
            self.contact_layer
            + self.sensor_top
            + self.foam_thickness * (1 - self.foam_compression)
            - self.mount_tape
        )

    def validate(self):
        if self.ready_for_final_print:
            assert all(
                value is not None
                for name, value in vars(self).items()
                if name.endswith("_measured")
            ), "Measure every physical interface before final-print release"
        assert self.base >= 1.6 and self.rail >= self.post_diameter + 2 * self.edge_break
        assert 0 <= self.foam_compression <= 0.05
        assert self.stop_z > self.base
        assert self.peg_height < self.bridge_thickness
        assert self.post_diameter + 1e-9 >= self.peg_diameter + 2 * 1.6
        assert (
            self.bridge_width / 2 - self.post_x - (self.peg_diameter + self.peg_clearance) / 2
            >= 1.6
        )
        assert self.foam_width / 2 < self.post_x + self.post_diameter / 2
        if self.mated_plug_reach_measured is not None:
            assert (
                self.cable_reach - self.rail / 2 > self.mated_plug_reach_measured + self.clearance
            )
        if self.cable_lowest_above_glass_measured is not None:
            assert (
                self.cable_lowest_above_glass_measured
                >= self.mount_tape + self.base + self.clearance
            )
        if self.cable_width_measured is not None:
            assert (
                self.cable_width_measured + 2 * self.clearance
                < self.cable_tab_width - 2 * self.tie_slot_length - 2 * 1.6
            )


P = Parameters()


def layout():
    return json.loads(Path(__file__).with_name("board-layout.json").read_text())


def box_at(w, d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z + h / 2) * bd.Box(w, d, h)


def rounded(w, d, h, x=0, y=0, z=0, radius=1):
    return bd.Pos(x, y, z) * bd.extrude(bd.RectangleRounded(w, d, radius), amount=h)


def carrier(p=P):
    p.validate()
    length = p.right_x - p.left_x + p.rail
    centre = (p.right_x + p.left_x) / 2
    body = rounded(length, 2 * p.rail_y + p.rail, p.base, centre, radius=p.corner)
    body -= box_at(length - 2 * p.rail, 2 * p.rail_y - p.rail, p.base + 2, centre, z=-1)
    # Break all exposed base top/bottom rims at 45 degrees, including the aperture.
    horizontal = [e for e in body.edges() if e.bounding_box().size.Z < 1e-6]
    body = bd.chamfer(horizontal, length=p.edge_break)
    # Cable saddle lies outside the PCB; no PCB ledge and no sensor backing.
    tab_length = p.cable_reach / 2 + p.rail
    tab_x = p.right_x - tab_length / 2 + p.rail / 2
    tab = rounded(tab_length, p.cable_tab_width, p.base, tab_x, radius=p.corner)
    tab = bd.chamfer(
        [e for e in tab.edges() if e.bounding_box().size.Z < 1e-6], length=p.edge_break
    )
    body += tab
    for x in (tab_x - p.tie_spacing / 2, tab_x + p.tie_spacing / 2):
        for y in (
            -p.cable_tab_width / 2 + p.tie_slot_length / 2 + 1.6,
            p.cable_tab_width / 2 - p.tie_slot_length / 2 - 1.6,
        ):
            body -= rounded(
                p.tie_slot_width, p.tie_slot_length, p.base + 2, x, y, -1, radius=p.tie_slot_radius
            )
    for x in (-p.post_x, p.post_x):
        for y in (-p.rail_y, p.rail_y):
            body += bd.Pos(x, y, p.base) * bd.Cylinder(
                p.post_diameter / 2,
                p.stop_z - p.base,
                align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
            )
            body += bd.Pos(x, y, p.stop_z) * bd.Cylinder(
                p.peg_diameter / 2,
                p.peg_height,
                align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
            )
    body.label = "draft_open_back_carrier"
    return body


def bridge(p=P):
    p.validate()
    body = rounded(p.bridge_width, 2 * p.rail_y + p.rail, p.bridge_thickness, radius=p.corner)
    for x in (-p.post_x, p.post_x):
        for y in (-p.rail_y, p.rail_y):
            body -= bd.Pos(x, y, -1) * bd.Cylinder(
                (p.peg_diameter + p.peg_clearance) / 2,
                p.bridge_thickness + 2,
                align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
            )
    body.label = "draft_foam_bridge"
    return body
