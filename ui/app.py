"""CADPilot Streamlit UI Wireframe.

Interactive benchmarking and CAD validation console connecting:
- 30-prompt benchmark dataset (`benchmarks/`)
- LangGraph / Standalone CADPilotAgent (`agent/`)
- Fusion 360 / Mock JSON-RPC Bridge (`client/` & `mock/`)
- Modular Geometric Validation Rule Engine (`validation/`)

Run with:
    streamlit run ui/app.py
"""

import os
import sys

# Ensure repository root is importable when launched via `streamlit run ui/app.py`
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import streamlit as st

from benchmarks import (
    get_benchmark_summary,
    load_prompts,
)
from ui.wireframe import (
    build_benchmark_history_rows,
    build_pipeline_stages,
    build_step_progress_rows,
    build_svg_wireframe_preview,
    format_prompt_label,
)
from validation import (
    check_bridge_health,
    run_benchmark_prompt,
    run_benchmark_suite,
)


def _init_session_state() -> None:
    if "run_history" not in st.session_state:
        st.session_state["run_history"] = []
    if "latest_result" not in st.session_state:
        st.session_state["latest_result"] = None
    if "batch_summary" not in st.session_state:
        st.session_state["batch_summary"] = None


def main() -> None:
    st.set_page_config(
        page_title="CADPilot Benchmark & Validation Console",
        page_icon="📐",
        layout="wide",
    )
    _init_session_state()

    # 1. Project Title and Introduction
    st.title("CADPilot — Autonomous CAD Generation & Benchmark Console")
    st.markdown(
        "Closed-loop **Perceive → Reason → Plan → Act → Reflect** CAD generation workbench "
        "powered by `CADPilotAgent`, the Fusion 360 / Mock JSON-RPC Bridge, and the "
        "`ValidationEngine` (volume, hole count, and bounding-box verification)."
    )

    summary = get_benchmark_summary()

    # Sidebar: Bridge & Filter Controls
    with st.sidebar:
        st.header("Bridge & Run Settings")
        bridge_mode = st.radio(
            "Bridge Endpoint",
            options=["mock", "http://127.0.0.1:9090/"],
            index=0,
            help="Use 'mock' for in-process MockBridge or HTTP URL for live Fusion 360 Add-In.",
        )

        if st.button("Ping Bridge", use_container_width=True):
            health = check_bridge_health(bridge_mode)
            if health.get("reachable"):
                st.success(f"Online ({health.get('bridge_mode')}): {health.get('message')}")
            else:
                st.error(f"Unreachable: {health.get('message')}")

        volume_tol_pct = st.slider(
            "Volume Tolerance (%)",
            min_value=0.5,
            max_value=15.0,
            value=2.0,
            step=0.5,
        )

        st.divider()
        st.subheader("Benchmark Filters")
        family_filter = st.selectbox(
            "Part Family",
            options=["All", "mounting_plate", "l_bracket", "standoff"],
        )
        complexity_filter = st.selectbox(
            "Complexity",
            options=["All", "simple", "medium", "complex"],
        )
        readiness_filter = st.selectbox(
            "Readiness",
            options=["All", "ready", "phase5_pending"],
        )

        st.divider()
        st.caption(
            f"Dataset: {summary['total_prompts']} prompts "
            f"(Ready: {summary['by_readiness'].get('ready', 0)}, "
            f"Phase 5 Pending: {summary['by_readiness'].get('phase5_pending', 0)})"
        )

    # 2. Benchmark Prompt Selection
    filtered_prompts = load_prompts(
        part_family=None if family_filter == "All" else family_filter,
        complexity=None if complexity_filter == "All" else complexity_filter,
        execution_readiness=None if readiness_filter == "All" else readiness_filter,
    )

    if not filtered_prompts:
        st.warning("No benchmark prompts match the selected sidebar filters.")
        return

    labels = [format_prompt_label(p) for p in filtered_prompts]
    selected_idx = st.selectbox(
        "Select Benchmark Prompt (1–30)",
        options=range(len(filtered_prompts)),
        format_func=lambda idx: labels[idx],
    )
    selected_prompt = filtered_prompts[selected_idx]

    use_custom_prompt = st.checkbox("Override with custom natural-language prompt", value=False)
    active_prompt_text = selected_prompt["prompt"]
    if use_custom_prompt:
        active_prompt_text = st.text_area(
            "Custom CAD Prompt",
            value=selected_prompt["prompt"],
            height=90,
        )

    # 3. Prompt Display & Wireframe Preview Placeholder
    col_prompt, col_preview = st.columns([3, 2])
    with col_prompt:
        st.subheader(f"{selected_prompt['id']}: {selected_prompt['title']}")
        st.info(active_prompt_text)

        exp = selected_prompt["expected_outcome"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Part Family", selected_prompt["part_family"])
        m2.metric("Expected Holes", exp["hole_count"])
        m3.metric("Expected Vol (mm³)", f"{exp['expected_volume_mm3']:.1f}")
        m4.metric("Readiness", selected_prompt["execution_readiness"])

        with st.expander("Expected Outcome Specification", expanded=False):
            st.json(exp)

    with col_preview:
        st.subheader("3D / CAD Wireframe Placeholder")
        latest = st.session_state.get("latest_result")
        latest_spec = None
        if latest and latest.get("prompt_id") == selected_prompt["id"]:
            latest_spec = latest.get("agent_state", {}).get("spec")
        svg_html = build_svg_wireframe_preview(
            spec=latest_spec,
            expected_outcome=exp,
            title=f"{selected_prompt['id']} Top-Down Wireframe",
        )
        st.markdown(svg_html, unsafe_allow_html=True)
        st.caption(
            "Top-down parametric wireframe preview (interactive WebGL STEP/STL viewer "
            "connects in live Fusion integration phase)."
        )

    # 4. CAD Generation Execution Interface
    st.divider()
    exec_col1, exec_col2, exec_col3 = st.columns([2, 2, 3])
    with exec_col1:
        run_single_clicked = st.button("Run Selected Prompt", type="primary", use_container_width=True)
    with exec_col2:
        run_batch_clicked = st.button("Run All Ready Benchmarks (15)", use_container_width=True)
    with exec_col3:
        force_pending = st.checkbox(
            "Force execute Phase 5 pending prompts",
            value=False,
            help="Phase 5 (L-bracket / standoff) planners are not yet implemented in agent/.",
        )

    if run_single_clicked:
        prompt_to_run = dict(selected_prompt)
        if use_custom_prompt:
            prompt_to_run["id"] = f"{selected_prompt['id']}-CUSTOM"
            prompt_to_run["prompt"] = active_prompt_text

        with st.spinner(f"Executing {prompt_to_run['id']} on bridge '{bridge_mode}'..."):
            run_res = run_benchmark_prompt(
                prompt_to_run,
                endpoint_url=bridge_mode,
                volume_tolerance_pct=volume_tol_pct,
                force_execute=force_pending,
            ).to_dict()
        st.session_state["latest_result"] = run_res
        st.session_state["run_history"].insert(0, run_res)

    if run_batch_clicked:
        ready_list = load_prompts(execution_readiness="ready")
        with st.spinner(f"Running {len(ready_list)} ready benchmark prompts on '{bridge_mode}'..."):
            batch_out = run_benchmark_suite(
                ready_list,
                endpoint_url=bridge_mode,
                volume_tolerance_pct=volume_tol_pct,
                include_pending=False,
            )
        st.session_state["batch_summary"] = batch_out
        for item in reversed(batch_out["results"]):
            st.session_state["run_history"].insert(0, item)
        if batch_out["results"]:
            st.session_state["latest_result"] = batch_out["results"][0]

    # 5. Execution Status & 6. Validation Results
    latest = st.session_state.get("latest_result")
    if latest:
        st.divider()
        st.subheader(f"Execution & Validation Results — {latest['prompt_id']}")

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Execution Status", latest["execution_status"].upper())
        s2.metric("Validation", "PASS" if latest["validation_passed"] else "FAIL")
        s3.metric("Retries", latest["retry_count"])
        s4.metric("Duration", f"{latest['duration_seconds']:.3f}s")

        if latest.get("notes"):
            st.warning(latest["notes"])

        tab_status, tab_validation, tab_raw = st.tabs(
            ["Step-by-Step Progress Monitor", "Validation Rule Report", "Raw B-Rep & Export"]
        )

        with tab_status:
            stages = build_pipeline_stages(
                latest.get("agent_state", {}),
                latest.get("execution_status", ""),
            )
            stage_cols = st.columns(len(stages))
            for idx, stg in enumerate(stages):
                stage_cols[idx].metric(stg["stage"], stg["state"])

            step_rows = build_step_progress_rows(latest.get("agent_state", {}))
            if step_rows:
                st.table(step_rows)
            else:
                st.info("No bridge tool steps were executed for this run.")

        with tab_validation:
            v_rep = latest.get("validation_report", {})
            for rule in v_rep.get("rules", []):
                if rule.get("passed"):
                    st.success(f"**{rule['rule_name']}**: {rule['message']}")
                else:
                    st.error(f"**{rule['rule_name']}**: {rule['message']}")

            if v_rep.get("warnings"):
                for w in v_rep["warnings"]:
                    st.warning(w)

        with tab_raw:
            final_out = latest.get("agent_state", {}).get("final_output", {})
            st.json(final_out or {"info": "No final_output generated."})

    # 7. Basic Benchmark Results Display
    batch_summary = st.session_state.get("batch_summary")
    if batch_summary:
        st.divider()
        st.subheader("Batch Benchmark Summary")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Executed", batch_summary["executed_count"])
        b2.metric("Passed", batch_summary["passed_count"])
        b3.metric("Pass Rate", f"{batch_summary['pass_rate_pct']}%")
        b4.metric("Total Retries", batch_summary["total_retries"])

    history = st.session_state.get("run_history", [])
    if history:
        st.divider()
        st.subheader("Session Benchmark History")
        st.table(build_benchmark_history_rows(history))


if __name__ == "__main__":
    main()
