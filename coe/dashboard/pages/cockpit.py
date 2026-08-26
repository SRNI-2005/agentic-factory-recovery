"""Cockpit page — narrative-driven recovery chat with live decision feed."""
from __future__ import annotations


def render() -> None:
    import streamlit as st

    from coe.agents.llm_client import LLMConfigError, require_llm_config
    from coe.config import get_settings

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    # --- LLM preflight ---------------------------------------------------
    try:
        require_llm_config(get_settings())
    except LLMConfigError as exc:
        st.warning(f"Recovery chat unavailable: {exc}")
        st.stop()

    # --- session-state chat history ---------------------------------------
    if "cockpit_messages" not in st.session_state:
        st.session_state["cockpit_messages"] = []

    _render_history(st.session_state["cockpit_messages"])

    # --- user input -------------------------------------------------------
    prompt = st.chat_input("Describe the disruption to recover from…")
    if not prompt:
        return

    st.session_state["cockpit_messages"].append({"role": "user", "content": prompt})
    _render_user_bubble(prompt)

    with st.chat_message("assistant"):
        _run_recovery(instance_name, prompt)


# ------------------------------------------------------------------
# rendering helpers
# ------------------------------------------------------------------

def _render_history(messages: list[dict]) -> None:
    import streamlit as st

    for msg in messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_user_bubble(text: str) -> None:
    import streamlit as st

    with st.chat_message("user"):
        st.markdown(text)


_NODE_LABELS: dict[str, str] = {
    "entry": "Initializing recovery pipeline",
    "translate": "Translating disruption narrative",
    "ingest": "Ingesting telemetry data",
    "machine_agent": "Investigating machine status",
    "production_agent": "Investigating production constraints",
    "inventory_agent": "Investigating inventory & materials",
    "worker_agent": "Investigating worker availability",
    "strategy": "Formulating recovery strategy",
    "manager_compile": "Compiling strategy for solver",
    "solve_node": "Solving schedule (~2 min expected on factory floor)",
    "gate_node": "Validating solution quality",
    "commit_node": "Committing recovery schedule",
    "verify_node": "Verifying schedule integrity",
    "explain_node": "Generating explanation",
}


def _run_recovery(instance_name: str, narrative: str) -> None:
    import streamlit as st

    from coe.agents.graph import execute_recovery_streaming

    # Capture the active schedule BEFORE recovery runs
    before_entries = _fetch_active_entries(instance_name)

    with st.status("Running recovery pipeline…", expanded=True) as status:
        feed_lines: list[str] = []
        feed_area = st.empty()

        result = None
        for chunk in execute_recovery_streaming(
            instance_name, trigger="CLI", narrative=narrative,
        ):
            if "node" in chunk:
                label = _NODE_LABELS.get(chunk["node"], chunk["node"])
                feed_lines.append(f"- {label}")
                feed_area.markdown("\n".join(feed_lines))
            else:
                result = chunk

    status.update(
        label=f"Recovery finished — **{result['status']}**",
        state="complete",
    )

    final = result["state"]
    _render_outcome(result["status"], final)

    # Schedule diff animation on COMMITTED recovery
    if result["status"] == "COMMITTED":
        _render_diff_animation(instance_name, before_entries)

    st.session_state["cockpit_messages"].append(
        {"role": "assistant", "content": _outcome_text(result["status"], final)},
    )


# ------------------------------------------------------------------
# outcome rendering
# ------------------------------------------------------------------

_SOLVER_STATUSES = ("OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN")


def _render_outcome(status: str, state) -> None:
    import streamlit as st

    st.markdown(f"**Outcome:** `{status}`")

    solution = state.solution
    if solution:
        cols = st.columns(3)
        cols[0].metric("Makespan", solution.get("makespan", "—"))
        cols[1].metric("Total tardiness", solution.get("total_tardiness", "—"))
        solver_status = solution.get("status", "—")
        cols[2].metric("Solver status", solver_status)

    if status == "UNKNOWN":
        st.info(
            "UNKNOWN means the solver was budget-starved (time limit hit "
            "before proving feasibility).  It does **not** mean a "
            "material-conflict."
        )

    if status == "COMMITTED":
        _render_explanation(state)


def _render_explanation(state) -> None:
    import streamlit as st
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import make_engine

    vid = state.committed_version_id
    if vid is None:
        return

    with Session(make_engine()) as session:
        version = session.get(ScheduleVersion, vid)
        if version is None:
            return
        explanation = (
            session.query(ScheduleExplanation)
            .filter(ScheduleExplanation.version_id == version.id)
            .first()
        )
        if explanation is not None:
            st.subheader("Schedule Explanation")
            st.markdown(explanation.rationale)


def _fetch_active_entries(instance_name: str) -> list[dict]:
    """Return the active schedule entry dicts for *instance_name*, or []."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from coe.db.session import make_engine

    with Session(make_engine()) as session:
        row = session.execute(
            text("SELECT id FROM instances WHERE name = :n"),
            {"n": instance_name},
        ).mappings().first()
        if row is None:
            return []
        instance_id: int = row["id"]
        entries = session.execute(text(
            "SELECT se.*, m.name AS machine_name, j.name AS job_name, "
            "       o.sequence_number, w.name AS worker_name "
            "FROM active_schedule asev "
            "JOIN schedule_entries se ON se.id = asev.id "
            "JOIN machines m ON m.id = se.machine_id "
            "JOIN operations o ON o.id = se.operation_id "
            "JOIN jobs j ON j.id = o.job_id "
            "LEFT JOIN workers w ON w.id = se.worker_id "
            "WHERE se.instance_id = :iid "
            "ORDER BY m.name ASC, se.start_time ASC, j.name ASC, "
            "         o.sequence_number ASC"
        ), {"iid": instance_id}).mappings().all()
        return [dict(e) for e in entries]


def _render_diff_animation(instance_name: str, before_entries: list[dict]) -> None:
    """Render the before → after schedule diff animation after a COMMIT."""
    import time as _time

    import streamlit as st

    from coe.dashboard.diff import schedule_frames

    after_entries = _fetch_active_entries(instance_name)
    frames = schedule_frames(before_entries, after_entries)
    if not frames:
        return

    st.subheader("Schedule Transition")
    placeholder = st.empty()
    for fig in frames:
        placeholder.plotly_chart(fig, use_container_width=True)
        if len(frames) > 1:
            _time.sleep(1.2)


def _outcome_text(status: str, state) -> str:
    parts = [f"Recovery outcome: **{status}**"]

    solution = state.solution
    if solution:
        parts.append(
            f"Makespan {solution.get('makespan', '—')} · "
            f"Tardiness {solution.get('total_tardiness', '—')} · "
            f"Solver {solution.get('status', '—')}"
        )

    if status == "UNKNOWN":
        parts.append(
            "UNKNOWN = solver budget-exhausted (not a material conflict)."
        )

    if status == "COMMITTED" and state.explanation:
        parts.append(f"\n\n{state.explanation}")

    return "\n\n".join(parts)
