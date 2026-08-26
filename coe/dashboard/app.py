"""Streamlit cockpit entrypoint."""


def main() -> None:
    import streamlit as st

    from coe.dashboard.data import list_instances
    from coe.dashboard.pages.benchmarks import render as render_benchmarks
    from coe.dashboard.pages.cockpit import render as render_cockpit
    from coe.dashboard.pages.configure import render as render_configure
    from coe.dashboard.pages.runs import render as render_runs
    from coe.db.session import session_scope

    st.set_page_config(
        page_title="COE Factory Recovery Cockpit",
        page_icon="🏭",
        layout="wide",
    )

    with session_scope() as session:
        instances = list_instances(session)
    if not instances:
        st.warning(
            "No instances found. Build one: `uv run python -m coe.cli import mk01 && "
            "uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42`"
        )
        st.stop()

    def value(instance, name):
        return instance[name] if isinstance(instance, dict) else getattr(instance, name)

    names = [value(instance, "name") for instance in instances]
    default = "factory_demo_01" if "factory_demo_01" in names else names[0]
    selected = st.sidebar.selectbox(
        "Instance", names, index=names.index(default), key="instance_name"
    )
    st.session_state["instance"] = selected
    parent = next(value(instance, "parent") for instance in instances
                  if value(instance, "name") == selected)
    if parent:
        st.sidebar.caption(f"fork of **{parent}**")

    pages = [
        st.Page(render_cockpit, title="Cockpit", icon="⚙️", url_path="cockpit", default=True),
        st.Page(render_configure, title="Configure", icon="🔧", url_path="configure"),
        st.Page(render_runs, title="Runs", icon="📊", url_path="runs"),
        st.Page(render_benchmarks, title="Benchmarks", icon="📈", url_path="benchmarks"),
    ]
    page = st.navigation(pages)
    page.run()


if __name__ == "__main__":
    main()
