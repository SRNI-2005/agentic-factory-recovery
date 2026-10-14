"""Overview page — system architecture, KPIs, and project introduction."""
from __future__ import annotations


def render() -> None:
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import (
        active_schedule,
        jobs_overview,
        machines_overview,
        materials_overview,
        workers_overview,
        recovery_runs,
    )
    from coe.dashboard.theme import (
        ACCENT_AMBER,
        ACCENT_BLUE,
        ACCENT_CYAN,
        ACCENT_EMERALD,
        ACCENT_ROSE,
        glass_card,
        kpi_card,
    )
    from coe.db.session import session_scope

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    # ---- fetch data ----
    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM instances WHERE name = :n"), {"n": instance_name}
        ).mappings().first()
        if row is None:
            st.error(f"Instance **{instance_name}** not found.")
            st.stop()
        iid: int = row["id"]
        sched = active_schedule(session, iid)
        machines = machines_overview(session, iid)
        workers = workers_overview(session, iid)
        materials = materials_overview(session, iid)
        jobs = jobs_overview(session, iid)
        runs = recovery_runs(session, iid)

    # ---- hero ----
    st.markdown("""
    <div style="text-align: center; padding: 2rem 0 1rem 0; animation: slideUp 0.6s ease;">
        <div style="font-size: 3.5rem; margin-bottom: 0.5rem;">🏭</div>
        <h1 style="font-size: 2.8rem !important; margin-bottom: 0.3rem;">
            Agentic Factory Recovery System
        </h1>
        <p style="color: #94a3b8; font-size: 1.1rem; max-width: 700px; margin: 0 auto;
           line-height: 1.6;">
           LLM-agent middleware for event-driven Flexible Job Shop Scheduling recovery.
           Agents own semantics — deterministic CP-SAT solver owns the math.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ---- KPI row ----
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(kpi_card(str(len(machines)), "Machines", ACCENT_BLUE), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card(str(len(workers)), "Workers", ACCENT_CYAN), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card(str(len(jobs)), "Jobs", ACCENT_EMERALD), unsafe_allow_html=True)
    with c4:
        total_ops = sum(j.get("ops", 0) if isinstance(j, dict) else j.ops for j in jobs)
        st.markdown(kpi_card(str(total_ops), "Operations", ACCENT_AMBER), unsafe_allow_html=True)
    with c5:
        st.markdown(kpi_card(str(len(materials)), "Materials", ACCENT_ROSE), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- schedule status ----
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown("## 📋 Current Schedule Status")
        if sched:
            ver = sched["version"]
            s_status = ver.get("solver_status", ver.get("solver_status", "—"))
            s_makespan = ver.get("makespan", "—")
            s_tard = ver.get("total_tardiness", "—")
            s_ver = ver.get("version_number", "—")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Version", f"v{s_ver}")
            m2.metric("Solver Status", s_status)
            m3.metric("Makespan (min)", s_makespan)
            m4.metric("Total Tardiness", s_tard)

            n_entries = len(sched.get("entries", []))
            st.caption(f"Schedule contains **{n_entries}** scheduled operations across "
                       f"**{len(machines)}** machines.")
        else:
            st.info("No active schedule yet. Run: `uv run python -m coe.cli solve baseline`")

    with col_right:
        st.markdown("## 🔄 Recovery Runs")
        committed = sum(1 for r in runs if r.get("status") == "COMMITTED")
        failed = len(runs) - committed
        st.markdown(kpi_card(str(len(runs)), "Total Runs", ACCENT_BLUE), unsafe_allow_html=True)
        if runs:
            rc1, rc2 = st.columns(2)
            rc1.metric("✅ Committed", committed)
            rc2.metric("❌ Failed", failed)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- architecture diagram ----
    st.markdown("## 🏗️ System Architecture")
    st.markdown("""
    <div class="glass-card">
        <div style="text-align: center; padding: 1rem;">
            <div style="color: #94a3b8; font-size: 0.8rem; text-transform: uppercase;
                 letter-spacing: 0.1em; margin-bottom: 1rem;">
                 Recovery Pipeline Flow
            </div>
            <div class="arch-flow">
                <div class="arch-node" style="border-color: rgba(99,102,241,0.4);">
                    🗣️ Narrative<br><small style="color:#94a3b8;">User describes disruption</small>
                </div>
                <span class="arch-arrow">→</span>
                <div class="arch-node" style="border-color: rgba(34,211,238,0.4);">
                    🔄 Translate<br><small style="color:#94a3b8;">LLM parses to structured</small>
                </div>
                <span class="arch-arrow">→</span>
                <div class="arch-node" style="border-color: rgba(16,185,129,0.4);">
                    🔍 Investigate<br><small style="color:#94a3b8;">4 specialist agents</small>
                </div>
                <span class="arch-arrow">→</span>
                <div class="arch-node" style="border-color: rgba(245,158,11,0.4);">
                    📋 Strategy<br><small style="color:#94a3b8;">LLM proposes approach</small>
                </div>
                <span class="arch-arrow">→</span>
                <div class="arch-node" style="border-color: rgba(139,92,246,0.4);">
                    ⚙️ CP-SAT Solve<br><small style="color:#94a3b8;">OR-Tools optimizer</small>
                </div>
                <span class="arch-arrow">→</span>
                <div class="arch-node" style="border-color: rgba(236,72,153,0.4);">
                    ✅ Gate & Commit<br><small style="color:#94a3b8;">Validate & persist</small>
                </div>
                <span class="arch-arrow">→</span>
                <div class="arch-node" style="border-color: rgba(99,102,241,0.4);">
                    📝 Explain<br><small style="color:#94a3b8;">LLM summarizes changes</small>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- tech stack ----
    st.markdown("## 🛠️ Technology Stack")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.markdown(glass_card("""
            <div style="text-align:center;">
                <div style="font-size:2rem; margin-bottom:0.5rem;">🧠</div>
                <div style="font-weight:600; color:#e2e8f0; margin-bottom:0.3rem;">LLM Agents</div>
                <div style="color:#94a3b8; font-size:0.82rem;">
                    LangGraph pipeline<br>
                    GPT-4 / Gemini<br>
                    Translate · Strategy · Explain
                </div>
            </div>
        """), unsafe_allow_html=True)
    with t2:
        st.markdown(glass_card("""
            <div style="text-align:center;">
                <div style="font-size:2rem; margin-bottom:0.5rem;">⚙️</div>
                <div style="font-weight:600; color:#e2e8f0; margin-bottom:0.3rem;">CP-SAT Solver</div>
                <div style="color:#94a3b8; font-size:0.82rem;">
                    Google OR-Tools<br>
                    Constraint Programming<br>
                    OPTIMAL / FEASIBLE proofs
                </div>
            </div>
        """), unsafe_allow_html=True)
    with t3:
        st.markdown(glass_card("""
            <div style="text-align:center;">
                <div style="font-size:2rem; margin-bottom:0.5rem;">🗄️</div>
                <div style="font-weight:600; color:#e2e8f0; margin-bottom:0.3rem;">TimescaleDB</div>
                <div style="color:#94a3b8; font-size:0.82rem;">
                    PostgreSQL backbone<br>
                    Hypertable events<br>
                    Versioned schedules
                </div>
            </div>
        """), unsafe_allow_html=True)
    with t4:
        st.markdown(glass_card("""
            <div style="text-align:center;">
                <div style="font-size:2rem; margin-bottom:0.5rem;">📡</div>
                <div style="font-weight:600; color:#e2e8f0; margin-bottom:0.3rem;">MQTT</div>
                <div style="color:#94a3b8; font-size:0.82rem;">
                    Mosquitto broker<br>
                    Real-time events<br>
                    Machine / Worker / Material
                </div>
            </div>
        """), unsafe_allow_html=True)

    # ---- resource health ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("## 📊 Resource Health Overview")

    machines_ok = sum(1 for m in machines
                      if (m.get("status") if isinstance(m, dict) else m.status) == "OPERATIONAL")
    machines_down = len(machines) - machines_ok
    workers_avail = sum(
        1 for w in workers
        if (w.get("absent_since") if isinstance(w, dict) else w.absent_since) is None
    )
    workers_absent = len(workers) - workers_avail

    import plotly.graph_objects as go
    from coe.dashboard.theme import apply_plotly_theme

    fig_machines = go.Figure(data=[go.Pie(
        labels=["Operational", "Down"],
        values=[machines_ok, machines_down],
        hole=0.65,
        marker=dict(colors=["#10b981", "#f43f5e"]),
        textinfo="value",
        textfont=dict(size=16, color="white"),
    )])
    fig_machines.update_layout(
        title=dict(text="Machines", font=dict(size=16)),
        showlegend=True,
        legend=dict(font=dict(color="#94a3b8")),
        height=280,
    )
    apply_plotly_theme(fig_machines)

    fig_workers = go.Figure(data=[go.Pie(
        labels=["Available", "Absent"],
        values=[workers_avail, workers_absent],
        hole=0.65,
        marker=dict(colors=["#22d3ee", "#f59e0b"]),
        textinfo="value",
        textfont=dict(size=16, color="white"),
    )])
    fig_workers.update_layout(
        title=dict(text="Workers", font=dict(size=16)),
        showlegend=True,
        legend=dict(font=dict(color="#94a3b8")),
        height=280,
    )
    apply_plotly_theme(fig_workers)

    ch1, ch2 = st.columns(2)
    with ch1:
        st.plotly_chart(fig_machines, use_container_width=True)
    with ch2:
        st.plotly_chart(fig_workers, use_container_width=True)

    # ---- how to use guide ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("## 🚀 How to Use This Dashboard")
    st.markdown("""
    <div class="glass-card">
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; text-align: center;">
            <div>
                <div style="font-size: 1.8rem; margin-bottom: 0.4rem;">1️⃣</div>
                <div style="font-weight: 600; color: #e2e8f0; margin-bottom: 0.3rem;">
                    Explore Dataset
                </div>
                <div style="color: #94a3b8; font-size: 0.82rem;">
                    Review machines, workers, jobs & materials
                </div>
            </div>
            <div>
                <div style="font-size: 1.8rem; margin-bottom: 0.4rem;">2️⃣</div>
                <div style="font-weight: 600; color: #e2e8f0; margin-bottom: 0.3rem;">
                    View Baseline
                </div>
                <div style="color: #94a3b8; font-size: 0.82rem;">
                    See the initial optimal schedule & Gantt chart
                </div>
            </div>
            <div>
                <div style="font-size: 1.8rem; margin-bottom: 0.4rem;">3️⃣</div>
                <div style="font-weight: 600; color: #e2e8f0; margin-bottom: 0.3rem;">
                    Create Disruption
                </div>
                <div style="color: #94a3b8; font-size: 0.82rem;">
                    Simulate machine failure, worker absence, or shortage
                </div>
            </div>
            <div>
                <div style="font-size: 1.8rem; margin-bottom: 0.4rem;">4️⃣</div>
                <div style="font-weight: 600; color: #e2e8f0; margin-bottom: 0.3rem;">
                    Watch Recovery
                </div>
                <div style="color: #94a3b8; font-size: 0.82rem;">
                    LLM agents analyze & CP-SAT reschedules live
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
