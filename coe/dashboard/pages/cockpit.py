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
