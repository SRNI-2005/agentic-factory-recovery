"""Analytics & Compare page — before/after comparison, KPI deltas, efficiency metrics."""
from __future__ import annotations


def render() -> None:
    import pandas as pd
    import plotly.graph_objects as go
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import (
        active_schedule,
        jobs_overview,
        machines_overview,
        recovery_runs,
        schedule_versions,
        workers_overview,
    )
    from coe.dashboard.theme import (
        ACCENT_AMBER,
        ACCENT_BLUE,
        ACCENT_CYAN,
        ACCENT_EMERALD,
        ACCENT_ROSE,
        apply_plotly_theme,
        glass_card,
        kpi_card,
    )
    from coe.db.session import session_scope

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM instances WHERE name = :n"), {"n": instance_name}
        ).mappings().first()
        if row is None:
            st.error(f"Instance **{instance_name}** not found.")
            st.stop()
        iid: int = row["id"]
        sched = active_schedule(session, iid)
        versions = schedule_versions(session, iid)
        machines = machines_overview(session, iid)
        workers = workers_overview(session, iid)
        jobs = jobs_overview(session, iid)
        runs = recovery_runs(session, iid)

    st.markdown("# 📈 Analytics & Comparison")
    st.caption("Deep-dive into schedule performance, version comparison, and efficiency metrics")

    if not sched:
        st.info("No active schedule to analyze. Generate one first.")
        st.stop()

    ver = sched["version"]
    entries = sched["entries"]

    # ---- Version comparison ----
    st.markdown("## 📋 Version-to-Version Comparison")

    if len(versions) >= 2:
        vc1, vc2 = st.columns(2)
        ver_labels = [f"v{v['version_number']} ({v['schedule_type']})" for v in versions]
        with vc1:
            sel_v1 = st.selectbox("Compare FROM", ver_labels, index=min(1, len(ver_labels) - 1),
                                  key="cmp_v1")
        with vc2:
            sel_v2 = st.selectbox("Compare TO", ver_labels, index=0, key="cmp_v2")

        v1_idx = ver_labels.index(sel_v1)
        v2_idx = ver_labels.index(sel_v2)
        v1 = versions[v1_idx]
        v2 = versions[v2_idx]

        # Delta KPIs
        mk_delta = v2["makespan"] - v1["makespan"]
        tard_delta = v2["total_tardiness"] - v1["total_tardiness"]

        d1, d2, d3, d4 = st.columns(4)
        d1.metric(f"Makespan (v{v1['version_number']})", v1["makespan"])
        d2.metric(f"Makespan (v{v2['version_number']})", v2["makespan"],
                  delta=mk_delta, delta_color="inverse")
        d3.metric(f"Tardiness (v{v1['version_number']})", v1["total_tardiness"])
        d4.metric(f"Tardiness (v{v2['version_number']})", v2["total_tardiness"],
                  delta=tard_delta, delta_color="inverse")

        # Version trend chart
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### 📊 Makespan & Tardiness Across Versions")

        sorted_versions = sorted(versions, key=lambda v: v["version_number"])
        fig_trend = go.Figure()
        fig_trend.add_trace(go.Scatter(
            x=[f"v{v['version_number']}" for v in sorted_versions],
            y=[v["makespan"] for v in sorted_versions],
            name="Makespan",
            mode="lines+markers",
            marker=dict(color=ACCENT_BLUE, size=10),
            line=dict(color=ACCENT_BLUE, width=2),
        ))
        fig_trend.add_trace(go.Scatter(
            x=[f"v{v['version_number']}" for v in sorted_versions],
            y=[v["total_tardiness"] for v in sorted_versions],
            name="Tardiness",
            mode="lines+markers",
            marker=dict(color=ACCENT_ROSE, size=10),
            line=dict(color=ACCENT_ROSE, width=2, dash="dash"),
            yaxis="y2",
        ))
        fig_trend.update_layout(
            title="Schedule Quality Trend",
            xaxis_title="Version",
            yaxis=dict(title="Makespan (min)", titlefont=dict(color=ACCENT_BLUE)),
            yaxis2=dict(title="Tardiness", titlefont=dict(color=ACCENT_ROSE),
                        overlaying="y", side="right"),
            height=380,
        )
        apply_plotly_theme(fig_trend)
        st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.info("Need at least 2 schedule versions for comparison. Run a recovery to create a new version.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Machine utilization analysis ----
    st.markdown("## 🔧 Machine Utilization Deep Dive")

    _v = lambda obj, k: obj.get(k) if isinstance(obj, dict) else getattr(obj, k)

    if entries:
        machine_stats = {}
        makespan = int(ver.get("makespan", 1))

        for e in entries:
            m = e.get("machine_name") or "?"
            dur = max(0, int(e["end_time"]) - int(e["start_time"]))
            machine_stats.setdefault(m, {"busy": 0, "ops": 0, "jobs": set()})
            machine_stats[m]["busy"] += dur
            machine_stats[m]["ops"] += 1
            job = e.get("job_name", "?")
            machine_stats[m]["jobs"].add(job)

        # Utilization radar chart
        m_names = sorted(machine_stats.keys())
        utilizations = [(machine_stats[m]["busy"] / makespan * 100) if makespan > 0 else 0
                        for m in m_names]

        fig_radar = go.Figure()
        fig_radar.add_trace(go.Scatterpolar(
            r=utilizations + [utilizations[0]] if utilizations else [],
            theta=m_names + [m_names[0]] if m_names else [],
            fill="toself",
            fillcolor="rgba(99,102,241,0.15)",
            line=dict(color=ACCENT_BLUE, width=2),
            marker=dict(color=ACCENT_CYAN, size=8),
            name="Utilization %",
        ))
        fig_radar.update_layout(
            polar=dict(
                bgcolor="rgba(17,22,56,0.4)",
                radialaxis=dict(visible=True, range=[0, 100],
                                gridcolor="rgba(99,130,255,0.12)",
                                tickfont=dict(color="#94a3b8")),
                angularaxis=dict(gridcolor="rgba(99,130,255,0.12)",
                                 tickfont=dict(color="#e2e8f0")),
            ),
            title="Machine Utilization Radar",
            height=450,
            showlegend=False,
        )
        apply_plotly_theme(fig_radar)
        st.plotly_chart(fig_radar, use_container_width=True)

        # Average utilization
        avg_util = sum(utilizations) / len(utilizations) if utilizations else 0
        max_util = max(utilizations) if utilizations else 0
        min_util = min(utilizations) if utilizations else 0

        uc1, uc2, uc3 = st.columns(3)
        uc1.metric("Average Utilization", f"{avg_util:.1f}%")
        uc2.metric("Max Utilization", f"{max_util:.1f}%")
        uc3.metric("Min Utilization", f"{min_util:.1f}%")

        # Bottleneck identification
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### 🔍 Bottleneck Analysis")

        bottleneck_data = []
        for m_name in m_names:
            stats = machine_stats[m_name]
            util = (stats["busy"] / makespan * 100) if makespan > 0 else 0
            bottleneck_data.append({
                "Machine": m_name,
                "Utilization %": round(util, 1),
                "Busy Time": stats["busy"],
                "Operations": stats["ops"],
                "Unique Jobs": len(stats["jobs"]),
                "Avg Op Duration": round(stats["busy"] / stats["ops"], 1) if stats["ops"] > 0 else 0,
            })

        bottleneck_data.sort(key=lambda x: x["Utilization %"], reverse=True)
        st.dataframe(bottleneck_data, use_container_width=True, hide_index=True)

    # ---- Recovery run analytics ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("## 🔄 Recovery Run Analytics")

    if runs:
        committed = sum(1 for r in runs if r.get("status") == "COMMITTED")
        total = len(runs)
        success_rate = (committed / total * 100) if total > 0 else 0

        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric("Total Runs", total)
        rc2.metric("Committed", committed)
        rc3.metric("Failed", total - committed)
        rc4.metric("Success Rate", f"{success_rate:.0f}%")

        # Run status distribution
        run_statuses = {}
        for r in runs:
            s = r.get("status", "UNKNOWN")
            run_statuses[s] = run_statuses.get(s, 0) + 1

        status_colors = {
            "COMMITTED": ACCENT_EMERALD,
            "SOLVE_INFEASIBLE": ACCENT_ROSE,
            "GATE_FAILED": ACCENT_AMBER,
            "TRANSLATION_FAILED": "#8b5cf6",
            "VERIFIER_ROLLBACK": "#ec4899",
            "STREAMING_ERROR": "#f97316",
        }

        fig_status = go.Figure(data=[go.Pie(
            labels=list(run_statuses.keys()),
            values=list(run_statuses.values()),
            hole=0.65,
            marker=dict(colors=[status_colors.get(s, ACCENT_BLUE) for s in run_statuses.keys()]),
            textinfo="label+value",
            textfont=dict(color="white"),
        )])
        fig_status.update_layout(
            title="Recovery Run Outcome Distribution",
            height=350,
        )
        apply_plotly_theme(fig_status)
        st.plotly_chart(fig_status, use_container_width=True)

        # Run timeline
        st.markdown("### ⏱️ Recovery Run Timeline")
        run_timeline = []
        for r in sorted(runs, key=lambda x: x.get("started_at") or 0):
            started = r.get("started_at")
            finished = r.get("finished_at")
            if started and finished:
                duration = finished - started
                run_timeline.append({
                    "Run": f"#{r['id']}",
                    "Status": r.get("status", "?"),
                    "Duration (s)": round(duration, 1) if isinstance(duration, float) else "—",
                    "Trigger": r.get("trigger", "?"),
                })

        if run_timeline:
            st.dataframe(run_timeline, use_container_width=True, hide_index=True)
    else:
        st.info("No recovery runs recorded yet. Use the Recovery Pipeline to create one.")

    # ---- Resource health summary ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("## 🏥 Resource Health Summary")

    machines_ok = sum(1 for m in machines if _v(m, "status") == "OPERATIONAL")
    workers_avail = sum(1 for w in workers if _v(w, "absent_since") is None)

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Machines Online", f"{machines_ok}/{len(machines)}")
    h2.metric("Workers Available", f"{workers_avail}/{len(workers)}")
    h3.metric("Active Jobs", sum(1 for j in jobs if _v(j, "status") == "ACTIVE"))
    h4.metric("Blocked Jobs", sum(1 for j in jobs if _v(j, "status") == "BLOCKED"))
