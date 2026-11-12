"""Unit and mock-integration tests for the CADPilot Validation Rule Engine."""

import math
import os
import sys
import unittest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from mock.mock_bridge import MockBridgeHTTPHandler, SimulatedCADState
from validation import (
    ValidationEngine,
    compute_expected_volume_from_spec,
    extract_geometry_from_brep,
    run_benchmark_prompt,
    validate_bounding_box,
    validate_hole_count,
    validate_volume,
)
from benchmarks import load_prompt_by_id


class TestValidationRules(unittest.TestCase):
    """Independent tests for volume, hole count, bounding box, and error handling."""

    def test_01_volume_validation_exact_and_within_tolerance(self):
        res_exact = validate_volume(5523.55, 5523.55, tolerance_pct=2.0)
        self.assertTrue(res_exact.passed)
        self.assertEqual(res_exact.details["abs_diff_mm3"], 0.0)

        # Within 2% tolerance
        res_within = validate_volume(5510.0, 5523.55, tolerance_pct=2.0)
        self.assertTrue(res_within.passed)
        self.assertLess(res_within.details["rel_diff_pct"], 2.0)

    def test_02_volume_validation_floating_point_precision(self):
        # Tiny floating-point drift on very small volumes handled via abs_tolerance_mm3
        res = validate_volume(1.0 + 1e-6, 1.0, tolerance_pct=0.0, abs_tolerance_mm3=1e-3)
        self.assertTrue(res.passed)

    def test_03_volume_validation_exceeds_tolerance(self):
        res = validate_volume(4800.0, 5523.55, tolerance_pct=2.0)
        self.assertFalse(res.passed)
        self.assertIn("exceeds", res.message)
        self.assertGreater(res.details["rel_diff_pct"], 2.0)

    def test_04_volume_validation_invalid_inputs_graceful(self):
        for bad_actual in (None, -10.0, 0.0, float("nan"), float("inf"), "bad", True):
            res = validate_volume(bad_actual, 5000.0)
            self.assertFalse(res.passed, f"Expected failure for actual={bad_actual!r}")

        for bad_expected in (None, -5.0, float("nan"), "bad"):
            res = validate_volume(5000.0, bad_expected)
            self.assertFalse(res.passed, f"Expected failure for expected={bad_expected!r}")

    def test_05_hole_count_validation_pass_and_fail(self):
        res_ok = validate_hole_count(5, 5, cylindrical_features=[{"diameter_mm": 22.0}] + [{"diameter_mm": 3.0}] * 4)
        self.assertTrue(res_ok.passed)
        self.assertEqual(len(res_ok.details["detected_diameters_mm"]), 5)

        res_zero = validate_hole_count(0, 0)
        self.assertTrue(res_zero.passed)

        res_fail = validate_hole_count(3, 5)
        self.assertFalse(res_fail.passed)
        self.assertEqual(res_fail.details["delta"], -2)

        res_bad = validate_hole_count(None, 5)
        self.assertFalse(res_bad.passed)

    def test_06_bounding_box_validation(self):
        res_ok = validate_bounding_box([42.31, 42.29, 4.0], [42.3, 42.3, 4.0], tolerance_mm=0.1)
        self.assertTrue(res_ok.passed)

        res_fail = validate_bounding_box([50.0, 42.3, 4.0], [42.3, 42.3, 4.0], tolerance_mm=0.5)
        self.assertFalse(res_fail.passed)

    def test_07_extract_geometry_missing_and_invalid_brep(self):
        self.assertFalse(extract_geometry_from_brep(None).valid)
        self.assertFalse(extract_geometry_from_brep({}).valid)
        self.assertFalse(extract_geometry_from_brep({"bodies": []}).valid)
        self.assertFalse(
            extract_geometry_from_brep({"bodies": [{"body_id": "b1", "is_solid": False, "volume_mm3": 100.0}]}).valid
        )
        self.assertFalse(
            extract_geometry_from_brep({"bodies": [{"body_id": "b1", "is_solid": True, "volume_mm3": -5.0}]}).valid
        )

    def test_08_compute_expected_volume_from_spec(self):
        spec = {
            "shape_type": "rectangle",
            "dimensions": {"width": 42.3, "height": 42.3, "thickness": 4.0},
            "holes": [
                {"diameter": 22.0, "depth": "through"},
                {"diameter": 3.0, "depth": "through"},
                {"diameter": 3.0, "depth": "through"},
                {"diameter": 3.0, "depth": "through"},
                {"diameter": 3.0, "depth": "through"},
            ],
        }
        vol = compute_expected_volume_from_spec(spec)
        expected = 42.3 * 42.3 * 4.0 - math.pi * (11.0 ** 2) * 4.0 - 4 * math.pi * (1.5 ** 2) * 4.0
        self.assertAlmostEqual(vol, expected, places=2)

    def test_09_validation_engine_with_mock_bridge_brep(self):
        handler = MockBridgeHTTPHandler
        handler.cad_state = SimulatedCADState()
        handler.handle_rpc(handler, "reset_design", {}, 1)
        handler.handle_rpc(
            handler,
            "create_sketch",
            {"plane": "xy", "shapes": [{"type": "rectangle", "center": [0, 0], "width": 42.3, "height": 42.3}]},
            2,
        )
        handler.handle_rpc(handler, "extrude_profile", {"distance": 4.0}, 3)
        handler.handle_rpc(handler, "cut_hole", {"position": [0.0, 0.0], "diameter": 22.0}, 4)
        for idx, pos in enumerate([[-15.5, -15.5], [15.5, -15.5], [15.5, 15.5], [-15.5, 15.5]], 5):
            handler.handle_rpc(handler, "cut_hole", {"position": pos, "diameter": 3.0}, idx)
        handler.handle_rpc(handler, "apply_fillet", {"radius": 2.0}, 9)
        brep = handler.handle_rpc(handler, "inspect_brep", {}, 10)["result"]

        bm1 = load_prompt_by_id("BM-001")
        engine = ValidationEngine()
        report = engine.validate(brep, bm1["expected_outcome"])
        self.assertTrue(report.passed, f"Validation errors: {report.errors}")
        self.assertEqual(len(report.rules), 3)

    def test_10_benchmark_runner_ready_and_pending_prompts(self):
        bm1 = load_prompt_by_id("BM-001")
        res1 = run_benchmark_prompt(bm1, endpoint_url="mock")
        self.assertEqual(res1.execution_status, "completed")
        self.assertTrue(res1.validation_passed)

        bm11 = load_prompt_by_id("BM-011")
        res11 = run_benchmark_prompt(bm11, endpoint_url="mock")
        self.assertEqual(res11.execution_status, "pending_phase5")
        self.assertFalse(res11.validation_passed)


if __name__ == "__main__":
    unittest.main()
