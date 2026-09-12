"""Simulate page — scripted-day playback with pacing controls."""
from __future__ import annotations


def _fork_or_default(instance_name: str, script_name: str) -> str:
    """Fork semantics identical to the CLI _run_simulate fork block.

    Honors settings.simulate_clone; fork name ``sim-<script>@<8hex>``.
    Never imports private CLI functions.
    """
    import uuid

    from sqlalchemy.exc import NoResultFound
    from sqlalchemy.orm import Session

    from coe.config import get_settings
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.services.fork import ForkError, fork_instance

    s = get_settings()
    if not s.simulate_clone:
        return instance_name
    with Session(make_engine()) as session:
        try:
            source = (session.query(Instance)
                      .filter(Instance.name == instance_name).one())
        except NoResultFound:
            raise ValueError(
                f"unknown instance: {instance_name} — source must exist "
                "to fork")
        try:
            forked = fork_instance(
                session, source,
                new_name=f"sim-{script_name}@{uuid.uuid4().hex[:8]}")
        except ForkError as exc:
            raise ValueError(f"fork failed: {exc}")
        session.commit()
        return forked.name


def _fetch_active_entries(instance_name: str) -> list[dict]:
    """Committed active schedule entries (mirror of cockpit's helper)."""
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
        ), {"iid": row["id"]}).mappings().all()
        return [dict(e) for e in entries]


def _rail_line(instance_name: str, t: int) -> str:
    from sqlalchemy.orm import Session

    from coe.db.session import make_engine
    from coe.simulator.projector import project_day

    with Session(make_engine()) as s:
        ds = project_day(s, instance_name=instance_name, t=t)
    return ds.feed_line()


def _render_diff(instance_name: str, before_entries: list[dict]) -> None:
    """Render the schedule diff — only at the terminal event."""
    import streamlit as st

    from coe.dashboard.diff import schedule_frames

    after_entries = _fetch_active_entries(instance_name)
    frames = schedule_frames(before_entries, after_entries)
    if not frames:
        st.info("Schedule unchanged by this day.")
        return
    st.subheader("Schedule Transition")
    st.plotly_chart(frames[-1], use_container_width=True)


def _render_idle(tl) -> None:
    import streamlit as st

    st.subheader(f"Timeline: **{tl.name}**")
    st.caption(f"{len(tl.events)} events · "
               f"horizon {tl.horizon_days} day(s) · seed {tl.seed}")


def _render_day() -> None:
    """Big clock + per-event progress + event feed (session-state driven)."""
    import streamlit as st

    clock = st.session_state.get("sim_clock", 0)
    st.metric("Day clock", f"{clock} min")

    total = max(st.session_state.get("sim_total", 1), 1)
    st.progress(min(st.session_state["sim_last_idx"] / total, 1.0))
    if st.session_state.get("sim_feed"):
        with st.status("Event feed", expanded=True):
            st.markdown("\n".join(st.session_state["sim_feed"]))


