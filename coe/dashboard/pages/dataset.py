"""Dataset Explorer page — rich visual cards for machines, workers, materials, jobs."""
from __future__ import annotations


def render() -> None:
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.data import (
        jobs_overview,
        machines_overview,
        materials_overview,
        workers_overview,
    )
    from coe.dashboard.theme import (
        ACCENT_AMBER,
        ACCENT_BLUE,
        ACCENT_CYAN,
        ACCENT_EMERALD,
        ACCENT_ROSE,
        apply_plotly_theme,
        kpi_card,
        resource_card_html,
        status_badge,
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
        machines = machines_overview(session, iid)
        workers = workers_overview(session, iid)
        materials = materials_overview(session, iid)
        jobs = jobs_overview(session, iid)

    st.markdown("# 📊 Dataset Explorer")
    st.caption(f"Instance: **{instance_name}** — Complete factory resource inventory")

    # ---- summary KPIs ----
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card(str(len(machines)), "Machines", ACCENT_BLUE), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card(str(len(workers)), "Workers", ACCENT_CYAN), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card(str(len(jobs)), "Jobs", ACCENT_EMERALD), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card(str(len(materials)), "Materials", ACCENT_AMBER), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- tabs ----
    tab_m, tab_w, tab_j, tab_mat = st.tabs([
        "🔧 Machines", "👷 Workers", "📦 Jobs", "🧪 Materials",
    ])

    # ---------- MACHINES ----------
    with tab_m:
        st.markdown("## 🔧 Machines")
        _v = lambda m, k: m.get(k) if isinstance(m, dict) else getattr(m, k)

        # Machine cards in grid
        cols = st.columns(4)
        for idx, m in enumerate(machines):
            name = _v(m, "name")
            status = _v(m, "status")
            down_since = _v(m, "down_since")
            details = []
            if down_since is not None:
                details.append(f"⚠️ Down since t={down_since} min")
            else:
                details.append("✅ Running normally")

            with cols[idx % 4]:
                st.markdown(resource_card_html(
                    f"🔧 {name}", status, details
                ), unsafe_allow_html=True)

        # Machine status distribution
        st.markdown("<br>", unsafe_allow_html=True)
        statuses = [_v(m, "status") for m in machines]
        status_counts = {}
        for s in statuses:
            status_counts[s] = status_counts.get(s, 0) + 1

        fig = go.Figure(data=[go.Bar(
            x=list(status_counts.keys()),
            y=list(status_counts.values()),
            marker=dict(
                color=[ACCENT_EMERALD if s == "OPERATIONAL" else ACCENT_ROSE
                       for s in status_counts.keys()],
                line=dict(width=0),
            ),
            text=list(status_counts.values()),
            textposition="auto",
            textfont=dict(color="white", size=14, family="Inter"),
        )])
        fig.update_layout(
            title="Machine Status Distribution",
            xaxis_title="Status",
            yaxis_title="Count",
            height=300,
        )
        apply_plotly_theme(fig)
        st.plotly_chart(fig, use_container_width=True)

    # ---------- WORKERS ----------
    with tab_w:
        st.markdown("## 👷 Workers")
        _wv = lambda w, k: w.get(k) if isinstance(w, dict) else getattr(w, k)

        cols = st.columns(4)
        for idx, w in enumerate(workers):
            name = _wv(w, "name")
            role = _wv(w, "role") or "General"
            absent_since = _wv(w, "absent_since")
            avail = _wv(w, "availability")
            avail_count = len(avail) if avail else 0

            status = "UNAVAILABLE" if absent_since is not None else "OPERATIONAL"
            details = [f"🎯 Role: {role}", f"📅 {avail_count} availability windows"]
            if absent_since is not None:
                details.append(f"⚠️ Absent since t={absent_since}")

            with cols[idx % 4]:
                st.markdown(resource_card_html(
                    f"👷 {name}", status, details
                ), unsafe_allow_html=True)

        # Worker availability timeline
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### Worker Availability Windows")
        w_rows = []
        import pandas as pd
        for w in workers:
            name = _wv(w, "name")
            avail = _wv(w, "availability")
            if avail:
                for a_from, a_until in avail:
                    w_rows.append(dict(
                        Worker=name,
                        Start=pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=a_from),
                        Finish=pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=a_until),
                    ))
        if w_rows:
            df = pd.DataFrame(w_rows)
            fig = px.timeline(df, x_start="Start", x_end="Finish", y="Worker",
                              color="Worker")
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(
                title="Worker Availability Timeline",
                height=max(250, len(workers) * 40 + 100),
                showlegend=False,
            )
            apply_plotly_theme(fig)
            st.plotly_chart(fig, use_container_width=True)

        # Role distribution
        roles = [_wv(w, "role") or "General" for w in workers]
        role_counts = {}
        for r in roles:
            role_counts[r] = role_counts.get(r, 0) + 1

        fig_roles = go.Figure(data=[go.Pie(
            labels=list(role_counts.keys()),
            values=list(role_counts.values()),
            hole=0.6,
            marker=dict(colors=[ACCENT_BLUE, ACCENT_CYAN, ACCENT_EMERALD,
                                ACCENT_AMBER, ACCENT_ROSE, "#8b5cf6"]),
            textinfo="label+value",
            textfont=dict(color="white"),
        )])
        fig_roles.update_layout(title="Role Distribution", height=300)
        apply_plotly_theme(fig_roles)
        st.plotly_chart(fig_roles, use_container_width=True)

    # ---------- JOBS ----------
    with tab_j:
        st.markdown("## 📦 Jobs")
        _jv = lambda j, k: j.get(k) if isinstance(j, dict) else getattr(j, k)

        # Summary stats
        total_ops = sum(_jv(j, "ops") for j in jobs)
        with_deadline = sum(1 for j in jobs if _jv(j, "deadline") is not None)
        avg_ops = total_ops / len(jobs) if jobs else 0

        kc1, kc2, kc3, kc4 = st.columns(4)
        kc1.metric("Total Jobs", len(jobs))
        kc2.metric("Total Operations", total_ops)
        kc3.metric("Avg Ops/Job", f"{avg_ops:.1f}")
        kc4.metric("With Deadline", with_deadline)

        st.markdown("<br>", unsafe_allow_html=True)

        # Jobs table with enhanced styling
        if jobs:
            job_data = []
            for j in jobs:
                job_data.append({
                    "Job": _jv(j, "name"),
                    "Family": _jv(j, "family") or "—",
                    "Release": _jv(j, "release_time"),
                    "Deadline": _jv(j, "deadline") if _jv(j, "deadline") is not None else "—",
                    "Priority": _jv(j, "priority"),
                    "Status": _jv(j, "status"),
                    "Operations": _jv(j, "ops"),
                })
            st.dataframe(job_data, use_container_width=True, hide_index=True, height=400)

        # Operations per job chart
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### Operations per Job")
        if jobs:
            job_names = [_jv(j, "name") for j in jobs]
            job_ops = [_jv(j, "ops") for j in jobs]
            fig_ops = go.Figure(data=[go.Bar(
                x=job_names, y=job_ops,
                marker=dict(
                    color=job_ops,
                    colorscale=[[0, ACCENT_BLUE], [0.5, ACCENT_CYAN], [1, ACCENT_EMERALD]],
                    line=dict(width=0),
                ),
                text=job_ops,
                textposition="auto",
                textfont=dict(color="white"),
            )])
            fig_ops.update_layout(
                title="Operations Count per Job",
                xaxis_title="Job",
                yaxis_title="Operations",
                height=350,
            )
            apply_plotly_theme(fig_ops)
            st.plotly_chart(fig_ops, use_container_width=True)

        # Priority distribution
        st.markdown("### Priority Distribution")
        if jobs:
            priorities = [_jv(j, "priority") for j in jobs]
            pri_counts = {}
            for p in priorities:
                pri_counts[p] = pri_counts.get(p, 0) + 1
            fig_pri = go.Figure(data=[go.Bar(
                x=[f"Priority {k}" for k in sorted(pri_counts.keys())],
                y=[pri_counts[k] for k in sorted(pri_counts.keys())],
                marker=dict(color=ACCENT_AMBER, line=dict(width=0)),
                text=[pri_counts[k] for k in sorted(pri_counts.keys())],
                textposition="auto",
                textfont=dict(color="white"),
            )])
            fig_pri.update_layout(
                title="Job Priority Distribution",
                height=280,
            )
            apply_plotly_theme(fig_pri)
            st.plotly_chart(fig_pri, use_container_width=True)

        # Job status distribution
        st.markdown("### Job Status Distribution")
        if jobs:
            statuses = [_jv(j, "status") for j in jobs]
            status_counts_j = {}
            for s in statuses:
                status_counts_j[s] = status_counts_j.get(s, 0) + 1
            fig_js = go.Figure(data=[go.Pie(
                labels=list(status_counts_j.keys()),
                values=list(status_counts_j.values()),
                hole=0.6,
                marker=dict(colors=[ACCENT_EMERALD, ACCENT_ROSE, ACCENT_AMBER, ACCENT_BLUE]),
                textinfo="label+percent",
                textfont=dict(color="white"),
            )])
            fig_js.update_layout(title="Job Status", height=300)
            apply_plotly_theme(fig_js)
            st.plotly_chart(fig_js, use_container_width=True)

    # ---------- MATERIALS ----------
    with tab_mat:
        st.markdown("## 🧪 Materials")
        _mv = lambda m, k: m.get(k) if isinstance(m, dict) else getattr(m, k)

        if not materials:
            st.info("No materials configured for this instance.")
            return

        cols = st.columns(3)
        for idx, m in enumerate(materials):
            sku = _mv(m, "sku")
            stock = _mv(m, "initial_stock")
            reorder = _mv(m, "reorder_point")
            receipts = _mv(m, "receipts")
            n_receipts = len(receipts) if receipts else 0

            details = [
                f"📦 Initial stock: {stock}",
                f"🔄 Reorder point: {reorder if reorder is not None else '—'}",
                f"📥 Scheduled receipts: {n_receipts}",
            ]
            status = "OPERATIONAL" if stock > 0 else "FAILED"

            with cols[idx % 3]:
                st.markdown(resource_card_html(
                    f"🧪 {sku}", status, details
                ), unsafe_allow_html=True)

        # Stock overview chart
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### Stock Levels Overview")
        skus = [_mv(m, "sku") for m in materials]
        stocks = [_mv(m, "initial_stock") for m in materials]
        reorders = [_mv(m, "reorder_point") or 0 for m in materials]

        fig_stock = go.Figure()
        fig_stock.add_trace(go.Bar(
            x=skus, y=stocks,
            name="Initial Stock",
            marker=dict(color=ACCENT_EMERALD, line=dict(width=0)),
            text=stocks,
            textposition="auto",
            textfont=dict(color="white"),
        ))
        fig_stock.add_trace(go.Scatter(
            x=skus, y=reorders,
            name="Reorder Point",
            mode="markers+lines",
            marker=dict(color=ACCENT_ROSE, size=10),
            line=dict(color=ACCENT_ROSE, dash="dash"),
        ))
        fig_stock.update_layout(
            title="Material Stock Levels vs Reorder Points",
            xaxis_title="Material SKU",
            yaxis_title="Quantity",
            height=350,
            barmode="group",
        )
        apply_plotly_theme(fig_stock)
        st.plotly_chart(fig_stock, use_container_width=True)

        # Receipt timeline
        receipt_rows = []
        for m in materials:
            sku = _mv(m, "sku")
            receipts = _mv(m, "receipts")
            if receipts:
                for r in receipts:
                    receipt_rows.append({
                        "Material": sku,
                        "Quantity": r.get("quantity", r["quantity"] if isinstance(r, dict) else 0),
                        "Available At (min)": r.get("available_at", 0),
                        "Source": r.get("source", "—"),
                    })
        if receipt_rows:
            st.markdown("### Scheduled Receipts")
            st.dataframe(receipt_rows, use_container_width=True, hide_index=True)
