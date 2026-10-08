"""Public release gate; all fixtures are synthetic, never received dimensions."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lib.geometry import Parameters


def synthetic_fit():
    return Parameters(
        board_length_measured=25.4,
        board_width_measured=17.78,
        hole_pitch_x_measured=20.32,
        hole_pitch_y_measured=12.7,
        hole_diameter_measured=2.5,
        pcb_thickness_measured=1.6,
        backside_protrusion_measured=0,
        component_top_above_back_measured=4.8,
        bottom_land_radius_measured=2.1,
        membrane_x_measured=0,
        membrane_y_measured=0,
        membrane_width_measured=4,
        membrane_depth_measured=4,
        membrane_top_above_back_measured=3,
        plug_reach_measured=6,
        plug_width_measured=6,
        plug_height_measured=4,
        plug_lowest_above_back_measured=0,
        cable_diameter_measured=3,
        bend_radius_measured=5,
        free_exit_measured=8,
        tape_thickness_measured=0.2,
        trim_width_measured=40,
        trim_length_measured=75,
        screw_length_measured=6,
        screw_head_radius_measured=3,
        tool_diameter_measured=5,
        printed_pilot_measured=2.55,
        received_revision_confirmed=True,
        bearing_lands_confirmed=True,
        trim_airflow_clearance_confirmed=True,
        retention_clamps_service_trial_passed=True,
        tape_attachment_removal_trial_passed=True,
        ready_for_final_print=True,
    )


class ReleaseGateTests(unittest.TestCase):
    def test_nominal_draft_cannot_be_released(self):
        Parameters().validate()
        with self.assertRaises(ValueError):
            Parameters(ready_for_final_print=True).validate()

    def test_every_missing_measurement_and_confirmation_blocks_release(self):
        p = synthetic_fit()
        p.validate()
        for name in vars(p):
            if name.endswith("_measured"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    replace(p, **{name: None}).validate()
            elif name.endswith("_confirmed") or name.endswith("_passed"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    replace(p, **{name: False}).validate()

    def test_reject_unsafe_received_interfaces(self):
        p = synthetic_fit()
        cases = {
            "board_length_measured": 26,
            "bottom_land_radius_measured": 1.5,
            "component_top_above_back_measured": 8,
            "backside_protrusion_measured": 8,
            "membrane_x_measured": 7,
            "membrane_depth_measured": 12,
            "membrane_top_above_back_measured": 6,
            "plug_reach_measured": 10,
            "plug_lowest_above_back_measured": -9,
            "plug_height_measured": 10,
            "cable_diameter_measured": 2,
            "free_exit_measured": 5,
            "trim_width_measured": 20,
            "screw_length_measured": 5,
            "screw_head_radius_measured": 4,
            "tool_diameter_measured": 8,
            "printed_pilot_measured": 2.8,
        }
        for name, value in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(p, **{name: value}).validate()

    def test_matching_but_inconsistent_layout_cannot_release(self):
        p = synthetic_fit()
        for changes in (
            {"hole_pitch_x": 0.1, "hole_pitch_x_measured": 0.1},
            {"hole_pitch_y": 16, "hole_pitch_y_measured": 16},
            {"hole_pitch_x": 18, "hole_pitch_x_measured": 18},
            {"clamp_gap": 1, "cable_diameter_measured": 1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(p, **changes).validate()

    def test_small_screw_head_cannot_release_joint(self):
        with self.assertRaises(ValueError):
            replace(synthetic_fit(), screw_head_radius_measured=1.5).validate()

    def test_invalid_measurements_rejected_even_in_draft(self):
        for value in (-1, 0, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Parameters(pcb_thickness_measured=value).validate()

    def test_thickness_updates_retainer_without_inventing_measurements(self):
        draft = Parameters()
        measured = replace(draft, pcb_thickness_measured=2.1)
        self.assertIsNone(draft.pcb_thickness_measured)
        self.assertAlmostEqual(measured.finger_depth, 1.8)
        self.assertEqual(measured.cover_stop, 18)


if __name__ == "__main__":
    unittest.main()
