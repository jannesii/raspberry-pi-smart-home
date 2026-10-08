"""Draft millimetre geometry; glass Z=-installed tape, base underside Z=0."""

from __future__ import annotations

import math
from dataclasses import dataclass

from cadgen import build123d as bd


@dataclass(frozen=True)
class Parameters:
    # Manufacturer nominal outline. Every other default is a design allowance.
    board_length: float = 25.4
    board_width: float = 17.78
    pilot: float = 2.55
    clearance_hole: float = 3.4
    base_thickness: float = 2.0
    cover_stop: float = 15.0
    retainer_stop: float = 6.0
    roof: float = 2.0
    board_edge_clearance: float = 0.3
    draft_pcb_thickness: float = 1.6
    draft_tape: float = 0.15
    draft_paste: float = 0.1
    pcb_thickness_measured: float | None = None
    tape_thickness_measured: float | None = None
    paste_thickness_measured: float | None = None
    sensor_top_measured: float | None = None
    underside_protrusion_measured: float | None = None
    plug_reach_measured: float | None = None
    plug_width_measured: float | None = None
    cable_diameter_measured: float | None = None
    bend_radius_measured: float | None = None
    foam_thickness_measured: float | None = None
    bearing_lands_confirmed: bool = False
    layout_confirmed: bool = False
    physical_trial_passed: bool = False
    ready_for_final_print: bool = False

    @property
    def pcb_back(self):
        tape = (
            self.draft_tape
            if self.tape_thickness_measured is None
            else self.tape_thickness_measured
        )
        paste = (
            self.draft_paste
            if self.paste_thickness_measured is None
            else self.paste_thickness_measured
        )
        return paste - tape

    @property
    def pcb_top(self):
        thickness = (
            self.draft_pcb_thickness
            if self.pcb_thickness_measured is None
            else self.pcb_thickness_measured
        )
        return self.pcb_back + thickness

    @property
    def finger_depth(self):
        return self.retainer_stop - self.pcb_top - 0.1

    def validate(self):
        measured: dict[str, float] = {}
        for name, value in vars(self).items():
            if not name.endswith("_measured"):
                continue
            if value is None:
                if self.ready_for_final_print:
                    raise ValueError("Final fit requires every physical measurement")
                continue
            if not math.isfinite(value) or value < 0:
                raise ValueError("Measurements must be finite nonnegative millimetres")
            measured[name] = value
        if self.pcb_thickness_measured is not None and self.pcb_thickness_measured <= 0:
            raise ValueError("PCB thickness must be positive")
        if not 0 < self.finger_depth < self.retainer_stop:
            raise ValueError("Contact stack outside retainer adjustment range")
        if self.ready_for_final_print:
            if not all(
                (self.layout_confirmed, self.bearing_lands_confirmed, self.physical_trial_passed)
            ):
                raise ValueError(
                    "Final fit requires measurements, confirmed lands/layout and physical trial"
                )
            if (
                any(
                    value <= 0
                    for value in (
                        measured["plug_reach_measured"],
                        measured["plug_width_measured"],
                        measured["cable_diameter_measured"],
                        measured["bend_radius_measured"],
                        measured["foam_thickness_measured"],
                    )
                )
                or measured["sensor_top_measured"] < measured["pcb_thickness_measured"]
            ):
                raise ValueError(
                    "Received component dimensions must be physically consistent and positive"
                )
            if measured["underside_protrusion_measured"] > measured["paste_thickness_measured"]:
                raise ValueError("Backside protrusions prevent nominal glass contact")
            if measured["plug_reach_measured"] > 8 or measured["plug_width_measured"] > 8:
                raise ValueError("Plug exceeds reserved envelope; redesign clearance")
            if measured["cable_diameter_measured"] > 3 or measured["bend_radius_measured"] > 10:
                raise ValueError("Cable exceeds draft clamp/straight exit allowance")
            if (
                self.pcb_back
                + measured["sensor_top_measured"]
                + measured["foam_thickness_measured"]
                > self.cover_stop - 0.5
            ):
                raise ValueError("Foam stack reaches cover; trim pad independently of screws")


P = Parameters()
RETAINERS = [(x, y) for x in (-10.16, 10.16) for y in (-13.0, 13.0)]
STOPS = [(x, y) for x in (-19.0, 19.0) for y in (-13.0, 13.0)]
CLAMPS = [(-27.0, 0.0), (27.0, 0.0)]


