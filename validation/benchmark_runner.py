"""Benchmark Execution & Validation Runner for CADPilot.

Connects the benchmark prompt dataset, CADPilotAgent / CADPilotClient, and the
ValidationEngine to execute individual prompts or suites and record structured
execution and geometric validation metrics.
"""

from dataclasses import dataclass, field, asdict
import time
from typing import Any, Dict, List, Optional

from agent.agent import CADPilotAgent
from client.test_client import CADPilotClient
from .rules import (
    RuleResult,
    ValidationEngine,
    ValidationReport,
)


@dataclass
class BenchmarkRunResult:
    """Structured outcome for a single benchmark prompt execution and validation."""

    prompt_id: str
    title: str
    part_family: str
    complexity: str
    execution_readiness: str
    execution_status: str  # "completed", "failed", "pending_phase5", "error"
    validation_passed: bool
    retry_count: int = 0
    duration_seconds: float = 0.0
    agent_state: Dict[str, Any] = field(default_factory=dict)
    validation_report: Dict[str, Any] = field(default_factory=dict)
    export_info: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_bridge_health(endpoint_url: str = "mock") -> Dict[str, Any]:
    """Ping the configured CADPilot bridge endpoint and report status/mode."""
    try:
        client = CADPilotClient(endpoint_url=endpoint_url)
        resp = client.ping()
        if "result" in resp:
            res = resp["result"]
            return {
                "reachable": True,
                "bridge_mode": res.get("bridge_mode", "unknown"),
                "fusion_version": res.get("fusion_version", "unknown"),
                "message": res.get("message", "Bridge ready."),
            }
        err = resp.get("error", {})
        return {
            "reachable": False,
            "bridge_mode": "error",
            "message": err.get("message", "Unknown bridge ping error."),
        }
    except Exception as exc:
        return {
            "reachable": False,
            "bridge_mode": "unreachable",
            "message": str(exc),
        }