def render() -> None:
    import pathlib

    import streamlit as st
    from streamlit.errors import StreamlitAPIException

    from coe.simulator.engine import walk_timeline
    from coe.simulator.timeline import TimelineError, load_timeline

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    # --- session-state contract ------------------------------------------
    for key, default in (
        ("sim_last_idx", 0), ("sim_paused", False),
        ("sim_running", False), ("sim_clock", 0), ("sim_total", 0),
        ("sim_active_instance", None), ("sim_before_entries", None),
        ("sim_feed", []),
    ):
        st.session_state.setdefault(key, default)

    # --- timeline source ---------------------------------------------------
    manual_entry = "sim_script" in st.session_state
    if manual_entry:
        script_path = st.session_state["sim_script"]
    else:
        scripts = sorted(pathlib.Path("data/timelines").glob("*.json"))
        if not scripts:
            st.error("No timeline scripts under data/timelines/. "
                     "Nothing to simulate.")
            st.stop()
        pick = st.sidebar.selectbox("Timeline", [p.name for p in scripts])
        script_path = str(pathlib.Path("data/timelines") / pick)

    from pydantic import ValidationError

    try:
        tl = load_timeline(script_path)
    except (TimelineError, ValidationError) as exc:
        # Deviation vs brief: pydantic schema violations surface as
        # ValidationError, not TimelineError — both must exit cleanly
        # (same deviation as the CLI).
        st.error(f"Timeline rejected: {exc}")
        st.stop()
    if not tl.events:
        st.error(f"Timeline '{tl.name}' has no events.")
        st.stop()

    speed = st.session_state.get("sim_speed")
    if speed is None:
        speed = st.sidebar.selectbox("Speed", ["instant", 10, 30, 60],
                                     index=2)

    use_llm = st.sidebar.toggle(
        "LLM narration (AI strategy + explanation)", value=False,
        help="Off = deterministic auto-fix: translate/strategy/explain "
             "are answered by the degraded client; the solver alone "
             "re-plans. On = live provider.")
    if use_llm:
        llm_client_factory = None    # None = live provider (§9)
    else:
        from coe.agents.degraded_client import DegradedLLMClient

        llm_client_factory = lambda: DegradedLLMClient()  # noqa: E731
    if speed != "instant":
        try:
            speed_val = int(speed)
        except (TypeError, ValueError):
            st.error(f"invalid speed: {speed} "
                     "(instant or a positive integer)")
            st.stop()
        if speed_val <= 0:
            st.error(f"invalid speed: {speed} "
                     "(instant or a positive integer)")
            st.stop()

    st.session_state["sim_total"] = len(tl.events)
    total = len(tl.events)
    finished = st.session_state["sim_last_idx"] >= total

    # --- sidebar controls (browser flow; manual_entry auto-runs below) -----
    if not manual_entry:
        col_run, col_step = st.sidebar.columns(2)
        run_pressed = col_run.button(
            "Run" if finished else "Resume", type="primary",
            key="sim_run")
        pause_pressed = col_step.button(
            "Pause", key="sim_pause",
            disabled=not st.session_state["sim_running"]
            or st.session_state["sim_paused"])

        if pause_pressed:
            st.session_state["sim_paused"] = True
            st.info("Playback paused. Press Resume to continue.")
            st.stop()
    else:
        run_pressed = True
        pause_pressed = False

    # Clear the pause BEFORE computing start_run: otherwise a paused run
    # can never resume (start_run was gated on sim_paused staying True).
    if run_pressed:
        st.session_state["sim_paused"] = False
    start_run = run_pressed
    if start_run:
        active_prev = st.session_state["sim_active_instance"]
        if finished or active_prev is None:
            try:
                active = _fork_or_default(instance_name, tl.name)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            st.session_state["sim_active_instance"] = active
            st.session_state["sim_last_idx"] = 0
            st.session_state["sim_clock"] = 0
            st.session_state["sim_feed"] = []
            st.session_state["sim_before_entries"] = \
                _fetch_active_entries(active)
        st.session_state["sim_running"] = True

    _render_idle(tl)
    _render_day()

    running = st.session_state["sim_running"]
    paused = st.session_state["sim_paused"]
    if not running or paused:
        st.caption("Paused — press Run/Resume." if running
                   else "Press Run to start the scripted day.")
        return

    active = st.session_state["sim_active_instance"]
    before_entries = st.session_state["sim_before_entries"]

    terminal = None
    # Cockpit-style context manager so the live feed renders inside the
    # status container; the exception-guard shim stays for bare-mode.
    with st.status("Simulating day…", expanded=True) as status:
        feed_area = st.empty()

        def _flush() -> None:
            feed_area.markdown("\n".join(st.session_state["sim_feed"]))

        if speed == "instant":
            # Instant walks complete synchronously in ONE render pass —
            # safe inside a st.status block (no threading; RunManager lesson).
            for chunk in walk_timeline(
                tl, instance_name=active, speed="instant",
                llm_client_factory=llm_client_factory,
                start_index=st.session_state["sim_last_idx"],
            ):
                if chunk["event"] == "done":
                    terminal = chunk
                    break
                if chunk["event"] == "recovery_start":
                    # Do NOT advance sim_last_idx — the completion chunk
                    # advances it. Surface that this step is live.
                    st.session_state["sim_feed"].append(
                        f"[t={chunk['t']:>4}] ⏳ recovery starting "
                        f"({'live LLM' if chunk.get('live') else 'auto-fix (no LLM)'}) —"
                        " this takes minutes (translate + solver floor)…")
                    _flush()
                    continue
                st.session_state["sim_last_idx"] = chunk["idx"] + 1
                st.session_state["sim_clock"] = chunk["t"]
                st.session_state["sim_feed"].append(
                    f"[t={chunk['t']:>4}] {chunk['event']} "
                    f"{chunk.get('kind', '')}".rstrip())
                _flush()
        else:
            # Paced mode: ONE event per rerender. A fresh generator is
            # created on every button-driven rerender; the engine skips
            # idx < start_index (already persisted) and runs silently
            # instant — the page owns the pacing clock via the pause
            # toggle + Run/Resume buttons.
            gen = walk_timeline(tl, instance_name=active, speed="instant",
                                llm_client_factory=llm_client_factory,
                                start_index=st.session_state["sim_last_idx"])
            # One TERMINAL-BAR step per click: recovery_start chunks are
            # consumed inline (displayed) until the step's completion chunk
            # arrives — the generator is only ever closed at a yield
            # boundary, never mid-recovery.
            chunk = None
            while True:
                chunk = next(gen, None)
                if chunk is None or chunk["event"] == "recovery_start":
                    if chunk is not None:
                        st.session_state["sim_feed"].append(
                            f"[t={chunk['t']:>4}] ⏳ recovery starting "
                            f"({'live LLM' if chunk.get('live') else 'auto-fix (no LLM)'}) —"
                            " this takes minutes…")
                        _flush()
                    continue
                break
            gen.close()
            if chunk is not None:
                if chunk["event"] == "done":
                    terminal = chunk
                else:
                    st.session_state["sim_last_idx"] = chunk["idx"] + 1
                    st.session_state["sim_clock"] = chunk["t"]
                    st.session_state["sim_feed"].append(
                        f"[t={chunk['t']:>4}] {chunk['event']} "
                        f"{chunk.get('kind', '')}".rstrip())
                    _flush()

        try:
            status.update(
                label="Day complete" if terminal is not None else "Simulating…",
                state="complete" if terminal is not None else "running",
            )
        except (StreamlitAPIException, AttributeError):
            # bare Streamlit (tests): status containers have no update()
            # (bare-mode StatusContainer.__enter__ may return None)
            pass

    if terminal is not None:
        st.session_state["sim_running"] = False
        st.session_state["sim_last_idx"] = total
        committed = terminal.get("committed") or []
        if committed:
            st.success(f"{len(committed)} recovery version(s) committed.")
        else:
            st.info("Day finished — structured events absorbed "
                    "without a recovery solve.")
        st.caption(_rail_line(active, st.session_state["sim_clock"]))
        if before_entries:
            _render_diff(active, before_entries)
