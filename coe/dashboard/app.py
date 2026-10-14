"""Streamlit cockpit entrypoint — V2 professional dashboard."""


def main() -> None:
    import streamlit as st

    from coe.dashboard.data import list_instances
    from coe.dashboard.theme import inject_theme
    from coe.dashboard.pages.overview import render as render_overview
    from coe.dashboard.pages.dataset import render as render_configure
    from coe.dashboard.pages.baseline import render as render_baseline
    from coe.dashboard.pages.disruption import render as render_disruption
    from coe.dashboard.pages.recovery import render as render_cockpit
    from coe.dashboard.pages.analytics import render as render_analytics
    from coe.dashboard.pages.history import render as render_runs
    from coe.dashboard.pages.benchmarks import render as render_benchmarks
    from coe.dashboard.pages.simulate import render as render_simulate
    from coe.db.session import session_scope

    st.set_page_config(
        page_title="COE Factory Recovery — Agentic Autonomous System",
        page_icon="🏭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    inject_theme()

    # ---- sidebar branding ----
    st.sidebar.markdown("""
    <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
        <div style="font-size: 2rem;">🏭</div>
        <div style="font-size: 1.1rem; font-weight: 700;
             background: linear-gradient(135deg, #6366f1, #22d3ee);
             -webkit-background-clip: text; -webkit-text-fill-color: transparent;
             background-clip: text; margin-top: 0.3rem;">
             COE Factory Recovery
        </div>
        <div style="font-size: 0.72rem; color: #64748b; margin-top: 0.2rem;
             letter-spacing: 0.1em; text-transform: uppercase;">
             Agentic Autonomous System
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.sidebar.markdown("---")

    # ---- instance selector ----
    with session_scope() as session:
        instances = list_instances(session)

    if not instances:
        st.warning(
            "No instances found. Build one:\n\n```bash\n"
            "uv run python -m coe.cli import mk01\n"
            "uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42\n"
            "uv run python -m coe.cli solve baseline --instance factory_demo_01\n```"
        )
        st.stop()

    def value(instance, name):
        return instance[name] if isinstance(instance, dict) else getattr(instance, name)

    names = [value(instance, "name") for instance in instances]
    default = "factory_demo_01" if "factory_demo_01" in names else names[0]

    st.sidebar.markdown(
        '<p style="color: #94a3b8; font-size: 0.8rem; font-weight: 600; '
        'text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.3rem;">'
        '📋 Active Instance</p>',
        unsafe_allow_html=True,
    )
    selected = st.sidebar.selectbox(
        "Instance", names, index=names.index(default),
        key="instance_name", label_visibility="collapsed",
    )
    st.session_state["instance"] = selected

    parent = next(
        value(instance, "parent") for instance in instances
        if value(instance, "name") == selected
    )
    if parent:
        st.sidebar.caption(f"↳ fork of **{parent}**")

    st.sidebar.markdown("---")

    # ---- MQTT live rail ----
    from coe.dashboard.rail import render_rail, start_rail
    start_rail()
    render_rail()

    # ---- page navigation ----
    pages = [
        st.Page(render_overview, title="Overview", icon="🏠", url_path="overview", default=True),
        st.Page(render_configure, title="Configure — Dataset", icon="🔧📊", url_path="dataset"),
        st.Page(render_baseline, title="Baseline Schedule", icon="⚙️", url_path="baseline"),
        st.Page(render_disruption, title="Disruption Simulator", icon="💥", url_path="disruption"),
        st.Page(render_cockpit, title="Cockpit — Recovery", icon="🤖", url_path="recovery"),
        st.Page(render_analytics, title="Analytics", icon="📊📈", url_path="analytics"),
        st.Page(render_runs, title="Runs — History", icon="📋", url_path="history"),
        st.Page(render_benchmarks, title="Benchmarks", icon="🧪", url_path="benchmarks"),
        st.Page(render_simulate, title="Simulate", icon="🎬", url_path="simulate"),
    ]
    page = st.navigation(pages)
    page.run()


if __name__ == "__main__":
    main()
