"""Disruption Simulator page — create disruptions with animated visual feedback."""
from __future__ import annotations


def render() -> None:
    import streamlit as st
    from sqlalchemy import text

    from coe.dashboard.actions import (
        machine_down_action,
        machine_restore_action,
        material_shortage_action,
        worker_absent_action,
        worker_return_action,
    )
    from coe.dashboard.data import (
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
        glass_card,
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

    st.markdown("# 💥 Disruption Simulator")
    st.caption("Create real-time disruptions to test the recovery system")

    _v = lambda obj, k: obj.get(k) if isinstance(obj, dict) else getattr(obj, k)

    # ---- disruption type selector ----
    st.markdown("<br>", unsafe_allow_html=True)

    tab_machine, tab_worker, tab_material = st.tabs([
        "🔧 Machine Failure", "👷 Worker Absence", "🧪 Material Shortage",
    ])

    # ==================== MACHINE ====================
    with tab_machine:
        st.markdown("## 🔧 Machine Failure Simulation")
        st.markdown("""
        <div class="glass-card" style="margin-bottom: 1.5rem;">
            <div style="color: #94a3b8; font-size: 0.9rem; line-height: 1.7;">
                <strong style="color: #f43f5e;">Machine failure</strong> is the most common
                disruption in manufacturing. When a machine fails, all operations assigned to it
                must be rescheduled to other capable machines. The CP-SAT solver re-optimizes
                the entire schedule considering the reduced machine capacity.
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Current machine status
        st.markdown("### Current Machine Status")
        cols = st.columns(4)
        for idx, m in enumerate(machines):
            name = _v(m, "name")
            status = _v(m, "status")
            down_since = _v(m, "down_since")
            detail = f"⚠️ Down since t={down_since}" if down_since else "✅ Operational"
            with cols[idx % 4]:
                st.markdown(resource_card_html(f"🔧 {name}", status, [detail]),
                            unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Action panel
        st.markdown("### ⚡ Actions")
        ac1, ac2 = st.columns(2)

        with ac1:
            st.markdown(glass_card("""
                <div style="text-align: center; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem;">💥</span>
                    <span style="font-weight: 600; color: #f43f5e; margin-left: 0.5rem;">
                        Trigger Machine Failure
                    </span>
                </div>
            """), unsafe_allow_html=True)
            machine_names = [_v(m, "name") for m in machines]
            sel_machine = st.selectbox(
                "Select Machine", machine_names, key="disrupt_machine_sel",
            )
            at_time = st.number_input(
                "Failure time (minutes from start)",
                min_value=0, value=0, step=10,
                key="disrupt_machine_at",
                help="0 = current time",
            )
            if st.button("🔴 Mark Machine DOWN", key="disrupt_machine_btn", type="primary"):
                at = at_time if at_time > 0 else None
                ok, err = machine_down_action(instance_name, sel_machine, at=at)
                if ok:
                    st.success(f"✅ {ok}")
                    st.balloons()
                    st.rerun()
                else:
                    st.error(f"❌ {err}")

        with ac2:
            st.markdown(glass_card("""
                <div style="text-align: center; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem;">🔧</span>
                    <span style="font-weight: 600; color: #10b981; margin-left: 0.5rem;">
                        Restore Machine
                    </span>
                </div>
            """), unsafe_allow_html=True)
            sel_restore = st.selectbox(
                "Select Machine to Restore", machine_names, key="restore_machine_sel",
            )
            if st.button("🟢 Restore Machine", key="restore_machine_btn"):
                ok, err = machine_restore_action(instance_name, sel_restore)
                if ok:
                    st.success(f"✅ {ok}")
                    st.rerun()
                else:
                    st.error(f"❌ {err}")

    # ==================== WORKER ====================
    with tab_worker:
        st.markdown("## 👷 Worker Absence Simulation")
        st.markdown("""
        <div class="glass-card" style="margin-bottom: 1.5rem;">
            <div style="color: #94a3b8; font-size: 0.9rem; line-height: 1.7;">
                <strong style="color: #f59e0b;">Worker absence</strong> disrupts operations
                that require specific worker skills. The system must find alternative workers
                with compatible qualifications, or defer operations until the worker returns.
                This tests the worker-flexibility layer of the FJSP model.
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Current worker status
        st.markdown("### Current Worker Status")
        cols = st.columns(4)
        for idx, w in enumerate(workers):
            name = _v(w, "name")
            role = _v(w, "role") or "General"
            absent_since = _v(w, "absent_since")
            status = "UNAVAILABLE" if absent_since is not None else "OPERATIONAL"
            detail = f"⚠️ Absent since t={absent_since}" if absent_since else "✅ Available"
            with cols[idx % 4]:
                st.markdown(resource_card_html(f"👷 {name}", status, [f"Role: {role}", detail]),
                            unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### ⚡ Actions")
        wc1, wc2 = st.columns(2)

        with wc1:
            st.markdown(glass_card("""
                <div style="text-align: center; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem;">🚫</span>
                    <span style="font-weight: 600; color: #f59e0b; margin-left: 0.5rem;">
                        Mark Worker Absent
                    </span>
                </div>
            """), unsafe_allow_html=True)
            worker_names = [_v(w, "name") for w in workers]
            sel_worker = st.selectbox(
                "Select Worker", worker_names, key="disrupt_worker_sel",
            )
            if st.button("🟡 Mark ABSENT", key="disrupt_worker_btn", type="primary"):
                ok, err = worker_absent_action(instance_name, sel_worker)
                if ok:
                    st.success(f"✅ {ok}")
                    st.balloons()
                    st.rerun()
                else:
                    st.error(f"❌ {err}")

        with wc2:
            st.markdown(glass_card("""
                <div style="text-align: center; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem;">✅</span>
                    <span style="font-weight: 600; color: #10b981; margin-left: 0.5rem;">
                        Return Worker
                    </span>
                </div>
            """), unsafe_allow_html=True)
            sel_return = st.selectbox(
                "Select Worker to Return", worker_names, key="return_worker_sel",
            )
            if st.button("🟢 Worker Return", key="return_worker_btn"):
                ok, err = worker_return_action(instance_name, sel_return)
                if ok:
                    st.success(f"✅ {ok}")
                    st.rerun()
                else:
                    st.error(f"❌ {err}")

    # ==================== MATERIAL ====================
    with tab_material:
        st.markdown("## 🧪 Material Shortage Simulation")
        st.markdown("""
        <div class="glass-card" style="margin-bottom: 1.5rem;">
            <div style="color: #94a3b8; font-size: 0.9rem; line-height: 1.7;">
                <strong style="color: #6366f1;">Material shortage</strong> forces the system
                to defer or reschedule operations that depend on unavailable raw materials.
                The solver uses a time-phased material reservoir to determine which operations
                can proceed with available stock and which must wait for scheduled receipts.
            </div>
        </div>
        """, unsafe_allow_html=True)

        if not materials:
            st.info("No materials configured for this instance.")
        else:
            # Current material status
            st.markdown("### Current Material Inventory")
            cols = st.columns(3)
            for idx, m in enumerate(materials):
                sku = _v(m, "sku")
                stock = _v(m, "initial_stock")
                reorder = _v(m, "reorder_point")
                receipts = _v(m, "receipts")
                n_receipts = len(receipts) if receipts else 0
                status = "OPERATIONAL" if stock > 0 else "FAILED"
                details = [
                    f"Stock: {stock}",
                    f"Reorder: {reorder if reorder is not None else '—'}",
                    f"Receipts: {n_receipts}",
                ]
                with cols[idx % 3]:
                    st.markdown(resource_card_html(f"🧪 {sku}", status, details),
                                unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("### ⚡ Report Shortage")

            st.markdown(glass_card("""
                <div style="text-align: center; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem;">📦</span>
                    <span style="font-weight: 600; color: #6366f1; margin-left: 0.5rem;">
                        Report Material Shortage
                    </span>
                </div>
            """), unsafe_allow_html=True)

            sku_list = [_v(m, "sku") for m in materials]
            sel_sku = st.selectbox("Select Material", sku_list, key="disrupt_mat_sel")
            if st.button("⚠️ Report SHORTAGE", key="disrupt_mat_btn", type="primary"):
                ok, err = material_shortage_action(instance_name, sel_sku)
                if ok:
                    st.success(f"✅ {ok}")
                    st.balloons()
                else:
                    st.error(f"❌ {err}")

    # ---- Impact summary ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("## 📋 Quick Reference — Disruption Types")
    st.markdown("""
    <div class="glass-card">
        <table style="width: 100%; border-collapse: collapse; color: #e2e8f0;">
            <thead>
                <tr style="border-bottom: 1px solid rgba(99,130,255,0.2);">
                    <th style="padding: 0.8rem; text-align: left; color: #94a3b8;">Type</th>
                    <th style="padding: 0.8rem; text-align: left; color: #94a3b8;">Impact</th>
                    <th style="padding: 0.8rem; text-align: left; color: #94a3b8;">Recovery Strategy</th>
                    <th style="padding: 0.8rem; text-align: left; color: #94a3b8;">MQTT Topic</th>
                </tr>
            </thead>
            <tbody>
                <tr style="border-bottom: 1px solid rgba(99,130,255,0.08);">
                    <td style="padding: 0.8rem;">💥 Machine Failure</td>
                    <td style="padding: 0.8rem;">Ops must move to alternative machines</td>
                    <td style="padding: 0.8rem;">RESCHEDULE / DEFER</td>
                    <td style="padding: 0.8rem;"><code>factory/+/machines/+/events</code></td>
                </tr>
                <tr style="border-bottom: 1px solid rgba(99,130,255,0.08);">
                    <td style="padding: 0.8rem;">🚫 Worker Absence</td>
                    <td style="padding: 0.8rem;">Qualified ops need reassignment</td>
                    <td style="padding: 0.8rem;">SUBSTITUTE / DEFER</td>
                    <td style="padding: 0.8rem;"><code>factory/+/workers/+/events</code></td>
                </tr>
                <tr>
                    <td style="padding: 0.8rem;">📦 Material Shortage</td>
                    <td style="padding: 0.8rem;">Dependent ops must defer to receipts</td>
                    <td style="padding: 0.8rem;">DEFER / SUSPEND_JOB</td>
                    <td style="padding: 0.8rem;"><code>factory/+/materials/+/events</code></td>
                </tr>
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)
