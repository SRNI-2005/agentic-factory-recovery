"""Simulate page — live-day + scripted-day playback with pacing controls.

Test seam: a *staged* chat submission may be injected via
``st.session_state["sim_chat_text"]`` before ``render()``; the production
path is the ``st.chat_input`` on the page itself (§6). Staged text is
consumed once into the InterruptQueue, exactly as typed input would be.
"""
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


def _active_exists(instance_name: str) -> bool:
    """True when the source instance has a non-rolled-back feasible version
    (the recovery pre-flight §4.4 contract)."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from coe.db.session import make_engine

    with Session(make_engine()) as session:
        n = session.execute(text(
            "SELECT COUNT(*) FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :n AND sv.solver_status IN ('OPTIMAL','FEASIBLE') "
            "AND sv.rolled_back = false"), {"n": instance_name}).scalar_one()
    return n > 0


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


def _walk_paced_seconds(speed) -> float:
    """Dwell between paced rerenders (N× speed: the page owns pacing)."""
    try:
        return 60.0 / int(speed)
    except (TypeError, ValueError):
        return 2.0


def _feed_line(chunk: dict) -> str:
    ev = chunk.get("event", "?")
    t = chunk.get("t", "—")
    if ev == "tick":
        return (f"[t={t:>4}] tick — done={chunk.get('completed', '?')} "
                f"running={chunk.get('in_progress', '?')}")
    if ev == "recovery_start":
        who = "live LLM" if chunk.get("live") else "auto-fix (no LLM)"
        return f"[t={t:>4}] ⏳ recovery starting ({who}) — solving…"
    if ev == "recovery":
        status = chunk.get("status", "?")
        mark = ("✓ COMMITTED" if status == "COMMITTED" else f"✗ {status}")
        return f"[t={t:>4}] recovery NARRATIVE — {mark}"
    if ev == "day_end":
        return f"[t={t:>4}] day_end"
    return f"[t={t:>4}] {ev}"


def _paint_idle_feed(caption: str | None) -> None:
    """Read-only feed panel for idle lanes (paused / complete / fresh).

    The ``st.status`` container remains the sole painter mid-walk — this
    only runs on early-return paths where the status block is skipped,
    so the feed never vanishes on a paused/completed rerender and never
    twin-paints while running.
    """
    import streamlit as st

    feed = st.session_state.get("sim_feed") or []
    if caption:
        st.caption(caption)
    if feed:
        st.markdown("\n\n".join(feed))


def _render_live(instance_name: str, llm_client_factory, speed) -> None:
    """Live-day controller (spec §6).

    Self-driving: instant = the whole day in ONE render pass; paced =
    ONE chunk per rerender with ``st.rerun()`` self-advancement
    (recovery chunks always complete inline — never discarded
    mid-recovery). The generator itself always runs ``instant``; the
    page owns the pacing clock (same controller semantics as the
    scripted paced lane).
    """
    import time as _time

    import streamlit as st
    from streamlit.errors import StreamlitAPIException

    from coe.simulator.live import InterruptQueue, LiveDayError, live_day

    st.session_state.setdefault("sim_complete", False)

    # Live lane owns sim_live_active/sim_live_clock (private keys) — never
    # the scripted lane's sim_active_instance/sim_clock. Re-fork only when
    # the live lane has no active fork or its SOURCE changed; scripted-lane
    # equality must never drive live-lane state.
    if (st.session_state.get("sim_live_active") is None
            or st.session_state.get("sim_live_instance") != instance_name):
        st.session_state["sim_live_instance"] = instance_name
        st.session_state["sim_interrupt_q"] = InterruptQueue()
        st.session_state["sim_seen"] = set()
        st.session_state["sim_feed"] = []
        st.session_state["sim_live_clock"] = 0
        st.session_state["sim_complete"] = False
        st.session_state["sim_live_paused"] = False
        try:
            active = _fork_or_default(instance_name, "live")
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        st.session_state["sim_live_active"] = active
    active = st.session_state["sim_live_active"]

    if st.session_state["sim_complete"]:
        st.caption("Day complete. Select a new instance to replay.")
        _paint_idle_feed(None)
        return

    st.session_state["sim_running"] = True
    st.session_state["sim_running_lane"] = "live"

    # §6: the chat input renders ONLY while the walk is live. A typed
    # submission is queued and the walk reruns; the staged key is the
    # test seam (see module docstring).
    staged = st.session_state.pop("sim_chat_text", None)
    chat = st.chat_input("Describe a disruption to inject mid-flight…",
                         key="sim_live_chat")
    # Push BOTH (staged first, then typed) — the queue is a deque with
    # ordered resolution; never silently drop a typed submission.
    if staged:
        st.session_state["sim_interrupt_q"].push(staged)
    if chat:
        st.session_state["sim_interrupt_q"].push(chat)

    with st.status("Simulating day…", expanded=True) as status:
        feed_area = st.empty()

        def _flush() -> None:
            feed_area.markdown("\n\n".join(st.session_state["sim_feed"]))

        def _record(chunk: dict) -> None:
            key = (chunk.get("t"), chunk.get("event"))
            if key in st.session_state["sim_seen"]:
                return
            st.session_state["sim_seen"].add(key)
            st.session_state["sim_feed"].append(_feed_line(chunk))
            st.session_state["sim_live_clock"] = chunk["t"]
            _flush()

        terminal = False
        rerender = False
        gen = live_day(active, speed="instant",
                       llm_client_factory=llm_client_factory,
                       start_clock=st.session_state["sim_live_clock"],
                       interrupt_queue=st.session_state["sim_interrupt_q"])
        try:
            chunk = next(gen, None)
            while chunk is not None:
                _record(chunk)
                if chunk["event"] == "day_end":
                    terminal = True
                    break
                if speed != "instant" and chunk["event"] != "recovery_start":
                    # one dwell per rerender; recovery completes inline
                    rerender = True
                    break
                chunk = next(gen, None)
        except LiveDayError as exc:
            st.error(str(exc))
            st.session_state["sim_running"] = False
            st.session_state["sim_running_lane"] = None
            st.stop()
        finally:
            # Deterministic generator close (workers pin restore), like
            # the scripted lane; a completed/dead generator may raise.
            try:
                gen.close()
            except Exception:
                pass
        try:
            status.update(
                label="Day complete" if terminal else "Simulating…",
                state="complete" if terminal else "running")
        except (StreamlitAPIException, AttributeError):
            pass

    if terminal:
        st.session_state["sim_running"] = False
        st.session_state["sim_running_lane"] = None
        st.session_state["sim_complete"] = True
    elif rerender:
        _time.sleep(min(60.0 / max(int(speed), 1), 10.0))
        st.rerun()


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
        ("sim_running", False), ("sim_running_lane", None),
        ("sim_clock", 0), ("sim_total", 0),
        ("sim_active_instance", None), ("sim_before_entries", None),
        ("sim_feed", []),
        ("sim_mode", "live"), ("sim_interrupt_q", None),
        ("sim_seen", set()),
        ("sim_live_active", None), ("sim_live_clock", 0),
        ("sim_live_paused", False), ("sim_complete", False),
    ):
        st.session_state.setdefault(key, default)

    # --- mode picker (before the timeline pickers; spec §6) ---------------
    # No widget key: the radio re-seeds from the persisted sim_mode each
    # render, so a staged sim_mode (test seam) and the user's last pick
    # both survive rerenders.
    mode = st.sidebar.radio(
        "Mode", ["Live day", "Scripted replay"],
        index=0 if st.session_state.get("sim_mode", "live") == "live"
        else 1, horizontal=True)
    st.session_state["sim_mode"] = ("live" if mode == "Live day"
                                    else "scripted")

    speed = st.session_state.get("sim_speed")
    if speed is None:
        speed = st.sidebar.selectbox("Speed", ["instant", 10, 30, 60],
                                     index=2)
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

    if st.session_state["sim_mode"] == "live":
        # Sidebar Run/Pause mirroring the scripted lane (spec §3 step 5).
        # sim_live_paused is live-lane-owned (the scripted sim_paused key
        # stays scripted-only).
        col_run, col_step = st.sidebar.columns(2)
        live_paused = st.session_state["sim_live_paused"]
        # Lane-owned running flag: sim_running set by the OTHER lane must
        # not enable this lane's Pause (or auto-drive its walk).
        live_running = (
            st.session_state["sim_running"]
            and st.session_state.get("sim_running_lane") == "live")
        run_pressed = col_run.button(
            "Run" if st.session_state["sim_complete"] else "Resume",
            type="primary", key="sim_live_run",
            disabled=st.session_state["sim_complete"])
        pause_pressed = col_step.button(
            "Pause", key="sim_live_pause",
            disabled=not live_running
            or live_paused or speed == "instant"
            or st.session_state["sim_complete"])

        if pause_pressed:
            st.session_state["sim_live_paused"] = True
            st.info("Playback paused. Press Resume to continue.")
            _paint_idle_feed(None)
            st.stop()
        if run_pressed:
            # Clear the pause BEFORE any early-stop so the walk resumes
            # from sim_live_clock (same ordering as the scripted lane).
            st.session_state["sim_live_paused"] = False
        elif live_paused:
            _paint_idle_feed("Paused — press Resume.")
            st.stop()
        _render_live(instance_name, llm_client_factory, speed)
        return

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

    # auto_recover override (spec amendment 2026-09-13): the toggle wins
    # over the timeline field when present; reset when the script changes
    # (same reset-on-script-change pattern as sim_script/sim_speed).
    if st.session_state.get("sim_auto_recover_script") != tl.name:
        st.session_state.pop("sim_auto_recover", None)
    tl.auto_recover = st.sidebar.toggle(
        "Auto-recover on every structured disruption",
        key="sim_auto_recover", value=tl.auto_recover,
        help="On = every structured disruption event is followed by a "
             "full recovery solve. Off = facts only (narrative steps "
             "still trigger solves).")
    st.session_state["sim_auto_recover_script"] = tl.name

    st.session_state["sim_total"] = len(tl.events)
    total = len(tl.events)
    finished = st.session_state["sim_last_idx"] >= total
    # Lane-owned running flag (see live lane): a sim_running left set by
    # the live lane must not auto-drive the scripted walk against a
    # stale/None sim_active_instance.
    scripted_running = (
        st.session_state["sim_running"]
        and st.session_state.get("sim_running_lane") == "scripted")

    # --- sidebar controls (browser flow; manual_entry auto-runs below) -----
    if not manual_entry:
        col_run, col_step = st.sidebar.columns(2)
        run_pressed = col_run.button(
            "Run" if finished else "Resume", type="primary",
            key="sim_run")
        pause_pressed = col_step.button(
            "Pause", key="sim_pause",
            disabled=not scripted_running
            or st.session_state["sim_paused"])

        if pause_pressed:
            st.session_state["sim_paused"] = True
            st.info("Playback paused. Press Resume to continue.")
            _paint_idle_feed(None)
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
            # Pre-flight: only narrative steps need an active baseline ON
            # THE SOURCE (the fork inherits it); structured-only scripts
            # work baseline-free (ingest + projector are self-sufficient).
            missing_baseline = (
                any(ev.kind == "NARRATIVE" for ev in tl.events)
                and not _active_exists(instance_name))
            if missing_baseline:
                st.error(
                    f"`{instance_name}` has no active schedule — run "
                    f"`uv run python -m coe.cli solve baseline --instance "
                    f"{instance_name}` first (every narrative event needs "
                    "a baseline to freeze from, §4.4).")
                st.stop()
            try:
                active = _fork_or_default(instance_name, tl.name)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            st.session_state["sim_active_instance"] = active
            st.session_state["sim_last_idx"] = 0
            st.session_state["sim_clock"] = 0
            st.session_state["sim_feed"] = []
            st.session_state["sim_seen"] = set()   # a second day repaints
            st.session_state["sim_before_entries"] = \
                _fetch_active_entries(active)
        st.session_state["sim_running"] = True
        st.session_state["sim_running_lane"] = "scripted"

    _render_idle(tl)
    _render_day()

    # Recompute AFTER the start_run block: a fresh Run press just set
    # sim_running/sim_running_lane above.
    running = (st.session_state["sim_running"]
               and st.session_state.get("sim_running_lane") == "scripted")
    paused = st.session_state["sim_paused"]
    if not running or paused:
        # Idle lane: keep the feed visible (paused / completed rerenders).
        feed = st.session_state["sim_feed"]
        if running:
            _paint_idle_feed("Paused — press Run/Resume.")
        elif finished and feed:
            _paint_idle_feed("Day complete.")
        elif not feed:
            _paint_idle_feed("Press Run to start the scripted day.")
        else:
            _paint_idle_feed(None)
        return

    active = st.session_state["sim_active_instance"]
    before_entries = st.session_state["sim_before_entries"]

    terminal = None
    # Cockpit-style context manager so the live feed renders inside the
    # status container; the exception-guard shim stays for bare-mode.
    with st.status("Simulating day…", expanded=True) as status:
        feed_area = st.empty()

        def _flush() -> None:
            feed_area.markdown("\n\n".join(st.session_state["sim_feed"]))

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
                    key = (chunk.get("t"), chunk.get("event"))
                    if key in st.session_state["sim_seen"]:
                        continue
                    st.session_state["sim_seen"].add(key)
                    st.session_state["sim_feed"].append(
                        f"[t={chunk['t']:>4}] ⏳ recovery starting "
                        f"({'live LLM' if chunk.get('live') else 'auto-fix (no LLM)'}) —"
                        " this takes minutes (translate + solver floor)…")
                    _flush()
                    continue
                st.session_state["sim_last_idx"] = chunk["idx"] + 1
                st.session_state["sim_clock"] = chunk["t"]
                key = (chunk.get("t"), chunk.get("event"))
                if key not in st.session_state["sim_seen"]:
                    st.session_state["sim_seen"].add(key)
                    if chunk["event"] == "recovery":
                        status = chunk.get("status", "?")
                        mark = "✓" if status == "COMMITTED" else f"✗ {status}"
                        st.session_state["sim_feed"].append(
                            f"[t={chunk['t']:>4}] recovery {chunk['kind']} — "
                            f"{mark}")
                    else:
                        st.session_state["sim_feed"].append(
                            f"[t={chunk['t']:>4}] {chunk['event']} "
                            f"{chunk.get('kind', '')}".rstrip())
                    _flush()
        else:
            # Paced mode: ONE full terminator per auto-rerender,
            # st.rerun() self-advancement. A fresh generator is created
            # on every rerender; the engine skips idx < start_index
            # (already persisted) and runs silently instant — the page
            # owns the pacing clock via the pause toggle + Run/Resume
            # buttons. recovery_start / auto_recover chunks NEVER
            # advance sim_last_idx and never break the loop — the walk
            # is driven to the next terminator (ingest chunk or
            # recovery completion) INSIDE this rerender so a narrative
            # solve finishes inline; the generator is only ever closed
            # at a yield boundary, never mid-recovery.
            gen = walk_timeline(tl, instance_name=active, speed="instant",
                                llm_client_factory=llm_client_factory,
                                start_index=st.session_state["sim_last_idx"])
            try:
                while True:
                    chunk = next(gen, None)
                    if chunk is None:
                        terminal = {"event": "done", "committed": []}
                        break
                    if chunk["event"] == "done":
                        terminal = chunk
                        break
                    key = (chunk.get("t"), chunk.get("event"))
                    if key in st.session_state["sim_seen"]:
                        if chunk["event"] in ("ingest", "recovery"):
                            break
                        continue
                    st.session_state["sim_seen"].add(key)
                    if chunk["event"] == "recovery":
                        status = chunk.get("status", "?")
                        mark = ("✓ COMMITTED" if status == "COMMITTED"
                                else f"✗ {status}")
                        line = (f"[t={chunk['t']:>4}] recovery "
                                f"{chunk.get('kind', '')} — {mark}")
                        st.session_state["sim_last_idx"] = chunk["idx"] + 1
                    elif chunk["event"] == "recovery_start":
                        who = ("live LLM" if chunk.get("live")
                               else "auto-fix (no LLM)")
                        line = (f"[t={chunk['t']:>4}] ⏳ recovery starting "
                                f"({who}) — solving…")
                    elif chunk["event"] == "auto_recover":
                        line = f"[t={chunk['t']:>4}] auto_recover"
                    else:
                        line = (f"[t={chunk['t']:>4}] {chunk['event']} "
                                f"{chunk.get('kind', '')}".rstrip())
                        st.session_state["sim_last_idx"] = chunk["idx"] + 1
                        st.session_state["sim_clock"] = chunk["t"]
                    st.session_state["sim_feed"].append(line)
                    _flush()
                    if chunk["event"] not in ("recovery_start",
                                              "auto_recover"):
                        break         # terminator → return control
            finally:
                try:
                    gen.close()
                except Exception:
                    pass
            # auto-advance: one dwell, then a fresh rerender drives the
            # next slice (Bug 1 dies — no single green button press
            # needed per event).
            if terminal is None and not st.session_state.get("sim_paused"):
                import time

                time.sleep(min(_walk_paced_seconds(speed), 2.0))
                st.rerun()

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