def box_at(w, d, h, x=0, y=0, z=0):
    return bd.Pos(x, y, z + h / 2) * bd.Box(w, d, h)


def bore(diameter, depth, x=0, y=0, z=0):
    return bd.Pos(x, y, z) * bd.Cylinder(
        diameter / 2, depth, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )


def base(p=P):
    p.validate()
    # Window walls locate the PCB perimeter; no shelves beneath board/tongue.
    body = box_at(64, 36, p.base_thickness)
    body -= box_at(
        p.board_length + 2 * p.board_edge_clearance,
        p.board_width + 2 * p.board_edge_clearance,
        p.base_thickness + 2,
        z=-1,
    )
    # Blind pilots leave at least 1 mm of printed floor above mounting tape.
    for x, y in STOPS:
        body += bore(7, p.cover_stop - p.base_thickness, x, y, p.base_thickness)
        body -= bore(p.pilot, 5, x, y, p.cover_stop - 4)
    for x, y in RETAINERS:
        body += bd.Pos(x, y, p.base_thickness) * bd.extrude(
            bd.RectangleRounded(7, 7, 0.5), amount=p.retainer_stop - p.base_thickness
        )
        body -= bore(p.pilot, 5, x, y, p.retainer_stop - 4)
    for x, _ in CLAMPS:
        for y in (-5, 5):
            body += box_at(8, 6, 2, x, y, 2)
            body -= bore(p.pilot, 4, x, y, 1)
    body.label = "DRAFT_taped_base"
    return body


def cover(p=P):
    # Print roof down: walls rise, so no bridge/support beneath installed roof.
    body = box_at(44, 32, p.roof)
    for y in (-10.7, 10.7):
        body += box_at(12, 1.4, p.cover_stop - p.base_thickness, y=y, z=p.roof)
    # Open ends admit plugs; small centre shield covers foam without sealing.
    for x in (-15.3, 15.3):
        body += bd.Pos(x, 0, p.roof) * bd.extrude(bd.RectangleRounded(1.4, 9, 0.3), amount=6)
    for x, y in STOPS:
        body -= bore(p.clearance_hole, p.roof + 2, x, y, -1)
    body.label = "DRAFT_airflow_cover_roof_down"
    return body


def retainer(p=P):
    p.validate()
    body = box_at(7, 9, p.roof, y=-2)
    body += box_at(5, 1.2, p.finger_depth, y=-5.9, z=p.roof)
    body -= bore(p.clearance_hole, p.roof + 2, z=-1)
    body.label = "DRAFT_land_retainer_top_down"
    return body


def cable_clamp(p=P):
    body = box_at(8, 16, 3)
    # Feet close on bosses. 4 x 3 mm opening grips only after measured sizing.
    for y in (-5, 5):
        body += box_at(8, 6, 1, y=y, z=3)
        body -= bore(p.clearance_hole, 6, y=y, z=-1)
    body.label = "DRAFT_cable_clamp_top_down"
    return body


def pilot_coupon(p=P):
    body = box_at(36, 12, 2)
    for x, d in ((-12, 2.45), (0, 2.55), (12, 2.65)):
        body += bore(7, 6, x=x, z=2)
        body -= bore(d, 7, x=x, z=2)
    body.label = "DRAFT_pilot_ladder_left_2p45_middle_2p55_right_2p65"
    return body


def fit_coupon(p=P):
    # Reduced-height full open board/plug window; reuse retention and clamp datums.
    body = base(p)
    body -= box_at(70, 40, 20, z=p.retainer_stop)
    body.label = "DRAFT_board_plug_contact_stack_coupon"
    return body


def installed_cover(shape, p=P):
    return shape.moved(bd.Location((0, 0, p.cover_stop + p.roof), (180, 0, 0)))


def installed_retainer(shape, x, y, p=P):
    # Local inward end is -Y; X rotation flips it. North needs a Z half-turn.
    rotation = (180, 0, 180 if y > 0 else 0)
    return shape.moved(bd.Location((x, y, p.retainer_stop + p.roof), rotation))


def installed_clamp(shape, x):
    return shape.moved(bd.Location((x, 0, 8), (180, 0, 0)))
