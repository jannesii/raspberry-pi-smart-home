"""Millimetre draft; adapter underside Z=0, housing local underside Z=0.

Candidate 5665 layout is NOT confirmed for ordered PTFE 6174. Printed contacts,
plug volumes and PCB thickness below are allowances, never received facts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cadgen import build123d as bd


@dataclass(frozen=True)
class Parameters:
    board_length: float = 25.4
    board_width: float = 17.78
    hole_pitch_x: float = 20.32
    hole_pitch_y: float = 12.7
    hole_diameter: float = 2.5
    pilot: float = 2.55
    clearance_hole: float = 3.4
    adapter_height: float = 8.0
    board_back: float = 10.0
    cover_stop: float = 18.0
    retainer_stop: float = 14.0
    plate: float = 2.0
    draft_pcb_thickness: float = 1.6
    clamp_gap: float = 3.0
    board_length_measured: float | None = None
    board_width_measured: float | None = None
    hole_pitch_x_measured: float | None = None
    hole_pitch_y_measured: float | None = None
    hole_diameter_measured: float | None = None
    pcb_thickness_measured: float | None = None
    backside_protrusion_measured: float | None = None
    component_top_above_back_measured: float | None = None
    bottom_land_radius_measured: float | None = None
    membrane_x_measured: float | None = None
    membrane_y_measured: float | None = None
    membrane_width_measured: float | None = None
    membrane_depth_measured: float | None = None
    membrane_top_above_back_measured: float | None = None
    plug_reach_measured: float | None = None
    plug_width_measured: float | None = None
    plug_height_measured: float | None = None
    plug_lowest_above_back_measured: float | None = None
    cable_diameter_measured: float | None = None
    bend_radius_measured: float | None = None
    free_exit_measured: float | None = None
    tape_thickness_measured: float | None = None
    trim_width_measured: float | None = None
    trim_length_measured: float | None = None
    screw_length_measured: float | None = None
    screw_head_radius_measured: float | None = None
    tool_diameter_measured: float | None = None
    printed_pilot_measured: float | None = None
    received_revision_confirmed: bool = False
    bearing_lands_confirmed: bool = False
    trim_airflow_clearance_confirmed: bool = False
    retention_clamps_service_trial_passed: bool = False
    tape_attachment_removal_trial_passed: bool = False
    ready_for_final_print: bool = False

    @property
    def pcb_thickness(self):
        return (
            self.draft_pcb_thickness
            if self.pcb_thickness_measured is None
            else self.pcb_thickness_measured
        )

    @property
    def finger_depth(self):
        return self.retainer_stop - self.board_back - self.pcb_thickness - 0.1

    @property
    def board_posts(self):
        return [
            (x, y)
            for x in (-self.hole_pitch_x / 2, self.hole_pitch_x / 2)
            for y in (-self.hole_pitch_y / 2, self.hole_pitch_y / 2)
        ]

    @property
    def retainers(self):
        return [
            (x, y) for x in (-self.hole_pitch_x / 2, self.hole_pitch_x / 2) for y in (-13.0, 13.0)
        ]

    def validate(self):
        for name in (
            "board_length",
            "board_width",
            "hole_pitch_x",
            "hole_pitch_y",
            "hole_diameter",
            "clamp_gap",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid geometry allowance: {name}")
        if self.clamp_gap <= 2:
            raise ValueError("Clamp feet require gap greater than 2 mm")
        if min(self.hole_pitch_x, self.hole_pitch_y) <= self.hole_diameter + 0.6:
            raise ValueError("PCB locating holes overlap or lack separating material")
        if self.hole_pitch_x + 4 > self.board_length or self.hole_pitch_y + 4 > self.board_width:
            raise ValueError("Support lands extend outside PCB envelope")
        distance_to_air_window = math.hypot(
            max(self.hole_pitch_x / 2 - 8, 0), max(self.hole_pitch_y / 2 - 6, 0)
        )
        if distance_to_air_window < 2.01:
            raise ValueError("PCB support enters protected through-air column")
        if self.hole_pitch_y / 2 + self.hole_diameter / 2 > 7.65:
            raise ValueError("Drill hole enters retainer contact pad; redesign lands")
        signed = {"membrane_x_measured", "membrane_y_measured", "plug_lowest_above_back_measured"}
        zero_allowed = signed | {"backside_protrusion_measured"}
        for name, value in vars(self).items():
            if name.endswith("_measured"):
                if value is None:
                    if self.ready_for_final_print:
                        raise ValueError(f"Final fit requires {name}")
                elif (
                    not math.isfinite(value)
                    or (name not in signed and value < 0)
                    or (name not in zero_allowed and value == 0)
                ):
                    raise ValueError(f"Invalid physical measurement: {name}")
        if not 0 < self.finger_depth < self.retainer_stop - self.plate:
            raise ValueError("PCB stack outside retainer adjustment range")
        if self.pilot != 2.55 or self.clearance_hole < 3.4:
            raise ValueError("Use BT3 2.55 mm source pilots and separate through-clearance")
        if not self.ready_for_final_print:
            return
        if not all(
            (
                self.received_revision_confirmed,
                self.bearing_lands_confirmed,
                self.trim_airflow_clearance_confirmed,
                self.retention_clamps_service_trial_passed,
                self.tape_attachment_removal_trial_passed,
            )
        ):
            raise ValueError("Final fit requires received-layout, lands and physical trials")
        for name in (
            "board_length",
            "board_width",
            "hole_pitch_x",
            "hole_pitch_y",
            "hole_diameter",
        ):
            if abs(getattr(self, name) - getattr(self, f"{name}_measured")) > 0.05:
                raise ValueError(f"Update nominal layout to received {name}")
        if self.board_length > 27.0 or self.board_width > 19.0 or self.hole_diameter < 2.4:
            raise ValueError("Received PCB exceeds draft locating window/pins; redesign")
        if self.bottom_land_radius_measured < 2.0:
            raise ValueError("Support rings need confirmed 2 mm backside lands")
        if self.backside_protrusion_measured > self.board_back - self.plate - 0.6:
            raise ValueError("Backside reaches housing floor")
        if (
            not self.pcb_thickness
            <= self.component_top_above_back_measured
            <= self.cover_stop - self.board_back - 0.6
        ):
            raise ValueError("Component stack reaches cover")
        if (
            not self.pcb_thickness
            <= self.membrane_top_above_back_measured
            <= self.component_top_above_back_measured
        ):
            raise ValueError("Membrane and component heights inconsistent")
        if (
            abs(self.membrane_x_measured) + self.membrane_width_measured / 2 + 0.6 > 8
            or abs(self.membrane_y_measured) + self.membrane_depth_measured / 2 + 0.6 > 6
        ):
            raise ValueError("Membrane exceeds through-air window")
        if (
            self.plug_reach_measured > 8
            or self.plug_width_measured > 8
            or self.plug_height_measured > 6
        ):
            raise ValueError("Plug exceeds reserved envelope")
        if (
            self.board_back + self.plug_lowest_above_back_measured < self.plate + 0.6
            or self.board_back + self.plug_lowest_above_back_measured + self.plug_height_measured
            > self.cover_stop - 0.6
        ):
            raise ValueError("Plug reaches floor or cover")
        if (
            abs(self.cable_diameter_measured - self.clamp_gap) > 0.1
            or self.free_exit_measured < self.bend_radius_measured + 2
        ):
            raise ValueError("Resize clamp or increase external bend clearance")
        if self.trim_length_measured < 72 or self.trim_width_measured < 38:
            raise ValueError("Measured trim interface too small for adapter")
        if (
            self.screw_length_measured != 6
            or not 1.9 <= self.screw_head_radius_measured <= 3.5
            or self.tool_diameter_measured > 6
        ):
            raise ValueError(
                "Draft joints require measured BT3x6/head/tool envelope; redesign for other stock"
            )
        if not 2.45 <= self.printed_pilot_measured <= 2.65:
            raise ValueError("Calibrate printed pilot on coupon")


P = Parameters()
MOUNTS = [(x, y) for x in (-30.0, 30.0) for y in (-15.0, 15.0)]
STOPS = [(x, y) for x in (-19.0, 19.0) for y in (-16.0, 16.0)]
CLAMPS = (-33.0, 33.0)


def box_at(w, d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z + h / 2) * bd.Box(w, d, h)


def bore(d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z) * bd.Cylinder(
        d / 2, h, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )


def rounded_plate(w, d, h):
    return bd.extrude(bd.RectangleRounded(w, d, 1), amount=h)


def pilot_boss(x, y, top, p=P, diameter=7):
    return bore(diameter, top - p.plate, x, y, p.plate) - bore(p.pilot, 5.1, x, y, top - 5)


def adapter_spigot(x=0, y=0, p=P):
    """Common lift-off locating interface for adapter and measurement coupon."""
    return bore(4, 0.8, x, y, p.adapter_height) - bore(p.clearance_hole, 1, x, y, p.adapter_height)


def socket_tools(x=0, y=0, p=P):
    """Common through-clearance and underside recess for housing and coupon."""
    return bore(p.clearance_hole, p.plate + 2, x, y, -1) + bore(4.4, 1, x, y)


def adapter(p=P):
    p.validate()
    body = rounded_plate(72, 38, p.plate) - box_at(28, 20, p.plate + 2, z=-1)
    for x, y in MOUNTS:
        body += pilot_boss(x, y, p.adapter_height, p, 8)
        # 4 mm locating spigot fits 4.4 mm recess beneath housing, not PCB.
        body += adapter_spigot(x, y, p)
    body.label = "DRAFT_taped_console_adapter"
    return body


def housing(p=P):
    p.validate()
    body = rounded_plate(80, 40, p.plate) - box_at(28, 20, p.plate + 2, z=-1)
    for x, y in MOUNTS:
        body -= socket_tools(x, y, p)
    for x, y in p.board_posts:
        # Rings + short locating pins: no screw passes through the PCB.
        body += bore(4, p.board_back, x, y)
        body += bore(1.8, 0.8, x, y, p.board_back)
        # Join each post to window rail; no shelf under membrane or tongue.
        body += box_at(4, 4, p.plate + 2, x, y + (2 if y > 0 else -2))
    for x, y in p.retainers:
        body += pilot_boss(x, y, p.retainer_stop, p)
    for x, y in STOPS:
        body += pilot_boss(x, y, p.cover_stop, p)
    for x in CLAMPS:
        for y in (-6, 6):
            body += box_at(8, 6, 2, x, y, p.plate)
            body -= bore(p.pilot, 3.1, x, y, 1)
    # Perimeter fences, outside the board and away from plug centre lines.
    for x in (-14.0, 14.0):
        for y in (-7, 7):
            body += box_at(1, 3.6, p.board_back + 0.8, x, y)
    body.label = "DRAFT_removable_ventilated_housing"
    return body


def cover(p=P):
    p.validate()
    body = rounded_plate(44, 40, p.plate)
    for x, y, w, d in (
        (0, 0, 16, 12),
        (-14, 0, 8, 19.6),
        (14, 0, 8, 20),
        (0, -13, 15.6, 5.6),
        (0, 13, 15.6, 5.6),
    ):
        body -= box_at(w, d, p.plate + 2, x, y, -1)
    for x, y in STOPS:
        body -= bore(p.clearance_hole, p.plate + 2, x, y, -1)
    for y in (-19, 19):
        body += box_at(24, 2, 8, y=y, z=p.plate)
    body.label = "DRAFT_ventilated_cover_top_down"
    return body


def retainer(p=P):
    p.validate()
    body = box_at(7, 9, p.plate, y=-1)
    # After inversion this pad sits at |Y|=8, outside candidate 2.5 mm hole.
    body += box_at(3, 0.7, p.finger_depth, y=-5, z=p.plate)
    body -= bore(p.clearance_hole, p.plate + 2, z=-1)
    body.label = "DRAFT_land_retainer_top_down"
    return body


def cable_clamp(p=P):
    p.validate()
    body = box_at(8, 18, 3)
    for y in (-6, 6):
        body += box_at(8, 6, p.clamp_gap - 2, y=y, z=3)
        body -= bore(p.clearance_hole, 6, y=y, z=-1)
    body.label = "DRAFT_insulated_cable_clamp_top_down"
    return body


def pilot_coupon(p=P):
    body = rounded_plate(36, 14, p.plate)
    for x, diameter in ((-12, 2.45), (0, 2.55), (12, 2.65)):
        body += bore(8, 6, x=x, z=p.plate)
        body -= bore(diameter, 5.1, x=x, z=3)
    body.label = "DRAFT_pilot_ladder_left_2p45_centre_2p55_right_2p65"
    return body


def fit_coupon(p=P):
    # Remove only cover towers: preserve actual retainer/clamp stops and pilots.
    body = housing(p)
    for x, y in STOPS:
        body -= bore(7.2, p.cover_stop, x, y, p.plate)
    body.label = "DRAFT_board_two_plug_coupon"
    return body


def adapter_coupon(p=P):
    body = rounded_plate(20, 14, p.plate) + pilot_boss(0, 0, p.adapter_height, p, 8)
    body += adapter_spigot(p=p)
    body.label = "DRAFT_adapter_boss_coupon"
    return body


def adapter_socket_coupon(p=P):
    body = rounded_plate(20, 14, p.plate) - socket_tools(p=p)
    body.label = "DRAFT_adapter_socket_coupon_recess_on_bed"
    return body


def installed_housing(shape, p=P):
    return shape.moved(bd.Location((0, 0, p.adapter_height)))


def installed_cover(shape, p=P):
    return shape.moved(bd.Location((0, 0, p.adapter_height + p.cover_stop + p.plate), (180, 0, 0)))


def installed_retainer(shape, x, y, p=P):
    return shape.moved(
        bd.Location(
            (x, y, p.adapter_height + p.retainer_stop + p.plate), (180, 0, 180 if y > 0 else 0)
        )
    )


def installed_clamp(shape, x, p=P):
    return shape.moved(bd.Location((x, 0, p.adapter_height + 5 + p.clamp_gap), (180, 0, 0)))
