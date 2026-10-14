"""Recovery Pipeline page — live LLM agent visualization with node-by-node progress."""
from __future__ import annotations


def render() -> None:
    import streamlit as st

    from coe.agents.llm_client import LLMConfigError, require_llm_config
    from coe.config import get_settings
    from coe.dashboard.theme import glass_card, pipeline_node_html

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    st.markdown("# 🤖 Recovery Pipeline")
    st.caption("Watch the LLM agents analyze disruptions and the CP-SAT solver reschedule in real-time")

    # ---- LLM preflight ----
    try:
        require_llm_config(get_settings())
    except LLMConfigError as exc:
        st.markdown(f"""
        <div class="glass-card" style="border-color: rgba(245,158,11,0.4);">
            <div style="display: flex; align-items: center; gap: 1rem;">
                <span style="font-size: 2rem;">⚠️</span>
                <div>
                    <div style="font-weight: 600; color: #f59e0b; font-size: 1.1rem;">
                        LLM Configuration Required
                    </div>
                    <div style="color: #94a3b8; margin-top: 0.3rem;">
                        {exc}<br>
                        Set <code>GOOGLE_API_KEY</code> or <code>OPENAI_API_KEY</code> in your
                        <code>.env</code> to enable the recovery pipeline.
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.stop()

    # ---- pipeline architecture visual ----
    st.markdown("## 🏗️ Pipeline Architecture")
    st.markdown("""
    <div class="glass-card">
        <div style="text-align: center; color: #94a3b8; font-size: 0.8rem; text-transform: uppercase;
             letter-spacing: 0.1em; margin-bottom: 1rem;">
             LangGraph Recovery Pipeline — 14 Nodes
        </div>
        <div class="arch-flow">
            <div class="arch-node" style="border-color: rgba(99,102,241,0.4);">
                🗣️ Narrative
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(34,211,238,0.4);">
                🔄 Translate
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(16,185,129,0.4);">
                📥 Ingest
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(245,158,11,0.4);">
                🔍 Investigate ×4
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(139,92,246,0.4);">
                📋 Strategy
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(236,72,153,0.4);">
                ⚙️ Compile
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(99,102,241,0.4);">
                🧮 CP-SAT Solve
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(34,211,238,0.4);">
                🚪 Gate
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(16,185,129,0.4);">
                💾 Commit
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(245,158,11,0.4);">
                ✅ Verify
            </div>
            <span class="arch-arrow">→</span>
            <div class="arch-node" style="border-color: rgba(139,92,246,0.4);">
                📝 Explain
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- chat history ----
    if "cockpit_messages" not in st.session_state:
        st.session_state["cockpit_messages"] = []

    _render_history(st.session_state["cockpit_messages"])

    # ---- user input ----
    st.markdown("## 💬 Describe the Disruption")
    prompt = st.chat_input("e.g., Machine M3 has broken down and needs repair…")
    if not prompt:
        # Show helpful examples
        if not st.session_state["cockpit_messages"]:
            st.markdown("""
            <div class="glass-card">
                <div style="color: #94a3b8; font-size: 0.9rem; margin-bottom: 0.8rem;">
                    <strong style="color: #e2e8f0;">💡 Example Disruption Narratives:</strong>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem;">
                    <div style="background: rgba(99,102,241,0.08); border: 1px solid rgba(99,102,241,0.2);
                         border-radius: 10px; padding: 0.8rem; cursor: pointer;">
                        <div style="color: #6366f1; font-weight: 600; font-size: 0.85rem;">
                            🔧 Machine Failure
                        </div>
                        <div style="color: #94a3b8; font-size: 0.82rem; margin-top: 0.3rem;">
                            "Machine M3 has experienced a critical failure and is offline"
                        </div>
                    </div>
                    <div style="background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.2);
                         border-radius: 10px; padding: 0.8rem; cursor: pointer;">
                        <div style="color: #f59e0b; font-weight: 600; font-size: 0.85rem;">
                            👷 Worker Absence
                        </div>
                        <div style="color: #94a3b8; font-size: 0.82rem; margin-top: 0.3rem;">
                            "Worker W2 called in sick and won't be available today"
                        </div>
                    </div>
                    <div style="background: rgba(16,185,129,0.08); border: 1px solid rgba(16,185,129,0.2);
                         border-radius: 10px; padding: 0.8rem; cursor: pointer;">
                        <div style="color: #10b981; font-weight: 600; font-size: 0.85rem;">
                            📦 Material Shortage
                        </div>
                        <div style="color: #94a3b8; font-size: 0.82rem; margin-top: 0.3rem;">
                            "We've run out of MAT-001 and the next delivery is delayed"
                        </div>
                    </div>
                    <div style="background: rgba(244,63,94,0.08); border: 1px solid rgba(244,63,94,0.2);
                         border-radius: 10px; padding: 0.8rem; cursor: pointer;">
                        <div style="color: #f43f5e; font-weight: 600; font-size: 0.85rem;">
                            💥 Combined Disruption
                        </div>
                        <div style="color: #94a3b8; font-size: 0.82rem; margin-top: 0.3rem;">
                            "M1 is down and worker W5 is absent since minute 120"
                        </div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
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


_NODE_LABELS: dict[str, tuple[str, str]] = {
    "entry": ("🚀", "Initializing recovery pipeline"),
    "translate": ("🔄", "LLM translating disruption narrative"),
    "ingest": ("📥", "Ingesting telemetry data into DB"),
    "machine_agent": ("🔧", "Machine Agent — investigating machine status"),
    "production_agent": ("📊", "Production Agent — analyzing production constraints"),
    "inventory_agent": ("📦", "Inventory Agent — checking materials & stock"),
    "worker_agent": ("👷", "Worker Agent — assessing worker availability"),
    "strategy": ("📋", "LLM formulating recovery strategy"),
    "manager_compile": ("⚙️", "Manager compiling strategy for solver"),
    "solve_node": ("🧮", "CP-SAT solving schedule (~2 min on factory scale)"),
    "gate_node": ("🚪", "Quality gate — validating solution"),
    "commit_node": ("💾", "Committing recovery schedule to DB"),
    "verify_node": ("✅", "Verifying schedule integrity"),
    "explain_node": ("📝", "LLM generating explanation"),
}


def _run_recovery(instance_name: str, narrative: str) -> None:
    import streamlit as st

    from coe.agents.graph import execute_recovery_streaming
    from coe.dashboard.theme import pipeline_node_html

    # Capture before-state
    before_entries = _fetch_active_entries(instance_name)

    with st.status("🤖 Running recovery pipeline…", expanded=True) as status:
        completed_nodes: list[str] = []
        feed_area = st.empty()
        progress_bar = st.progress(0)

        result = None
        total_nodes = len(_NODE_LABELS)

        for chunk in execute_recovery_streaming(
            instance_name, trigger="CLI", narrative=narrative,
        ):
            if "node" in chunk:
                node = chunk["node"]
                completed_nodes.append(node)
                icon, label = _NODE_LABELS.get(node, ("⚡", node))

                # Build pipeline visualization
                pipeline_html = ""
                for n_name, (n_icon, n_label) in _NODE_LABELS.items():
                    if n_name in completed_nodes:
                        n_status = "completed"
                    elif n_name == node:
                        n_status = "active"
                    else:
                        n_status = "pending"
                    pipeline_html += pipeline_node_html(n_icon, n_label, n_status)

                feed_area.markdown(pipeline_html, unsafe_allow_html=True)
                progress = min(len(completed_nodes) / total_nodes, 1.0)
                progress_bar.progress(progress)
            else:
                result = chunk

        progress_bar.progress(1.0)

    if result is None:
        st.error("Recovery pipeline returned no result.")
        return

    status.update(
        label=f"✅ Recovery finished — **{result['status']}**",
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

def _render_outcome(status: str, state) -> None:
    import streamlit as st
    from coe.dashboard.theme import status_badge

    # Status header
    if status == "COMMITTED":
        badge_color = "rgba(16,185,129,0.15)"
        text_color = "#10b981"
    elif status in ("SOLVE_INFEASIBLE", "GATE_FAILED"):
        badge_color = "rgba(244,63,94,0.15)"
        text_color = "#f43f5e"
    else:
        badge_color = "rgba(245,158,11,0.15)"
        text_color = "#f59e0b"

    st.markdown(f"""
    <div style="background: {badge_color}; border: 1px solid {text_color}30;
         border-radius: 12px; padding: 1rem 1.5rem; margin: 1rem 0;">
        <div style="font-weight: 700; color: {text_color}; font-size: 1.2rem;">
            Outcome: {status}
        </div>
    </div>
    """, unsafe_allow_html=True)

    solution = getattr(state, "solution", None) if not isinstance(state, str) else None
    if solution:
        cols = st.columns(3)
        cols[0].metric("Makespan", solution.get("makespan", "—"))
        cols[1].metric("Total Tardiness", solution.get("total_tardiness", "—"))
        cols[2].metric("Solver Status", solution.get("status", "—"))

    if status == "UNKNOWN":
        st.info(
            "UNKNOWN means the solver was budget-starved (time limit hit "
            "before proving feasibility). It does **not** mean a material-conflict."
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

    vid = getattr(state, "committed_version_id", None)
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
            st.markdown("## 📝 LLM Explanation")
            st.markdown(f"""
            <div class="glass-card">
                <div style="color: #e2e8f0; line-height: 1.8; font-size: 0.95rem;">
                    {explanation.rationale}
                </div>
            </div>
            """, unsafe_allow_html=True)


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
    """Render the before → after schedule diff animation."""
    import streamlit as st

    from coe.dashboard.diff import schedule_frames
    from coe.dashboard.theme import apply_plotly_theme

    after_entries = _fetch_active_entries(instance_name)
    frames = schedule_frames(before_entries, after_entries)
    if not frames:
        return

    st.markdown("## 🔄 Schedule Transition Animation")
    st.markdown("""
    <div class="glass-card" style="margin-bottom: 1rem;">
        <div style="color: #94a3b8; font-size: 0.85rem;">
            The animation below shows how the schedule changed after recovery.
            <strong style="color: #f43f5e;">Faded bars</strong> = removed operations.
            <strong style="color: #10b981;">Solid bars</strong> = new assignments.
            Use the slider to step through each change.
        </div>
    </div>
    """, unsafe_allow_html=True)

    for frame in frames:
        apply_plotly_theme(frame)

    st.plotly_chart(frames[-1], use_container_width=True)
    if len(frames) > 1:
        idx = st.slider("Recovery move", 1, len(frames), len(frames))
        st.plotly_chart(frames[idx - 1], use_container_width=True)


def _outcome_text(status: str, state) -> str:
    parts = [f"Recovery outcome: **{status}**"]

    solution = getattr(state, "solution", None) if not isinstance(state, str) else None
    if solution:
        parts.append(
            f"Makespan {solution.get('makespan', '—')} · "
            f"Tardiness {solution.get('total_tardiness', '—')} · "
            f"Solver {solution.get('status', '—')}"
        )

    if status == "UNKNOWN":
        parts.append("UNKNOWN = solver budget-exhausted (not a material conflict).")

    explanation = getattr(state, "explanation", None) if not isinstance(state, str) else None
    if status == "COMMITTED" and explanation:
        parts.append(f"\n\n{explanation}")

    return "\n\n".join(parts)
