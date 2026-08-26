"""Recovery run history inspector."""
from __future__ import annotations


def render() -> None:
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import recovery_runs
    from coe.db.session import session_scope

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM instances WHERE name = :n"),
            {"n": instance_name},
        ).mappings().first()
        if row is None:
            st.error(f"Instance **{instance_name}** not found.")
            st.stop()
        instance_id: int = row["id"]
        runs = recovery_runs(session, instance_id)

    if not runs:
        st.info("No recovery runs yet.")
        return

    for r in runs:
        status = r["status"]
        is_committed = status == "COMMITTED"
        marker = "🟢" if is_committed else "🔴"
        started = r["started_at"]
        ts = f"{started:%m-%d %H:%M}" if started else "—"
        header = f"{marker} Run #{r['id']} · {r['trigger']} · {status} · {ts}"

        with st.expander(header, expanded=False):
            st.subheader("Disruption record")
            st.json(r["disruption_record_json"] or {})

            _render_timings(r["node_timings_json"])

            if r["quantum_shadow_json"]:
                st.subheader("Quantum shadow")
                st.json(r["quantum_shadow_json"])


def _render_timings(node_timings_json) -> None:
    """Render per-node wall-clock bar chart from node_timings_json.

    Handles two persisted shapes:
    - dict: {node_name: seconds_float}  (current test-fixture shape)
    - list[dict]: [{node, started_at, ended_at}, ...]  (spec §5 target)
    """
    if not node_timings_json:
        return

    import plotly.express as px
    import streamlit as st

    pairs: list[dict] = []

    if isinstance(node_timings_json, dict):
        for node, seconds in node_timings_json.items():
            if isinstance(seconds, (int, float)):
                pairs.append({"node": node, "seconds": round(float(seconds), 3)})

    elif isinstance(node_timings_json, list):
        for entry in node_timings_json:
            if isinstance(entry, dict) and "node" in entry:
                s = entry.get("started_at")
                e = entry.get("ended_at")
                if isinstance(s, (int, float)) and isinstance(e, (int, float)):
                    pairs.append({"node": entry["node"],
                                  "seconds": round(e - s, 3)})

    if pairs:
        st.subheader("Per-node wall-clock")
        st.plotly_chart(
            px.bar(pairs, x="node", y="seconds", title="Per-node wall-clock"),
            use_container_width=True,
        )
