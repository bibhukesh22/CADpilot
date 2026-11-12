"""Modular Validation Rule Engine for CADPilot B-Rep outputs.

Validates CAD generation results extracted from `inspect_brep` (Fusion 360 or
Mock Bridge) against expected mechanical specifications:
1. Volume validation (with configurable percentage & floating-point tolerances)
2. Hole count validation (cylindrical feature count vs expected hole count)
3. Bounding box validation (outer [x, y, z] dimensions within tolerance)
"""

from dataclasses import dataclass, field, asdict
import math
from typing import Any, Dict, List, Optional, Sequence


DEFAULT_FLOAT_ABS_TOL_MM3 = 1e-3
DEFAULT_VOLUME_TOLERANCE_PCT = 2.0
DEFAULT_BBOX_TOLERANCE_MM = 0.5


@dataclass
class RuleResult:
    """Result of an individual geometric validation rule."""

    rule_name: str
    passed: bool
    message: str
    expected: Any = None
    actual: Any = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleResult":
        return cls(
            rule_name=str(data["rule_name"]),
            passed=bool(data["passed"]),
            message=str(data["message"]),
            expected=data.get("expected"),
            actual=data.get("actual"),
            details=dict(data.get("details", {})),
        )


@dataclass
class ValidationReport:
    """Aggregated validation report across all evaluated CAD rules."""

    passed: bool
    rules: List[RuleResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    geometry_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "rules": [r.to_dict() for r in self.rules],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "geometry_summary": dict(self.geometry_summary),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidationReport":
        return cls(
            passed=bool(data.get("passed", False)),
            rules=[RuleResult.from_dict(r) for r in data.get("rules", [])],
            errors=list(data.get("errors", [])),
            warnings=list(data.get("warnings", [])),
            geometry_summary=dict(data.get("geometry_summary", {})),
        )


@dataclass
class ExtractedGeometry:
    """Normalized geometric metrics extracted from `inspect_brep` output."""

    valid: bool
    body_count: int = 0
    total_volume_mm3: Optional[float] = None
    bounding_box_dimensions_mm: Optional[List[float]] = None
    hole_count: int = 0
    cylindrical_features: List[Dict[str, Any]] = field(default_factory=list)
    is_solid: bool = False
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_finite_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def extract_geometry_from_brep(brep_result: Optional[Dict[str, Any]]) -> ExtractedGeometry:
    """Extract normalized volume, bounding box, and hole features from `inspect_brep` output.

    Handles missing payloads, empty bodies, non-solid bodies, and malformed numeric
    fields gracefully without raising exceptions.
    """
    if not brep_result or not isinstance(brep_result, dict):
        return ExtractedGeometry(
            valid=False,
            error_reason="Missing or invalid inspect_brep payload.",
        )

    bodies = brep_result.get("bodies")
    if not isinstance(bodies, list) or len(bodies) == 0:
        return ExtractedGeometry(
            valid=False,
            body_count=int(brep_result.get("body_count", 0) or 0),
            error_reason="No solid bodies found in inspect_brep output.",
        )

    total_volume = 0.0
    all_cylindrical: List[Dict[str, Any]] = []
    any_solid = False

    for idx, body in enumerate(bodies):
        if not isinstance(body, dict):
            return ExtractedGeometry(
                valid=False,
                body_count=len(bodies),
                error_reason=f"Malformed body entry at index {idx}.",
            )

        is_solid = body.get("is_solid", True)
        if is_solid:
            any_solid = True

        vol = body.get("volume_mm3")
        if not _is_finite_number(vol) or float(vol) < 0.0:
            return ExtractedGeometry(
                valid=False,
                body_count=len(bodies),
                is_solid=any_solid,
                error_reason=f"Invalid or missing volume_mm3 in body '{body.get('body_id', idx)}': {vol!r}.",
            )
        total_volume += float(vol)

        cyls = body.get("cylindrical_features", [])
        if isinstance(cyls, list):
            all_cylindrical.extend(c for c in cyls if isinstance(c, dict))

    if not any_solid:
        return ExtractedGeometry(
            valid=False,
            body_count=len(bodies),
            total_volume_mm3=round(total_volume, 4),
            is_solid=False,
            error_reason="Generated geometry contains no solid B-Rep bodies (is_solid=False).",
        )

    overall_bbox = brep_result.get("overall_bounding_box") or bodies[0].get("bounding_box")
    dims_mm: Optional[List[float]] = None
    if isinstance(overall_bbox, dict):
        raw_dims = overall_bbox.get("dimensions")
        if isinstance(raw_dims, list) and len(raw_dims) == 3 and all(_is_finite_number(d) for d in raw_dims):
            dims_mm = [round(float(d), 4) for d in raw_dims]

    return ExtractedGeometry(
        valid=True,
        body_count=len(bodies),
        total_volume_mm3=round(total_volume, 4),
        bounding_box_dimensions_mm=dims_mm,
        hole_count=len(all_cylindrical),
        cylindrical_features=all_cylindrical,
        is_solid=True,
    )


def compute_expected_volume_from_spec(spec: Dict[str, Any]) -> Optional[float]:
    """Compute theoretical solid volume (mm^3) from a TaskSpec dictionary.

    Supports rectangular and circular mounting plates with through or blind holes.
    Returns None if required dimensions are missing or invalid.
    """
    if not isinstance(spec, dict):
        return None

    dims = spec.get("dimensions", {})
    shape_type = spec.get("shape_type", "rectangle")
    thickness = dims.get("thickness")
    if not _is_finite_number(thickness) or float(thickness) <= 0.0:
        return None
    t = float(thickness)

    if shape_type == "circle":
        diameter = dims.get("diameter")
        if not _is_finite_number(diameter) or float(diameter) <= 0.0:
            return None
        base_volume = math.pi * ((float(diameter) / 2.0) ** 2) * t
    else:
        width = dims.get("width")
        height = dims.get("height")
        if not (_is_finite_number(width) and _is_finite_number(height)):
            return None
        if float(width) <= 0.0 or float(height) <= 0.0:
            return None
        base_volume = float(width) * float(height) * t

    hole_volume = 0.0
    for hole in spec.get("holes", []):
        if not isinstance(hole, dict):
            continue
        hd = hole.get("diameter")
        if not _is_finite_number(hd) or float(hd) <= 0.0:
            continue
        depth = hole.get("depth", "through")
        effective_depth = t if depth == "through" else min(t, float(depth)) if _is_finite_number(depth) else t
        hole_volume += math.pi * ((float(hd) / 2.0) ** 2) * max(0.0, effective_depth)

    return round(max(0.0, base_volume - hole_volume), 4)


def validate_volume(
    actual_volume_mm3: Optional[float],
    expected_volume_mm3: Optional[float],
    tolerance_pct: float = DEFAULT_VOLUME_TOLERANCE_PCT,
    abs_tolerance_mm3: float = DEFAULT_FLOAT_ABS_TOL_MM3,
) -> RuleResult:
    """Validate generated B-Rep volume against expected volume within tolerance.

    Handles floating-point precision via combined relative percentage tolerance
    and minimum absolute epsilon (`abs_tolerance_mm3`), and fails gracefully on
    missing, non-numeric, or non-positive inputs.
    """
    if not _is_finite_number(expected_volume_mm3) or float(expected_volume_mm3) < 0.0:
        return RuleResult(
            rule_name="volume_validation",
            passed=False,
            message=f"Invalid expected volume: {expected_volume_mm3!r}.",
            expected=expected_volume_mm3,
            actual=actual_volume_mm3,
        )

    if not _is_finite_number(actual_volume_mm3) or float(actual_volume_mm3) <= 0.0:
        return RuleResult(
            rule_name="volume_validation",
            passed=False,
            message=f"Missing or non-positive actual CAD volume: {actual_volume_mm3!r}.",
            expected=float(expected_volume_mm3),
            actual=actual_volume_mm3,
        )

    if not _is_finite_number(tolerance_pct) or float(tolerance_pct) < 0.0:
        return RuleResult(
            rule_name="volume_validation",
            passed=False,
            message=f"Invalid tolerance percentage: {tolerance_pct!r}.",
            expected=float(expected_volume_mm3),
            actual=float(actual_volume_mm3),
        )

    actual = float(actual_volume_mm3)
    expected = float(expected_volume_mm3)
    tol_pct = float(tolerance_pct)

    abs_diff = abs(actual - expected)
    if expected == 0.0:
        rel_diff_pct = 0.0 if abs_diff <= abs_tolerance_mm3 else float("inf")
        allowed_diff = abs_tolerance_mm3
    else:
        rel_diff_pct = (abs_diff / expected) * 100.0
        allowed_diff = max(abs_tolerance_mm3, expected * (tol_pct / 100.0))

    passed = abs_diff <= allowed_diff + 1e-9
    details = {
        "actual_volume_mm3": round(actual, 4),
        "expected_volume_mm3": round(expected, 4),
        "abs_diff_mm3": round(abs_diff, 4),
        "rel_diff_pct": round(rel_diff_pct, 4) if math.isfinite(rel_diff_pct) else None,
        "tolerance_pct": tol_pct,
        "allowed_diff_mm3": round(allowed_diff, 4),
    }

    if passed:
        msg = (
            f"Volume check passed: actual {actual:.2f} mm³ vs expected {expected:.2f} mm³ "
            f"(delta {rel_diff_pct:.2f}% <= {tol_pct:.2f}% tolerance)."
        )
    else:
        msg = (
            f"Volume mismatch: actual {actual:.2f} mm³ vs expected {expected:.2f} mm³ "
            f"(delta {abs_diff:.2f} mm³ / {rel_diff_pct:.2f}% exceeds {tol_pct:.2f}% tolerance)."
        )

    return RuleResult(
        rule_name="volume_validation",
        passed=passed,
        message=msg,
        expected=round(expected, 4),
        actual=round(actual, 4),
        details=details,
    )


def validate_hole_count(
    actual_hole_count: Optional[int],
    expected_hole_count: Optional[int],
    cylindrical_features: Optional[List[Dict[str, Any]]] = None,
) -> RuleResult:
    """Validate detected cylindrical hole count against expected hole count."""
    if expected_hole_count is None or isinstance(expected_hole_count, bool) or not isinstance(expected_hole_count, int) or expected_hole_count < 0:
        return RuleResult(
            rule_name="hole_count_validation",
            passed=False,
            message=f"Invalid expected hole count: {expected_hole_count!r}.",
            expected=expected_hole_count,
            actual=actual_hole_count,
        )

    if actual_hole_count is None or isinstance(actual_hole_count, bool) or not isinstance(actual_hole_count, int) or actual_hole_count < 0:
        return RuleResult(
            rule_name="hole_count_validation",
            passed=False,
            message=f"Missing or invalid actual hole count: {actual_hole_count!r}.",
            expected=expected_hole_count,
            actual=actual_hole_count,
        )

    passed = actual_hole_count == expected_hole_count
    delta = actual_hole_count - expected_hole_count
    details: Dict[str, Any] = {
        "expected_hole_count": expected_hole_count,
        "actual_hole_count": actual_hole_count,
        "delta": delta,
    }
    if cylindrical_features is not None:
        details["detected_diameters_mm"] = [
            c.get("diameter_mm") for c in cylindrical_features if isinstance(c, dict) and "diameter_mm" in c
        ]

    if passed:
        msg = f"Hole count check passed: {actual_hole_count} hole(s) matched expected {expected_hole_count}."
    else:
        msg = f"Hole count mismatch: found {actual_hole_count} hole(s), expected {expected_hole_count} (delta {delta:+d})."

    return RuleResult(
        rule_name="hole_count_validation",
        passed=passed,
        message=msg,
        expected=expected_hole_count,
        actual=actual_hole_count,
        details=details,
    )


def validate_bounding_box(
    actual_dims_mm: Optional[Sequence[float]],
    expected_dims_mm: Optional[Sequence[float]],
    tolerance_mm: float = DEFAULT_BBOX_TOLERANCE_MM,
) -> RuleResult:
    """Validate 3D bounding box [x, y, z] dimensions within linear tolerance."""
    if not expected_dims_mm or len(expected_dims_mm) != 3 or not all(_is_finite_number(v) for v in expected_dims_mm):
        return RuleResult(
            rule_name="bounding_box_validation",
            passed=False,
            message=f"Invalid expected bounding box dimensions: {expected_dims_mm!r}.",
            expected=list(expected_dims_mm) if expected_dims_mm is not None else None,
            actual=list(actual_dims_mm) if actual_dims_mm is not None else None,
        )

    if not actual_dims_mm or len(actual_dims_mm) != 3 or not all(_is_finite_number(v) for v in actual_dims_mm):
        return RuleResult(
            rule_name="bounding_box_validation",
            passed=False,
            message=f"Missing or invalid actual bounding box dimensions: {actual_dims_mm!r}.",
            expected=[float(v) for v in expected_dims_mm],
            actual=list(actual_dims_mm) if actual_dims_mm is not None else None,
        )

    actual = [round(float(v), 4) for v in actual_dims_mm]
    expected = [round(float(v), 4) for v in expected_dims_mm]
    axis_diffs = [round(abs(a - e), 4) for a, e in zip(actual, expected)]
    max_diff = max(axis_diffs)
    passed = max_diff <= tolerance_mm + 1e-9

    details = {
        "actual_dimensions_mm": actual,
        "expected_dimensions_mm": expected,
        "axis_diffs_mm": axis_diffs,
        "max_diff_mm": max_diff,
        "tolerance_mm": tolerance_mm,
    }

    if passed:
        msg = f"Bounding box check passed: {actual} mm matches expected {expected} mm (max diff {max_diff:.3f} mm)."
    else:
        msg = f"Bounding box mismatch: {actual} mm vs expected {expected} mm (max diff {max_diff:.3f} mm > {tolerance_mm:.3f} mm)."

    return RuleResult(
        rule_name="bounding_box_validation",
        passed=passed,
        message=msg,
        expected=expected,
        actual=actual,
        details=details,
    )


class ValidationEngine:
    """Orchestrates modular geometric validation rules on `inspect_brep` outputs."""

    def __init__(
        self,
        default_volume_tolerance_pct: float = DEFAULT_VOLUME_TOLERANCE_PCT,
        default_bbox_tolerance_mm: float = DEFAULT_BBOX_TOLERANCE_MM,
    ):
        self.default_volume_tolerance_pct = default_volume_tolerance_pct
        self.default_bbox_tolerance_mm = default_bbox_tolerance_mm

    def validate(
        self,
        brep_result: Optional[Dict[str, Any]],
        expected_outcome: Dict[str, Any],
        check_bounding_box: bool = True,
    ) -> ValidationReport:
        """Evaluate volume, hole count, and optional bounding box rules against `brep_result`."""
        geom = extract_geometry_from_brep(brep_result)
        errors: List[str] = []
        warnings: List[str] = []
        rules: List[RuleResult] = []

        if not geom.valid:
            err_msg = geom.error_reason or "Invalid or missing B-Rep geometry."
            errors.append(err_msg)
            rules.append(
                RuleResult(
                    rule_name="geometry_integrity",
                    passed=False,
                    message=err_msg,
                )
            )
            return ValidationReport(
                passed=False,
                rules=rules,
                errors=errors,
                warnings=warnings,
                geometry_summary=geom.to_dict(),
            )

        # 1. Volume Validation
        exp_vol = expected_outcome.get("expected_volume_mm3")
        tol_pct = float(expected_outcome.get("volume_tolerance_pct", self.default_volume_tolerance_pct))
        vol_rule = validate_volume(
            actual_volume_mm3=geom.total_volume_mm3,
            expected_volume_mm3=exp_vol,
            tolerance_pct=tol_pct,
        )
        rules.append(vol_rule)
        if not vol_rule.passed:
            errors.append(vol_rule.message)

        # 2. Hole Count Validation
        exp_holes = expected_outcome.get("hole_count")
        hole_rule = validate_hole_count(
            actual_hole_count=geom.hole_count,
            expected_hole_count=exp_holes,
            cylindrical_features=geom.cylindrical_features,
        )
        rules.append(hole_rule)
        if not hole_rule.passed:
            errors.append(hole_rule.message)

        # 3. Bounding Box Validation (when expected dimensions provided)
        exp_bbox = expected_outcome.get("bounding_box_mm")
        if check_bounding_box and exp_bbox is not None:
            bbox_rule = validate_bounding_box(
                actual_dims_mm=geom.bounding_box_dimensions_mm,
                expected_dims_mm=exp_bbox,
                tolerance_mm=self.default_bbox_tolerance_mm,
            )
            rules.append(bbox_rule)
            if not bbox_rule.passed:
                errors.append(bbox_rule.message)

        all_passed = all(r.passed for r in rules)
        return ValidationReport(
            passed=all_passed,
            rules=rules,
            errors=errors,
            warnings=warnings,
            geometry_summary=geom.to_dict(),
        )
