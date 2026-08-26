"""Streamlit cockpit entrypoint."""
import importlib


def main() -> None:
    import streamlit as st

    from coe.dashboard.data import list_instances
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

    pages = {
        "Cockpit": "cockpit",
        "Configure": "configure",
        "Runs": "runs",
        "Benchmarks": "benchmarks",
    }
    choice = st.sidebar.radio("Pages", list(pages), key="nav")
    try:
        module = importlib.import_module(f"coe.dashboard.pages.{pages[choice]}")
    except ModuleNotFoundError:
        st.info(f"{choice} page arrives in a later task.")
        return
    st.title(choice)
    render = getattr(module, "render", None)
    if render is not None:
        render()


if __name__ == "__main__":
    main()
