"""CADPilot Validation Rule Engine Package.

Provides modular, independently testable geometric validation rules for
B-Rep inspection outputs (`inspect_brep`) and end-to-end benchmark execution.
"""

from .rules import (
    RuleResult,
    ValidationReport,
    ExtractedGeometry,
    extract_geometry_from_brep,
    compute_expected_volume_from_spec,
    validate_volume,
    validate_hole_count,
    validate_bounding_box,
    ValidationEngine,
)
from .benchmark_runner import (
    BenchmarkRunResult,
    check_bridge_health,
    run_benchmark_prompt,
    run_benchmark_suite,
)

__all__ = [
    "RuleResult",
    "ValidationReport",
    "ExtractedGeometry",
    "extract_geometry_from_brep",
    "compute_expected_volume_from_spec",
    "validate_volume",
    "validate_hole_count",
    "validate_bounding_box",
    "ValidationEngine",
    "BenchmarkRunResult",
    "check_bridge_health",
    "run_benchmark_prompt",
    "run_benchmark_suite",
]
