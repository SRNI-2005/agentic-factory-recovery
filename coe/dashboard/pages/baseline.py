"""Baseline Schedule page — interactive Gantt, machine utilization analytics, worker assignments."""
from __future__ import annotations


def render() -> None:
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import (
        active_schedule,
        jobs_overview,
        jobs_per_day,
        machines_overview,
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
        per_day = jobs_per_day(session, iid)

    st.markdown("# ⚙️ Baseline Schedule")
    st.caption(f"Instance: **{instance_name}** — CP-SAT optimized production schedule")

    if sched is None:
        st.markdown(glass_card("""
            <div style="text-align: center; padding: 2rem;">
                <div style="font-size: 3rem; margin-bottom: 1rem;">📋</div>
                <div style="font-weight: 600; color: #e2e8f0; font-size: 1.2rem;">
                    No Active Schedule
                </div>
                <div style="color: #94a3b8; margin-top: 0.5rem;">
                    Generate a baseline schedule to see the Gantt chart and analytics.<br>
                    <code style="background: rgba(99,102,241,0.15); padding: 4px 8px; border-radius: 6px;
                           color: #22d3ee; font-size: 0.85rem;">
                        uv run python -m coe.cli solve baseline --instance {instance_name}
                    </code>
                </div>
            </div>
        """.replace("{instance_name}", instance_name)), unsafe_allow_html=True)
        st.stop()

    ver = sched["version"]
    entries = sched["entries"]

    # ---- schedule KPIs ----
    v_status = ver.get("solver_status", "—")
    v_makespan = ver.get("makespan", "—")
    v_tard = ver.get("total_tardiness", "—")
    v_num = ver.get("version_number", "—")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(kpi_card(f"v{v_num}", "Version", ACCENT_BLUE), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card(str(v_status), "Solver Status", ACCENT_CYAN), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card(str(v_makespan), "Makespan (min)", ACCENT_EMERALD), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card(str(v_tard), "Tardiness", ACCENT_AMBER), unsafe_allow_html=True)
    with c5:
        st.markdown(kpi_card(str(len(entries)), "Entries", ACCENT_ROSE), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    if not entries:
        st.info("Schedule is empty — no entries to display.")
        return

    # ---- Gantt chart ----
    tab_gantt, tab_util, tab_worker, tab_timeline, tab_versions = st.tabs([
        "📊 Gantt Chart", "🔧 Machine Utilization", "👷 Worker Assignments",
        "📅 Job Timeline", "📋 Version History",
    ])

    with tab_gantt:
        st.markdown("## 📊 Interactive Gantt Chart")

        # Build data
        gantt_rows = []
        for e in entries:
            start = int(e["start_time"])
            end = int(e["end_time"])
            if end <= start:
                end = start + 1
            job = e.get("job_name") or e.get("job_id", "?")
            op = e.get("sequence_number", "")
            label = f"{job}/op{op}"
            machine = e.get("machine_name") or e.get("machine_id", "?")
            worker = e.get("worker_name") or "—"
            gantt_rows.append(dict(
                Machine=machine, Task=label, Worker=worker, Job=job,
                Start=pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=start),
                Finish=pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=end),
                Duration=end - start,
            ))

        df = pd.DataFrame(gantt_rows)

        # Filter options
        with st.expander("🔍 Filter Options", expanded=False):
            fc1, fc2 = st.columns(2)
            with fc1:
                sel_machines = st.multiselect(
                    "Filter by Machine",
                    sorted(df["Machine"].unique()),
                    default=sorted(df["Machine"].unique()),
                    key="gantt_filter_machine",
                )
            with fc2:
                sel_jobs = st.multiselect(
                    "Filter by Job",
                    sorted(df["Job"].unique()),
                    default=sorted(df["Job"].unique()),
                    key="gantt_filter_job",
                )
        df_filtered = df[df["Machine"].isin(sel_machines) & df["Job"].isin(sel_jobs)] if (sel_machines and sel_jobs) else df

        # Gantt
        fig = px.timeline(
            df_filtered, x_start="Start", x_end="Finish", y="Machine",
            color="Job", hover_data=["Worker", "Duration", "Task"],
            title="Production Schedule — Gantt Chart",
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(
            height=max(400, len(df_filtered["Machine"].unique()) * 50 + 120),
            legend=dict(font=dict(size=10)),
        )
        apply_plotly_theme(fig)
        st.plotly_chart(fig, use_container_width=True)

        # Stats under gantt
        st.markdown("### 📊 Schedule Statistics")
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Total Operations", len(entries))
        sc2.metric("Unique Machines", len(df["Machine"].unique()))
        sc3.metric("Unique Jobs", len(df["Job"].unique()))
        sc4.metric("Avg Duration", f"{df['Duration'].mean():.1f} min")

    # ---- Machine utilization ----
    with tab_util:
        st.markdown("## 🔧 Machine Utilization Analysis")

        machine_util = {}
        for _, row_data in df.iterrows():
            m = row_data["Machine"]
            machine_util.setdefault(m, {"busy": 0, "ops": 0})
            machine_util[m]["busy"] += row_data["Duration"]
            machine_util[m]["ops"] += 1

        makespan = int(v_makespan) if isinstance(v_makespan, (int, float)) else 1

        util_data = []
        for m_name in sorted(machine_util.keys()):
            data = machine_util[m_name]
            util_pct = (data["busy"] / makespan * 100) if makespan > 0 else 0
            util_data.append({
                "Machine": m_name,
                "Busy Time (min)": data["busy"],
                "Idle Time (min)": max(0, makespan - data["busy"]),
                "Utilization %": round(util_pct, 1),
                "Operations": data["ops"],
            })

        # Utilization bar chart
        fig_util = go.Figure()
        fig_util.add_trace(go.Bar(
            x=[d["Machine"] for d in util_data],
            y=[d["Utilization %"] for d in util_data],
            marker=dict(
                color=[d["Utilization %"] for d in util_data],
                colorscale=[[0, ACCENT_ROSE], [0.5, ACCENT_AMBER], [1, ACCENT_EMERALD]],
                line=dict(width=0),
            ),
            text=[f"{d['Utilization %']}%" for d in util_data],
            textposition="auto",
            textfont=dict(color="white", size=12),
        ))
        fig_util.update_layout(
            title="Machine Utilization (%)",
            xaxis_title="Machine",
            yaxis_title="Utilization %",
            yaxis=dict(range=[0, 105]),
            height=400,
        )
        apply_plotly_theme(fig_util)
        st.plotly_chart(fig_util, use_container_width=True)

        # Busy vs idle stacked
        fig_stacked = go.Figure()
        fig_stacked.add_trace(go.Bar(
            x=[d["Machine"] for d in util_data],
            y=[d["Busy Time (min)"] for d in util_data],
            name="Busy",
            marker=dict(color=ACCENT_EMERALD),
        ))
        fig_stacked.add_trace(go.Bar(
            x=[d["Machine"] for d in util_data],
            y=[d["Idle Time (min)"] for d in util_data],
            name="Idle",
            marker=dict(color="rgba(100,116,139,0.4)"),
        ))
        fig_stacked.update_layout(
            barmode="stack",
            title="Busy vs Idle Time per Machine",
            xaxis_title="Machine",
            yaxis_title="Time (min)",
            height=380,
        )
        apply_plotly_theme(fig_stacked)
        st.plotly_chart(fig_stacked, use_container_width=True)

        # Ops per machine
        fig_ops = go.Figure(data=[go.Bar(
            x=[d["Machine"] for d in util_data],
            y=[d["Operations"] for d in util_data],
            marker=dict(color=ACCENT_BLUE, line=dict(width=0)),
            text=[d["Operations"] for d in util_data],
            textposition="auto",
            textfont=dict(color="white"),
        )])
        fig_ops.update_layout(
            title="Operations Count per Machine",
            xaxis_title="Machine",
            yaxis_title="Operations",
            height=320,
        )
        apply_plotly_theme(fig_ops)
        st.plotly_chart(fig_ops, use_container_width=True)

    # ---- Worker assignments ----
    with tab_worker:
        st.markdown("## 👷 Worker Assignment Analysis")

        worker_assignments = {}
        for _, row_data in df.iterrows():
            w = row_data["Worker"]
            if w == "—":
                continue
            worker_assignments.setdefault(w, {"busy": 0, "ops": 0, "machines": set()})
            worker_assignments[w]["busy"] += row_data["Duration"]
            worker_assignments[w]["ops"] += 1
            worker_assignments[w]["machines"].add(row_data["Machine"])

        if worker_assignments:
            # Worker load chart
            w_names = sorted(worker_assignments.keys())
            w_busy = [worker_assignments[w]["busy"] for w in w_names]
            w_ops = [worker_assignments[w]["ops"] for w in w_names]

            fig_wload = go.Figure()
            fig_wload.add_trace(go.Bar(
                x=w_names, y=w_busy,
                name="Busy Time (min)",
                marker=dict(color=ACCENT_CYAN, line=dict(width=0)),
                text=w_busy,
                textposition="auto",
                textfont=dict(color="white"),
            ))
            fig_wload.update_layout(
                title="Worker Load — Busy Time (min)",
                xaxis_title="Worker",
                yaxis_title="Time (min)",
                height=380,
            )
            apply_plotly_theme(fig_wload)
            st.plotly_chart(fig_wload, use_container_width=True)

            # Worker-machine heatmap
            all_w = sorted(worker_assignments.keys())
            all_m = sorted(set(m for v in worker_assignments.values() for m in v["machines"]))
            heat_data = []
            for w_name in all_w:
                row_vals = []
                for m_name in all_m:
                    count = sum(1 for _, r in df.iterrows()
                                if r["Worker"] == w_name and r["Machine"] == m_name)
                    row_vals.append(count)
                heat_data.append(row_vals)

            fig_heat = go.Figure(data=go.Heatmap(
                z=heat_data,
                x=all_m,
                y=all_w,
                colorscale=[[0, "rgba(17,22,56,0.8)"], [0.5, ACCENT_BLUE], [1, ACCENT_CYAN]],
                text=heat_data,
                texttemplate="%{text}",
                textfont=dict(color="white"),
            ))
            fig_heat.update_layout(
                title="Worker × Machine Assignment Heatmap",
                xaxis_title="Machine",
                yaxis_title="Worker",
                height=max(300, len(all_w) * 35 + 100),
            )
            apply_plotly_theme(fig_heat)
            st.plotly_chart(fig_heat, use_container_width=True)
        else:
            st.info("No worker assignments in the current schedule.")

    # ---- Job timeline ----
    with tab_timeline:
        st.markdown("## 📅 Job Timeline")

        job_spans = {}
        for _, row_data in df.iterrows():
            job = row_data["Job"]
            start_ts = row_data["Start"]
            end_ts = row_data["Finish"]
            if job not in job_spans:
                job_spans[job] = {"start": start_ts, "finish": end_ts, "ops": 0, "total_dur": 0}
            else:
                job_spans[job]["start"] = min(job_spans[job]["start"], start_ts)
                job_spans[job]["finish"] = max(job_spans[job]["finish"], end_ts)
            job_spans[job]["ops"] += 1
            job_spans[job]["total_dur"] += row_data["Duration"]

        if job_spans:
            timeline_rows = []
            for job_name, span in sorted(job_spans.items()):
                timeline_rows.append(dict(
                    Job=job_name,
                    Start=span["start"],
                    Finish=span["finish"],
                    Operations=span["ops"],
                    TotalDuration=span["total_dur"],
                ))
            tl_df = pd.DataFrame(timeline_rows)

            fig_tl = px.timeline(
                tl_df, x_start="Start", x_end="Finish", y="Job",
                color="TotalDuration",
                hover_data=["Operations", "TotalDuration"],
                title="Job Span Timeline (earliest start → latest finish)",
                color_continuous_scale=[[0, ACCENT_BLUE], [0.5, ACCENT_CYAN], [1, ACCENT_EMERALD]],
            )
            fig_tl.update_yaxes(autorange="reversed")
            fig_tl.update_layout(
                height=max(400, len(job_spans) * 25 + 100),
            )
            apply_plotly_theme(fig_tl)
            st.plotly_chart(fig_tl, use_container_width=True)

        # Deadlines per day
        if per_day:
            st.markdown("### 📅 Job Deadlines by Day")
            days = sorted(per_day.keys())
            counts = [len(per_day[d]) for d in days]
            fig_pd = go.Figure(data=[go.Bar(
                x=[f"Day {d}" for d in days],
                y=counts,
                marker=dict(color=ACCENT_AMBER, line=dict(width=0)),
                text=counts,
                textposition="auto",
                textfont=dict(color="white"),
            )])
            fig_pd.update_layout(
                title="Deadlines by Day",
                height=300,
            )
            apply_plotly_theme(fig_pd)
            st.plotly_chart(fig_pd, use_container_width=True)

    # ---- Version history ----
    with tab_versions:
        st.markdown("## 📋 Schedule Version History")
        if versions:
            for v in versions:
                is_current = v["version_number"] == ver.get("version_number")
                border_color = "rgba(99,102,241,0.5)" if is_current else "rgba(99,130,255,0.15)"
                badge = "🟢 ACTIVE" if is_current else "⚪ Historical"
                rb = " · 🔙 Rolled back" if v["rolled_back"] else ""

                st.markdown(f"""
                <div class="glass-card" style="border-color: {border_color}; margin-bottom: 0.8rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="font-weight: 700; font-size: 1.1rem; color: #e2e8f0;">
                                Version {v['version_number']}
                            </span>
                            <span style="margin-left: 0.8rem; font-size: 0.8rem; color: #94a3b8;">
                                {v['schedule_type']} · {v['solver_status']}{rb}
                            </span>
                        </div>
                        <span style="font-size: 0.8rem; color: #94a3b8;">{badge}</span>
                    </div>
                    <div style="margin-top: 0.6rem; display: flex; gap: 2rem;">
                        <span style="color: #94a3b8; font-size: 0.85rem;">
                            Makespan: <strong style="color: #e2e8f0;">{v['makespan']}</strong>
                        </span>
                        <span style="color: #94a3b8; font-size: 0.85rem;">
                            Tardiness: <strong style="color: #e2e8f0;">{v['total_tardiness']}</strong>
                        </span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No version history available.")
