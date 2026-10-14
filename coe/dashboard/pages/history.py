"""Run History page — recovery run timeline with detailed inspection."""
from __future__ import annotations


def render() -> None:
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import recovery_runs
    from coe.dashboard.theme import (
        ACCENT_AMBER,
        ACCENT_BLUE,
        ACCENT_CYAN,
        ACCENT_EMERALD,
        ACCENT_ROSE,
        apply_plotly_theme,
        glass_card,
        kpi_card,
        timeline_item_html,
    )
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
        iid: int = row["id"]
        runs = recovery_runs(session, iid)

    st.markdown("# 📋 Recovery Run History")
    st.caption(f"Instance: **{instance_name}** — Complete recovery audit trail")

    if not runs:
        st.markdown(glass_card("""
            <div style="text-align: center; padding: 2rem;">
                <div style="font-size: 3rem; margin-bottom: 1rem;">📋</div>
                <div style="font-weight: 600; color: #e2e8f0; font-size: 1.2rem;">
                    No Recovery Runs Yet
                </div>
                <div style="color: #94a3b8; margin-top: 0.5rem;">
                    Run a recovery from the Recovery Pipeline page to see results here.
                </div>
            </div>
        """), unsafe_allow_html=True)
        return

    # ---- summary KPIs ----
    committed = sum(1 for r in runs if r.get("status") == "COMMITTED")
    failed = len(runs) - committed
    success_rate = (committed / len(runs) * 100) if runs else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card(str(len(runs)), "Total Runs", ACCENT_BLUE), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card(str(committed), "Committed", ACCENT_EMERALD), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card(str(failed), "Failed", ACCENT_ROSE), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card(f"{success_rate:.0f}%", "Success Rate", ACCENT_CYAN), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- timeline view ----
    st.markdown("## ⏱️ Run Timeline")

    timeline_html = ""
    for r in runs:
        status = r["status"]
        is_committed = status == "COMMITTED"
        started = r["started_at"]
        finished = r.get("finished_at")

        ts = f"{started:%Y-%m-%d %H:%M:%S}" if started else "—"
        duration = ""
        if started and finished:
            dur = finished - started
            if isinstance(dur, (int, float)):
                duration = f" · {dur:.1f}s"
            else:
                total_seconds = dur.total_seconds()
                duration = f" · {total_seconds:.1f}s"

        marker = "🟢" if is_committed else "🔴"
        tl_status = "success" if is_committed else "failure"
        content = (
            f"<strong>{marker} Run #{r['id']}</strong> · "
            f"<span style='color: #94a3b8;'>{r['trigger']} · {status}{duration}</span>"
        )
        timeline_html += timeline_item_html(ts, content, tl_status)

    st.markdown(f"""
    <div class="glass-card" style="max-height: 400px; overflow-y: auto;">
        {timeline_html}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- detailed run inspector ----
    st.markdown("## 🔍 Run Inspector")

    for r in runs:
        status = r["status"]
        is_committed = status == "COMMITTED"
        marker = "🟢" if is_committed else "🔴"
        started = r["started_at"]
        ts = f"{started:%m-%d %H:%M}" if started else "—"
        header = f"{marker} Run #{r['id']} · {r['trigger']} · {status} · {ts}"

        border_color = ("rgba(16,185,129,0.4)" if is_committed
                        else "rgba(244,63,94,0.4)")

        with st.expander(header, expanded=False):
            # Disruption record
            st.markdown("### 📋 Disruption Record")
            if r["disruption_record_json"]:
                st.json(r["disruption_record_json"])
            else:
                st.caption("No disruption record available.")

            # Node timings
            _render_timings(r["node_timings_json"])

            # Quantum shadow
            if r["quantum_shadow_json"]:
                st.markdown("### ⚛️ Quantum Shadow")
                st.json(r["quantum_shadow_json"])

    # ---- per-node timing analysis ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("## ⏱️ Pipeline Timing Analysis")

    all_timings = {}
    for r in runs:
        if r["node_timings_json"] and isinstance(r["node_timings_json"], dict):
            for node, seconds in r["node_timings_json"].items():
                if isinstance(seconds, (int, float)):
                    all_timings.setdefault(node, []).append(float(seconds))

    if all_timings:
        # Average time per node
        avg_data = []
        for node, times in sorted(all_timings.items()):
            avg_data.append({
                "Node": node,
                "Avg Time (s)": round(sum(times) / len(times), 3),
                "Max Time (s)": round(max(times), 3),
                "Min Time (s)": round(min(times), 3),
                "Runs": len(times),
            })

        fig_avg = go.Figure(data=[go.Bar(
            x=[d["Node"] for d in avg_data],
            y=[d["Avg Time (s)"] for d in avg_data],
            marker=dict(
                color=[d["Avg Time (s)"] for d in avg_data],
                colorscale=[[0, ACCENT_EMERALD], [0.5, ACCENT_AMBER], [1, ACCENT_ROSE]],
                line=dict(width=0),
            ),
            text=[f"{d['Avg Time (s)']:.2f}s" for d in avg_data],
            textposition="auto",
            textfont=dict(color="white"),
        )])
        fig_avg.update_layout(
            title="Average Wall-Clock Time per Pipeline Node",
            xaxis_title="Node",
            yaxis_title="Time (seconds)",
            height=380,
        )
        apply_plotly_theme(fig_avg)
        st.plotly_chart(fig_avg, use_container_width=True)

        st.dataframe(avg_data, use_container_width=True, hide_index=True)
    else:
        st.info("No timing data available from recovery runs.")


def _render_timings(node_timings_json) -> None:
    """Render per-node wall-clock bar chart."""
    if not node_timings_json:
        return

    import plotly.graph_objects as go
    import streamlit as st

    from coe.dashboard.theme import ACCENT_BLUE, ACCENT_CYAN, apply_plotly_theme

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
                    pairs.append({"node": entry["node"], "seconds": round(e - s, 3)})

    if pairs:
        st.markdown("### ⏱️ Per-node Wall-clock")
        fig = go.Figure(data=[go.Bar(
            x=[p["node"] for p in pairs],
            y=[p["seconds"] for p in pairs],
            marker=dict(
                color=[p["seconds"] for p in pairs],
                colorscale=[[0, ACCENT_BLUE], [1, ACCENT_CYAN]],
                line=dict(width=0),
            ),
            text=[f"{p['seconds']:.2f}s" for p in pairs],
            textposition="auto",
            textfont=dict(color="white"),
        )])
        fig.update_layout(
            title="Per-node Wall-clock Timing",
            xaxis_title="Node",
            yaxis_title="Seconds",
            height=320,
        )
        apply_plotly_theme(fig)
        st.plotly_chart(fig, use_container_width=True)
