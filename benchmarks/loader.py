"""Loader and query utilities for CADPilot benchmark prompts."""

import json
import os
from typing import Any, Dict, List, Optional

BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))

PROMPT_BATCH_FILES = {
    "1-15": os.path.join(BENCHMARK_DIR, "prompts_1_15.json"),
    "16-30": os.path.join(BENCHMARK_DIR, "prompts_16_30.json"),
}

REQUIRED_PROMPT_KEYS = {
    "id",
    "number",
    "title",
    "part_family",
    "complexity",
    "execution_readiness",
    "operations",
    "prompt",
    "expected_outcome",
}

REQUIRED_EXPECTED_KEYS = {
    "part_type",
    "dimensions_mm",
    "bounding_box_mm",
    "hole_count",
    "expected_volume_mm3",
    "volume_tolerance_pct",
    "should_succeed",
}


def validate_prompt_schema(entry: Dict[str, Any]) -> List[str]:
    """Validate a single benchmark prompt dictionary and return any schema errors."""
    errors: List[str] = []
    missing = REQUIRED_PROMPT_KEYS - set(entry.keys())
    if missing:
        errors.append(f"Missing required keys: {sorted(missing)}")

    expected = entry.get("expected_outcome", {})
    if not isinstance(expected, dict):
        errors.append("'expected_outcome' must be a dictionary.")
    else:
        missing_exp = REQUIRED_EXPECTED_KEYS - set(expected.keys())
        if missing_exp:
            errors.append(f"Missing expected_outcome keys: {sorted(missing_exp)}")

    if not isinstance(entry.get("operations"), list) or not entry.get("operations"):
        errors.append("'operations' must be a non-empty list.")

    if not isinstance(entry.get("prompt"), str) or not entry["prompt"].strip():
        errors.append("'prompt' must be a non-empty string.")

    return errors


def _read_batch_file(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "prompts" in data:
        return list(data["prompts"])
    if isinstance(data, list):
        return list(data)
    raise ValueError(f"Unsupported benchmark JSON structure in {path}")


def load_prompts_batch(batch: str = "all") -> List[Dict[str, Any]]:
    """Load prompts by batch identifier: '1-15', '16-30', or 'all'."""
    if batch == "all":
        prompts: List[Dict[str, Any]] = []
        for key in ("1-15", "16-30"):
            prompts.extend(_read_batch_file(PROMPT_BATCH_FILES[key]))
        return sorted(prompts, key=lambda p: p.get("number", 0))

    if batch not in PROMPT_BATCH_FILES:
        raise ValueError(f"Unknown batch '{batch}'. Expected '1-15', '16-30', or 'all'.")

    return sorted(_read_batch_file(PROMPT_BATCH_FILES[batch]), key=lambda p: p.get("number", 0))


def load_prompts(
    part_family: Optional[str] = None,
    complexity: Optional[str] = None,
    execution_readiness: Optional[str] = None,
    batch: str = "all",
) -> List[Dict[str, Any]]:
    """Load and optionally filter CADPilot benchmark prompts."""
    prompts = load_prompts_batch(batch=batch)

    if part_family:
        prompts = [p for p in prompts if p.get("part_family") == part_family]
    if complexity:
        prompts = [p for p in prompts if p.get("complexity") == complexity]
    if execution_readiness:
        prompts = [p for p in prompts if p.get("execution_readiness") == execution_readiness]

    return prompts


def load_prompt_by_id(prompt_id: str) -> Optional[Dict[str, Any]]:
    """Return a single benchmark prompt matching ID (e.g. 'BM-001') or None."""
    for p in load_prompts_batch("all"):
        if p.get("id") == prompt_id:
            return p
    return None


def get_benchmark_summary() -> Dict[str, Any]:
    """Return aggregate counts grouped by part family, complexity, and readiness."""
    prompts = load_prompts_batch("all")
    by_family: Dict[str, int] = {}
    by_complexity: Dict[str, int] = {}
    by_readiness: Dict[str, int] = {}

    for p in prompts:
        fam = p.get("part_family", "unknown")
        comp = p.get("complexity", "unknown")
        rdy = p.get("execution_readiness", "unknown")
        by_family[fam] = by_family.get(fam, 0) + 1
        by_complexity[comp] = by_complexity.get(comp, 0) + 1
        by_readiness[rdy] = by_readiness.get(rdy, 0) + 1

    return {
        "total_prompts": len(prompts),
        "by_part_family": by_family,
        "by_complexity": by_complexity,
        "by_readiness": by_readiness,
    }
