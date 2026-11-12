"""Unit tests for CADPilot Streamlit UI wireframe helpers and module validity."""

import ast
import os
import sys
import unittest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from benchmarks import load_prompt_by_id
from ui.wireframe import (
    build_benchmark_history_rows,
    build_pipeline_stages,
    build_step_progress_rows,
    build_svg_wireframe_preview,
    format_prompt_label,
)
from validation import run_benchmark_prompt


class TestUIWireframeHelpers(unittest.TestCase):
    """Verify UI formatting, SVG wireframe generation, and Streamlit script syntax."""

    def test_01_format_prompt_label(self):
        bm1 = load_prompt_by_id("BM-001")
        label = format_prompt_label(bm1)
        self.assertIn("BM-001", label)
        self.assertIn("mounting_plate", label)
        self.assertIn("Ready", label)

    def test_02_pipeline_stages_and_step_rows(self):
        bm1 = load_prompt_by_id("BM-001")
        run_res = run_benchmark_prompt(bm1, endpoint_url="mock").to_dict()

        stages = build_pipeline_stages(run_res["agent_state"], run_res["execution_status"])
        self.assertEqual(len(stages), 5)
        self.assertEqual(stages[0]["state"], "done")
        self.assertEqual(stages[3]["state"], "done")

        rows = build_step_progress_rows(run_res["agent_state"])
        self.assertGreaterEqual(len(rows), 7)
        self.assertEqual(rows[0]["Tool"], "reset_design")
        self.assertEqual(rows[-1]["Tool"], "export_cad")

    def test_03_benchmark_history_rows(self):
        bm1 = load_prompt_by_id("BM-001")
        run_res = run_benchmark_prompt(bm1, endpoint_url="mock").to_dict()
        hist_rows = build_benchmark_history_rows([run_res])
        self.assertEqual(len(hist_rows), 1)
        self.assertEqual(hist_rows[0]["ID"], "BM-001")
        self.assertEqual(hist_rows[0]["Validation"], "PASS")
        self.assertEqual(hist_rows[0]["Holes"], 5)

    def test_04_svg_wireframe_preview(self):
        bm1 = load_prompt_by_id("BM-001")
        svg_rect = build_svg_wireframe_preview(expected_outcome=bm1["expected_outcome"])
        self.assertIn("<svg", svg_rect)
        self.assertIn("<rect", svg_rect)
        self.assertIn("Holes: 5", svg_rect)

        bm3 = load_prompt_by_id("BM-003")
        svg_circ = build_svg_wireframe_preview(expected_outcome=bm3["expected_outcome"])
        self.assertIn("<circle", svg_circ)
        self.assertIn("Dia 50", svg_circ)

    def test_05_streamlit_app_valid_python_syntax(self):
        app_path = os.path.join(_repo_root, "ui", "app.py")
        with open(app_path, "r", encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=app_path)


if __name__ == "__main__":
    unittest.main()
