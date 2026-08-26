"""Configure page — five read-only tabs for schedule, materials, machines, workers, jobs."""
from __future__ import annotations


def render() -> None:
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import (
        active_schedule,
        jobs_overview,
        jobs_per_day,
        machines_overview,
        materials_overview,
        schedule_versions,
        workers_overview,
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
        instance_id: int = row["id"]

        schedule_data = active_schedule(session, instance_id)
        versions = schedule_versions(session, instance_id)
        materials = materials_overview(session, instance_id)
        machines = machines_overview(session, instance_id)
        workers = workers_overview(session, instance_id)
        jobs = jobs_overview(session, instance_id)
        per_day = jobs_per_day(session, instance_id)

    tab_sched, tab_mat, tab_mach, tab_work, tab_jobs = st.tabs(
        ["Schedule", "Materials", "Machines", "Workers", "Jobs/day"]
    )

    _render_schedule(tab_sched, schedule_data, versions)
    _render_materials(tab_mat, materials)
    _render_machines(tab_mach, machines)
    _render_workers(tab_work, workers)
    _render_jobs_day(tab_jobs, jobs, per_day)


# ------------------------------------------------------------------
# Schedule tab
# ------------------------------------------------------------------

def _render_schedule(container, schedule_data, versions):
    import plotly.express as px
    import streamlit as st

    with container:
        st.subheader("Active Schedule")

        if schedule_data is None:
            st.info("No active schedule yet. Run a solver to generate one.")
        else:
            ver = schedule_data["version"]
            entries = schedule_data["entries"]
            st.caption(
                f"Version **{ver['version_number']}** · "
                f"Status **{ver['solver_status']}** · "
                f"Makespan **{ver['makespan']}** · "
                f"Tardiness **{ver['total_tardiness']}**"
                + (" · 🔙 rolled back" if ver["rolled_back"] else "")
            )
            if not entries:
                st.info("Schedule is empty — no entries to display.")
            else:
                _render_gantt(entries)

        if versions:
            st.subheader("Version History")
            st.dataframe(
                [
                    {
                        "Version": v["version_number"],
                        "Type": v["schedule_type"],
                        "Status": v["solver_status"],
                        "Makespan": v["makespan"],
                        "Tardiness": v["total_tardiness"],
                        "Rolled back": v["rolled_back"],
                    }
                    for v in versions
                ],
                use_container_width=True,
                hide_index=True,
            )


def _render_gantt(entries):
    import plotly.express as px
    import pandas as pd

    rows = []
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
        rows.append(
            dict(
                Machine=machine,
                Task=label,
                Worker=worker,
                Start=pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=start),
                Finish=pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=end),
            )
        )
    df = pd.DataFrame(rows)
    fig = px.timeline(
        df, x_start="Start", x_end="Finish", y="Machine",
        color="Task", hover_data=["Worker"],
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=max(300, len(set(r["Machine"] for r in rows)) * 50 + 100))
    import streamlit as st
    st.plotly_chart(fig, use_container_width=True)


# ------------------------------------------------------------------
# Materials tab
# ------------------------------------------------------------------

def _render_materials(container, materials):
    import streamlit as st

    with container:
        st.subheader("Materials")
        if not materials:
            st.info("No materials configured.")
            return
        for m in materials:
            with st.expander(m["sku"], expanded=False):
                cols = st.columns(3)
                cols[0].metric("Initial stock", m["initial_stock"])
                cols[1].metric("Reorder point", m["reorder_point"] if m["reorder_point"] is not None else "—")
                cols[2].metric("Scheduled receipts", len(m["receipts"]))
                if m["receipts"]:
                    st.dataframe(
                        [
                            {"Quantity": r["quantity"], "Available at (min)": r["available_at"],
                             "Source": r["source"]}
                            for r in m["receipts"]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )


# ------------------------------------------------------------------
# Machines tab
# ------------------------------------------------------------------

def _render_machines(container, machines):
    import streamlit as st

    with container:
        st.subheader("Machines")
        if not machines:
            st.info("No machines configured.")
            return
        st.dataframe(
            [
                {
                    "Machine": m["name"],
                    "Status": m["status"],
                    "Down since (min)": m["down_since"] if m["down_since"] is not None else "—",
                }
                for m in machines
            ],
            use_container_width=True,
            hide_index=True,
        )


# ------------------------------------------------------------------
# Workers tab
# ------------------------------------------------------------------

def _render_workers(container, workers):
    import streamlit as st

    with container:
        st.subheader("Workers")
        if not workers:
            st.info("No workers configured.")
            return
        st.dataframe(
            [
                {
                    "Worker": w["name"],
                    "Role": w["role"] or "—",
                    "Availability windows": len(w["availability"]),
                    "Absent since (min)": w["absent_since"] if w["absent_since"] is not None else "—",
                }
                for w in workers
            ],
            use_container_width=True,
            hide_index=True,
        )


# ------------------------------------------------------------------
# Jobs/day tab
# ------------------------------------------------------------------

def _render_jobs_day(container, jobs, per_day):
    import plotly.express as px
    import streamlit as st

    with container:
        st.subheader("Jobs Overview")
        if jobs:
            st.dataframe(
                [
                    {
                        "Job": j["name"],
                        "Family": j["family"] or "—",
                        "Release": j["release_time"],
                        "Deadline": j["deadline"] if j["deadline"] is not None else "—",
                        "Priority": j["priority"],
                        "Status": j["status"],
                        "Ops": j["ops"],
                    }
                    for j in jobs
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No jobs configured.")

        st.subheader("Deadlines by Day")
        if not per_day:
            st.info("No jobs with deadlines.")
            return
        days = sorted(per_day.keys())
        counts = [len(per_day[d]) for d in days]
        import pandas as pd
        df = pd.DataFrame({"Day": days, "Jobs with deadline": counts})
        fig = px.bar(df, x="Day", y="Jobs with deadline")
        fig.update_layout(xaxis_title="Day", yaxis_title="Job count")
        st.plotly_chart(fig, use_container_width=True)
