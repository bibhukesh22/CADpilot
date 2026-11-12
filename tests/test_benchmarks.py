"""Unit tests for the CADPilot 30-prompt benchmark dataset and loader."""

import os
import sys
import unittest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from benchmarks import (
    load_prompts,
    load_prompt_by_id,
    load_prompts_batch,
    get_benchmark_summary,
    validate_prompt_schema,
)
from agent.nodes.spec_extractor import extract_spec_from_text


class TestBenchmarkDataset(unittest.TestCase):
    """Verify structure, completeness, uniqueness, and spec alignment for Prompts 1-30."""

    def test_01_batch_1_to_15_loaded(self):
        prompts = load_prompts_batch("1-15")
        self.assertEqual(len(prompts), 15)
        self.assertEqual([p["number"] for p in prompts], list(range(1, 16)))

    def test_02_batch_16_to_30_loaded(self):
        prompts = load_prompts_batch("16-30")
        self.assertEqual(len(prompts), 15)
        self.assertEqual([p["number"] for p in prompts], list(range(16, 31)))

    def test_03_all_30_prompts_unique_and_valid_schema(self):
        prompts = load_prompts_batch("all")
        self.assertEqual(len(prompts), 30)

        ids = [p["id"] for p in prompts]
        texts = [p["prompt"].strip().lower() for p in prompts]
        self.assertEqual(len(set(ids)), 30, "Prompt IDs must be unique")
        self.assertEqual(len(set(texts)), 30, "Prompt texts must not be duplicates")

        for entry in prompts:
            errors = validate_prompt_schema(entry)
            self.assertEqual(errors, [], f"Schema errors in {entry.get('id')}: {errors}")

    def test_04_part_family_and_complexity_coverage(self):
        summary = get_benchmark_summary()
        self.assertEqual(summary["total_prompts"], 30)
        self.assertIn("mounting_plate", summary["by_part_family"])
        self.assertIn("l_bracket", summary["by_part_family"])
        self.assertIn("standoff", summary["by_part_family"])
        self.assertEqual(set(summary["by_complexity"].keys()), {"simple", "medium", "complex"})

    def test_05_filter_and_lookup_helpers(self):
        bm1 = load_prompt_by_id("BM-001")
        self.assertIsNotNone(bm1)
        self.assertEqual(bm1["number"], 1)

        lbrackets = load_prompts(part_family="l_bracket")
        self.assertEqual(len(lbrackets), 10)

        standoffs = load_prompts(part_family="standoff")
        self.assertEqual(len(standoffs), 5)

        ready_prompts = load_prompts(execution_readiness="ready")
        self.assertEqual(len(ready_prompts), 15)

        self.assertIsNone(load_prompt_by_id("BM-999"))

    def test_06_ready_prompts_parse_with_spec_extractor(self):
        ready_prompts = load_prompts(execution_readiness="ready")
        for entry in ready_prompts:
            exp = entry["expected_outcome"]
            if not exp.get("should_succeed", True):
                continue
            spec = extract_spec_from_text(entry["prompt"])
            self.assertEqual(
                spec.dimensions,
                exp["dimensions_mm"],
                f"Dimension mismatch for {entry['id']}: {spec.dimensions} vs {exp['dimensions_mm']}",
            )
            self.assertEqual(
                len(spec.holes),
                exp["hole_count"],
                f"Hole count mismatch for {entry['id']}: {len(spec.holes)} vs {exp['hole_count']}",
            )


if __name__ == "__main__":
    unittest.main()