def run_benchmark_prompt(
    prompt_entry: Dict[str, Any],
    endpoint_url: str = "mock",
    volume_tolerance_pct: Optional[float] = None,
    force_execute: bool = False,
) -> BenchmarkRunResult:
    """Execute a single benchmark prompt through CADPilotAgent and validate output."""
    pid = str(prompt_entry.get("id", "CUSTOM"))
    title = str(prompt_entry.get("title", "Custom Prompt"))
    part_family = str(prompt_entry.get("part_family", "custom"))
    complexity = str(prompt_entry.get("complexity", "medium"))
    readiness = str(prompt_entry.get("execution_readiness", "ready"))
    expected_outcome = dict(prompt_entry.get("expected_outcome", {}))

    if readiness == "phase5_pending" and not force_execute:
        msg = (
            f"Prompt {pid} ({part_family}) targets Phase 5 part-family support "
            "(L-bracket / standoff planner & bridge extensions) which is not yet implemented."
        )
        pending_report = ValidationReport(
            passed=False,
            rules=[
                RuleResult(
                    rule_name="phase5_readiness",
                    passed=False,
                    message=msg,
                )
            ],
            warnings=[msg],
        )
        return BenchmarkRunResult(
            prompt_id=pid,
            title=title,
            part_family=part_family,
            complexity=complexity,
            execution_readiness=readiness,
            execution_status="pending_phase5",
            validation_passed=False,
            validation_report=pending_report.to_dict(),
            notes=msg,
        )

    if volume_tolerance_pct is not None:
        expected_outcome["volume_tolerance_pct"] = float(volume_tolerance_pct)

    start_time = time.perf_counter()
    agent = CADPilotAgent(endpoint_url=endpoint_url, verbose=False)
    state = agent.run(prompt_entry["prompt"])
    elapsed = round(time.perf_counter() - start_time, 4)

    status = str(state.get("status", "failed"))
    retry_count = int(state.get("reflection", {}).get("retry_count", 0))
    final_output = state.get("final_output", {})
    export_info = dict(final_output.get("export", {}))
    should_succeed = bool(expected_outcome.get("should_succeed", True))

    if not should_succeed:
        rejected_cleanly = status == "failed"
        rule = RuleResult(
            rule_name="expected_rejection_validation",
            passed=rejected_cleanly,
            expected="failed",
            actual=status,
            message=(
                f"Adversarial prompt cleanly rejected as expected ({state.get('error')})."
                if rejected_cleanly
                else "Adversarial prompt unexpectedly succeeded instead of rejecting infeasible geometry."
            ),
        )
        report = ValidationReport(
            passed=rejected_cleanly,
            rules=[rule],
            errors=[] if rejected_cleanly else [rule.message],
        )
        return BenchmarkRunResult(
            prompt_id=pid,
            title=title,
            part_family=part_family,
            complexity=complexity,
            execution_readiness=readiness,
            execution_status=status,
            validation_passed=rejected_cleanly,
            retry_count=retry_count,
            duration_seconds=elapsed,
            agent_state=dict(state),
            validation_report=report.to_dict(),
            export_info=export_info,
            notes=rule.message,
        )

    if status != "completed":
        err_msg = str(state.get("error") or "Agent execution did not complete.")
        fail_report = ValidationReport(
            passed=False,
            rules=[
                RuleResult(
                    rule_name="agent_execution",
                    passed=False,
                    expected="completed",
                    actual=status,
                    message=err_msg,
                )
            ],
            errors=[err_msg],
        )
        return BenchmarkRunResult(
            prompt_id=pid,
            title=title,
            part_family=part_family,
            complexity=complexity,
            execution_readiness=readiness,
            execution_status=status,
            validation_passed=False,
            retry_count=retry_count,
            duration_seconds=elapsed,
            agent_state=dict(state),
            validation_report=fail_report.to_dict(),
            export_info=export_info,
            notes=err_msg,
        )

    brep_result = final_output.get("brep")
    engine = ValidationEngine()
    report = engine.validate(brep_result, expected_outcome)

    notes = ""
    if (
        endpoint_url == "mock"
        and expected_outcome.get("shape_type") == "circle"
        and not report.passed
    ):
        note = (
            "Note: MockBridge SimulatedCADState.extrude approximates circular base volume "
            "using bounding-box area (d*d*t) rather than pi*r^2*t."
        )
        report.warnings.append(note)
        notes = note

    return BenchmarkRunResult(
        prompt_id=pid,
        title=title,
        part_family=part_family,
        complexity=complexity,
        execution_readiness=readiness,
        execution_status=status,
        validation_passed=report.passed,
        retry_count=retry_count,
        duration_seconds=elapsed,
        agent_state=dict(state),
        validation_report=report.to_dict(),
        export_info=export_info,
        notes=notes,
    )


def run_benchmark_suite(
    prompts: List[Dict[str, Any]],
    endpoint_url: str = "mock",
    volume_tolerance_pct: Optional[float] = None,
    include_pending: bool = False,
) -> Dict[str, Any]:
    """Run a list of benchmark prompts and compute summary statistics."""
    results: List[BenchmarkRunResult] = []
    for entry in prompts:
        if not include_pending and entry.get("execution_readiness") == "phase5_pending":
            continue
        results.append(
            run_benchmark_prompt(
                entry,
                endpoint_url=endpoint_url,
                volume_tolerance_pct=volume_tolerance_pct,
            )
        )

    executed = [r for r in results if r.execution_status != "pending_phase5"]
    passed = [r for r in executed if r.validation_passed]
    total_retries = sum(r.retry_count for r in executed)

    return {
        "total_selected": len(results),
        "executed_count": len(executed),
        "passed_count": len(passed),
        "failed_count": len(executed) - len(passed),
        "pass_rate_pct": round((len(passed) / len(executed) * 100.0), 2) if executed else 0.0,
        "total_retries": total_retries,
        "results": [r.to_dict() for r in results],
    }
