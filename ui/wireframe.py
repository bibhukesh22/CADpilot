"""Pure-Python formatting and SVG wireframe helpers for the CADPilot Streamlit UI.

Kept free of Streamlit imports so formatting, step-progress mapping, and SVG
wireframe preview generation remain independently unit-testable.
"""

import html
from typing import Any, Dict, List, Optional


def format_prompt_label(entry: Dict[str, Any]) -> str:
    """Format a benchmark prompt entry for dropdown selection."""
    pid = entry.get("id", "BM-???")
    title = entry.get("title", "Untitled")
    family = entry.get("part_family", "custom")
    comp = entry.get("complexity", "medium")
    readiness = entry.get("execution_readiness", "ready")
    tag = "Ready" if readiness == "ready" else "Phase 5"
    return f"{pid} | {title} [{family} / {comp} / {tag}]"


def build_pipeline_stages(agent_state: Dict[str, Any], execution_status: str) -> List[Dict[str, str]]:
    """Derive high-level LangGraph node completion states for the UI monitor."""
    has_spec = bool(agent_state.get("spec"))
    feas = agent_state.get("feasibility", {})
    has_plan = bool(agent_state.get("plan", {}).get("steps"))
    obs = agent_state.get("observations", [])
    retries = int(agent_state.get("reflection", {}).get("retry_count", 0))

    if execution_status == "pending_phase5":
        return [
            {"stage": "1. Spec Extractor", "state": "pending"},
            {"stage": "2. Feasibility Checker", "state": "pending"},
            {"stage": "3. Sequence Planner", "state": "pending"},
            {"stage": "4. Tool Executor", "state": "pending"},
            {"stage": "5. Reflection", "state": "skipped"},
        ]

    feas_ok = feas.get("is_feasible", False)
    return [
        {"stage": "1. Spec Extractor", "state": "done" if has_spec else "pending"},
        {
            "stage": "2. Feasibility Checker",
            "state": "done" if feas_ok else ("warning" if has_spec else "pending"),
        },
        {"stage": "3. Sequence Planner", "state": "done" if has_plan else "pending"},
        {
            "stage": "4. Tool Executor",
            "state": (
                "done"
                if execution_status == "completed"
                else ("error" if obs and execution_status == "failed" else "pending")
            ),
        },
        {
            "stage": "5. Reflection",
            "state": f"triggered ({retries}x)" if retries > 0 else "not_needed",
        },
    ]


def build_step_progress_rows(agent_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert ExecutionObservation records into tabular rows for the UI."""
    rows: List[Dict[str, Any]] = []
    for obs in agent_state.get("observations", []):
        bbox = obs.get("bounding_box") or {}
        dims = bbox.get("dimensions")
        rows.append(
            {
                "Step": obs.get("step_index"),
                "Tool": obs.get("tool"),
                "Status": obs.get("status"),
                "Bounding Box (mm)": str(dims) if dims else "-",
                "Details": obs.get("error_message") or obs.get("error_hint") or "OK",
            }
        )
    return rows


def build_benchmark_history_rows(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Summarize BenchmarkRunResult dicts into compact table rows."""
    rows: List[Dict[str, Any]] = []
    for item in history:
        report = item.get("validation_report", {})
        geom = report.get("geometry_summary", {})
        vol = geom.get("total_volume_mm3")
        holes = geom.get("hole_count")
        rows.append(
            {
                "ID": item.get("prompt_id"),
                "Title": item.get("title"),
                "Family": item.get("part_family"),
                "Exec Status": item.get("execution_status"),
                "Validation": "PASS" if item.get("validation_passed") else "FAIL",
                "Volume (mm³)": float(vol) if vol is not None else None,
                "Holes": int(holes) if holes is not None else None,
                "Retries": int(item.get("retry_count", 0)),
                "Time (s)": float(item.get("duration_seconds", 0.0)),
            }
        )
    return rows


def build_svg_wireframe_preview(
    spec: Optional[Dict[str, Any]] = None,
    expected_outcome: Optional[Dict[str, Any]] = None,
    title: str = "2D CAD Wireframe Preview",
) -> str:
    """Render a lightweight top-down SVG wireframe placeholder from TaskSpec or expected_outcome."""
    source = spec if (spec and spec.get("dimensions")) else None
    exp = expected_outcome or {}

    if source:
        shape_type = source.get("shape_type", "rectangle")
        dims = source.get("dimensions", {})
        holes = source.get("holes", [])
        fillet_r = (source.get("fillets") or {}).get("radius") or 0.0
    else:
        shape_type = exp.get("shape_type", "rectangle")
        dims = exp.get("dimensions_mm", {})
        holes = [
            {"position": h.get("position_mm", [0.0, 0.0]), "diameter": h.get("diameter_mm", 3.0)}
            for h in exp.get("holes", [])
        ]
        fillet_r = exp.get("fillet_radius_mm") or 0.0

    canvas = 260
    pad = 30
    draw_span = canvas - 2 * pad

    if shape_type == "circle":
        outer_d = float(dims.get("diameter") or dims.get("outer_diameter") or dims.get("base_diameter") or 50.0)
        w = outer_d
        h = outer_d
    else:
        w = float(dims.get("width") or dims.get("leg2") or 50.0)
        h = float(dims.get("height") or dims.get("leg1") or 50.0)

    scale = draw_span / max(w, h, 1.0)
    cx, cy = canvas / 2.0, canvas / 2.0
    svg_parts: List[str] = [
        f'<svg viewBox="0 0 {canvas} {canvas}" width="100%" height="260" '
        'xmlns="http://www.w3.org/2000/svg" style="background:#0f172a;border-radius:8px;border:1px solid #334155;">',
        f'<text x="12" y="20" fill="#94a3b8" font-size="11" font-family="monospace">{html.escape(title)}</text>',
    ]

    if shape_type == "circle":
        r_px = (w / 2.0) * scale
        svg_parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r_px:.2f}" '
            'fill="rgba(56,189,248,0.12)" stroke="#38bdf8" stroke-width="2" />'
        )
    else:
        rw = w * scale
        rh = h * scale
        rx = min(float(fillet_r) * scale, rw / 4.0, rh / 4.0) if fillet_r else 0.0
        x0 = cx - rw / 2.0
        y0 = cy - rh / 2.0
        svg_parts.append(
            f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{rw:.2f}" height="{rh:.2f}" rx="{rx:.2f}" '
            'fill="rgba(56,189,248,0.12)" stroke="#38bdf8" stroke-width="2" />'
        )

    for hole in holes:
        pos = hole.get("position", [0.0, 0.0])
        hd = float(hole.get("diameter", 3.0))
        hx = cx + float(pos[0]) * scale
        hy = cy - float(pos[1]) * scale
        hr = max(2.0, (hd / 2.0) * scale)
        svg_parts.append(
            f'<circle cx="{hx:.2f}" cy="{hy:.2f}" r="{hr:.2f}" '
            'fill="#0f172a" stroke="#f59e0b" stroke-width="1.5" />'
        )

    dim_label = f"{w:g} x {h:g} mm" if shape_type != "circle" else f"Dia {w:g} mm"
    svg_parts.append(
        f'<text x="12" y="{canvas - 10}" fill="#cbd5e1" font-size="11" font-family="monospace">'
        f"{html.escape(dim_label)} | Holes: {len(holes)}</text>"
    )
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)
