"""Release API tests; synthetic measurements never describe received hardware."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lib.geometry import Parameters


class ReleaseGateTests(unittest.TestCase):
    def test_draft_is_allowed_but_cannot_be_released(self):
        Parameters().validate()
        with self.assertRaises(ValueError):
            Parameters(ready_for_final_print=True).validate()

    def test_invalid_measurements_are_rejected(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Parameters(tape_thickness_measured=value).validate()

    def test_tape_changes_retention_height_without_changing_cover_stop(self):
        first = Parameters(tape_thickness_measured=0.15)
        second = replace(first, tape_thickness_measured=0.65)
        self.assertAlmostEqual(second.pcb_back, -0.55)
        self.assertAlmostEqual(second.finger_depth - first.finger_depth, 0.5)
        self.assertEqual(second.cover_stop, first.cover_stop)

    def test_zero_sized_received_cable_does_not_unlock_release(self):
        p = Parameters(
            pcb_thickness_measured=1.6,
            tape_thickness_measured=0.15,
            paste_thickness_measured=0.1,
            sensor_top_measured=2.5,
            underside_protrusion_measured=0,
            plug_reach_measured=6,
            plug_width_measured=6,
            cable_diameter_measured=0,
            bend_radius_measured=5,
            foam_thickness_measured=8,
            bearing_lands_confirmed=True,
            layout_confirmed=True,
            physical_trial_passed=True,
            ready_for_final_print=True,
        )
        with self.assertRaises(ValueError):
            p.validate()


if __name__ == "__main__":
    unittest.main()
