"""Simulate page — live-day + scripted-day playback with pacing controls.

Test seams: a *staged* chat submission may be injected via
``st.session_state["sim_chat_text"]`` and a *staged* Run/Resume press via
``st.session_state["sim_run_pressed"]`` before ``render()``; the production
paths are the page's own ``st.chat_input`` / Resume button (§6). Staged
values are consumed exactly as real input would be.
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
    st.caption("Initial (baseline)")
    st.plotly_chart(frames[0], use_container_width=True)
    st.caption("Final (after the last recovery)")
    st.plotly_chart(frames[-1], use_container_width=True)


def _jobs_palette(active: str) -> dict[str, str]:
    """One colour per JOB (same convention as the Configure page)."""
    # stable hash → hue within the dashboard palette set used before
    palette_ids = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd",
                   "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22",
                   "#17becf", "#aec7e8", "#ffbb78", "#98df8a",
                   "#ff9896", "#c5b0d5", "#c5b0d5", "#c49c94"]
    out: dict[str, str] = {}
    for i, job in enumerate(sorted({e["job_name"]
                                    for e in _fetch_active_entries(active)})):
        out[job] = palette_ids[i % len(palette_ids)]
    return out


def _paint_board(active: str, t: int, slot=None) -> None:
    """[gantt] — tree-stable board stanza (spec §3, A3 §11.3).

    The clock hero is `_render_day(t)` (shared by both lanes); this
    paints ONE st.plotly_chart per pass at a constant path (unique
    key — the duplicate-ID crash fix 2026-09-19). Writes go through a
    stable-path slot when given (all post-walk; staleness fix). Between
    boundaries only the bar colours/state arrays change.
    """
    import streamlit as st

    from coe.dashboard.gantt import build_board_figure

    entries = _fetch_active_entries(active)
    fig = build_board_figure(entries, int(t), _jobs_palette(active))
    if fig is not None:
        (slot or st).plotly_chart(fig, use_container_width=True,
                                  key="sim_gantt")
    else:
        (slot or st).empty()          # keep the gantt slot in the tree


def _board_state_line(instance: str, t: int, detail: str) -> str:
    """Unified log schema (spec §10): ``[t=<4>] done=N running=M · detail``.

    done/running come from the SAME entry classification the board
    paints (``classify`` over ``_fetch_active_entries``, read-only); a
    per-pass session memo keyed by (instance, t) caps the helper at ONE
    query per pass (memo is reset each render pass in ``render()``).
    """
    import streamlit as st

    from coe.dashboard.gantt import classify

    memo = st.session_state.setdefault("sim_state_memo", {})
    key = (instance, int(t))
    if key not in memo:
        memo[key] = classify(_fetch_active_entries(instance), int(t))
    cls = memo[key]
    prefix = (f"[t={int(t):>4}] done={len(cls['completed'])} "
              f"running={len(cls['in_progress'])}")
    detail = (detail or "").strip()
    return f"{prefix} · {detail}" if detail else prefix


def _feed_line(chunk: dict, instance: str | None = None) -> str:
    """Lane feed line. WITH an instance (all live/append sites route
    through it) every line carries the board's done/running numbers
    (spec §10 unified schema); tick lines drop the word "tick" — the
    state numbers ARE the detail."""
    ev = chunk.get("event", "?")
    t = chunk.get("t", "—")
    if ev == "tick":
        if instance is not None:
            return _board_state_line(instance, t, "")
        return (f"[t={t:>4}] tick — done={chunk.get('completed', '?')} "
                f"running={chunk.get('in_progress', '?')}")
    if ev == "recovery_start":
        who = "live LLM" if chunk.get("live") else "auto-fix (no LLM)"
        detail = f"⏳ recovery starting ({who}) — solving…"
    elif ev == "recovery":
        status = chunk.get("status", "?")
        mark = ("✓ COMMITTED" if status == "COMMITTED" else f"✗ {status}")
        detail = f"recovery NARRATIVE — {mark}"
    elif ev == "day_end":
        detail = "day_end"
    else:
        detail = str(ev)
    if instance is None:
        return f"[t={t:>4}] {detail}"
    return _board_state_line(instance, t, detail)


_LOG_TAIL = 10
_LOG_VIEWPORT = 280          # fits 10 tight rows without inner scrolling


def _paint_idle_feed(lane: str | None,
                     caption: str | None,
                     full: bool = False) -> None:
    """THE single event-log surface (bug 2026-09-16 fix).

    Plain markdown, painted in every state; both lanes call only this.

    LANE-SCOPED feed (bug 2026-09-18): the painted list is the lane's
    own — ``sim_feed_live`` or ``sim_feed_scripted`` — so switching
    modes shows each lane's own log. ``lane=None`` reads the legacy
    shared ``sim_feed`` key (pre-lane callers only; no production
    caller remains).

    TREE-STABLE paint (double-box bug 2026-09-17): the SAME element
    sequence is emitted in EVERY state — caption slot, count caption,
    fixed-height container, content slot — identical element count and
    identical container height. Only the string CONTENTS differ (tail
    vs full log; hint text or ""). Conditional elements *above* the box
    (chat input drawn only mid-walk, info bar only on pause, count
    caption only when truncated) shifted the box's element path per
    state, and Streamlit's positional reconcile then left an old copy
    of the box in the DOM. With a constant tree, every run replaces
    cleanly.

    Render-cost split: WHILE a walk runs (page re-executes per dwell)
    the paint is the LAST ``_LOG_TAIL`` lines (constant size, capped);
    paused / complete / fresh paint the FULL history — scrollback
    restored, nothing lost.
    """
    import streamlit as st

    slots = (st.session_state.get("sim_wave_slots_" + lane)
             if lane else None) or {}
    hint = slots.get("log_hint") or st.empty()
    count = slots.get("log_count") or st.empty()
    if lane is not None:
        feed = st.session_state.get("sim_feed_" + lane) or []
    else:
        feed = st.session_state.get("sim_feed") or []
    shown = feed if full else feed[-_LOG_TAIL:]
    hint.caption(caption or "")
    count.caption(
        f"showing last {len(shown)} of {len(feed)} events"
        + (" — full history" if full else ""))
    slots_box = slots.get("log_box")
    if slots_box is not None:
        slots_box.markdown("  \n".join(shown) or "no events yet")
        return
    with st.container(height=_LOG_VIEWPORT):
        box = st.empty()
        box.markdown("  \n".join(shown) or "no events yet")


def _event_narrative(ev) -> str:
    """Timeline event → deterministic narrative text (spec A3 §11.2).

    The authored event enters the SAME recovery path a typed disruptive
    narration would; the phrasings carry the resource id + the
    deterministic keywords the degraded translate path binds (failed /
    maintenance / absent / back / shortage / restock) so narrative-only
    replay resolves exactly like the live lane.
    """
    if ev.kind == "MACHINE" and ev.event_type == "FAILURE":
        return f"Machine {ev.machine_id} failed — total shutdown, critical"
    if ev.kind == "MACHINE":
        return (f"Machine {ev.machine_id} down for maintenance"
                f"{f' — about {ev.estimated_downtime} min' if ev.estimated_downtime else ''}")
    if ev.kind == "WORKER" and ev.event_type == "WORKER_ABSENT":
        return (f"Worker {ev.worker_id} absent — out sick"
                f"{f' for about {ev.duration} min' if ev.duration else ''}")
    if ev.kind == "WORKER":
        return f"Worker {ev.worker_id} back, returns, available again"
    if ev.kind == "MATERIAL" and ev.event_type == "MATERIAL_SHORTAGE":
        return (f"Material {ev.sku} shortage — depleted, empty"
                f"{f', need {ev.quantity}' if ev.quantity else ''}")
    if ev.kind == "MATERIAL":
        return (f"Material {ev.sku} restock delivery arrived"
                f"{f' — refill of {ev.quantity}' if ev.quantity else ''}")
    return ev.text


def _event_schedule(tl) -> list[tuple[int, str]]:
    """Authored timeline events → [(minute, narrative)] (spec A3)."""
    return [(int(ev.t), _event_narrative(ev)) for ev in tl.events]


def _wave_slots(lane: str) -> dict:
    """Per-pass stable-path slots, created FRESH every render pass in DOM
    order [hero][board][log(hint,count,box)].
    """
    import streamlit as st

    slots = {
        "hero": st.empty(),
        "board": st.empty(),
        "log_hint": st.empty(),
        "log_count": st.empty(),
        "log_box": st.empty()
    }
    st.session_state["sim_wave_slots_" + lane] = slots
    return slots


def _render_day(t: int, slot=None) -> None:
    """Clock hero metric — the SAME hero in both lanes (spec A3 §11.3);
    `_paint_board` paints the gantt under it without a clock caption.
    """
    import streamlit as st

    slot = slot or st.empty()
    slot.metric("Day clock", f"{int(t)} min")


def _ensure_walk_lane(instance_name: str, *, lane: str, label: str,
                      scheduled: list[tuple[int, str]] | None = None) -> str:
    """Create/reset a lane's walk state and return the fork to play
    (spec A3: ONE bootstrapping helper for BOTH lanes — DRY).

    Runs at mode entry EVEN when the lane is idle (a fresh render must
    not fork off a walk — only a Resume press starts one). ``lane``
    prefixes every private key (sim_live_* / sim_scripted_*); the two
    lanes differ only in the interrupt queue they own (Scheduled for
    the timeline file, plain for typed input) and the fork label.
    """
    import streamlit as st

    from coe.simulator.live import InterruptQueue, ScheduledInterruptQueue

    st.session_state.setdefault(f"sim_{lane}_complete", False)

    active_key, instance_key = f"sim_{lane}_active", f"sim_{lane}_instance"
    if (st.session_state.get(active_key) is None
            or st.session_state.get(instance_key) != instance_name):
        st.session_state[instance_key] = instance_name
        if scheduled:
            queue = ScheduledInterruptQueue(scheduled)
        else:
            queue = InterruptQueue()
        st.session_state[f"sim_{lane}_interrupt_q"] = queue
        st.session_state["sim_seen"] = set()
        st.session_state[f"sim_feed_{lane}"] = []
        st.session_state[f"sim_{lane}_clock"] = 0
        st.session_state[f"sim_{lane}_complete"] = False
        st.session_state[f"sim_{lane}_paused"] = False
        st.session_state[f"sim_{lane}_paused"] = False
        # a fresh day must not inherit a stale running flag (or it would
        # auto-walk before any Resume press)
        if st.session_state.get("sim_running_lane") == lane:
            st.session_state["sim_running"] = False
            st.session_state["sim_running_lane"] = None
        try:
            active = _fork_or_default(instance_name, label)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        st.session_state[active_key] = active
        st.session_state[f"sim_{lane}_before_entries"] = \
            _fetch_active_entries(active)
    return st.session_state[active_key]


def _render_live(instance_name: str, llm_client_factory, speed,
                 *, lane: str = "live") -> None:
    """Lane walk (spec §6 / A3) — caller has validated run/pause state.
    ONE walker-render for BOTH lanes: all private state keys are
    ``sim_{lane}_*``; the live lane passes lane="live", the scripted
    lane calls the SAME function with its own fork + scheduled queue.

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

    from coe.simulator.live import LiveDayError, live_day

    active = st.session_state[f"sim_{lane}_active"]
    st.session_state["sim_running"] = True
    st.session_state["sim_running_lane"] = lane

    # chat capture moved to render()'s live branch (TREE-STABLE: the
    # input must be present in EVERY state, not only mid-walk).

    # No status container at all: a "Simulating day…" st.status is itself
    # a collapsible dropdown that replays open/close presentation on
    # every paced rerender. The painted log is the walk's feedback.
    def _record(chunk: dict) -> None:
        key = (chunk.get("t"), chunk.get("event"))
        if key in st.session_state["sim_seen"]:
            return
        st.session_state["sim_seen"].add(key)
        if (chunk.get("event") == "recovery"
                and chunk.get("status") == "COMMITTED"):
            # FINAL-REVIEW FIX 2': the board changed — clear BEFORE the
            # line's _board_state_line (via _feed_line) so the COMMITTED
            # line reads post-commit state.
            st.session_state["sim_state_memo"] = {}
        st.session_state[f"sim_feed_{lane}"].append(
            _feed_line(chunk, active))
        st.session_state[f"sim_{lane}_clock"] = chunk["t"]

    terminal = False
    rerender = False
    gen = live_day(active, speed="instant",
                   llm_client_factory=llm_client_factory,
                   start_clock=st.session_state[f"sim_{lane}_clock"],
                   interrupt_queue=st.session_state[
                       f"sim_{lane}_interrupt_q"])
    try:
        chunk = next(gen, None)
        while chunk is not None:
            _record(chunk)
            if chunk["event"] == "recovery_start":
                # pass-blocked solve signal (spec A3 single-paint discipline):
                # floating toast, NOT a second log container — the log
                # paints ONCE per pass, post-walk (double-box fix wave).
                who = ("live LLM" if chunk.get("live")
                       else "auto-fix (no LLM)")
                st.toast(f"⏳ recovery starting ({who}) — solving…")
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

    # A3 single-paint contract: the walker RECORDS; ONE paint wave per
    # pass — hero + board carry the POST-walk clock (stale-marker fix)
    # and the LOG paints once (double-box fix). The mid-solve state is
    # a floating toast, never a second container.
    #
    # Paint BEFORE the potential st.rerun(): rerun raises RerunException
    # and halts execution — any rendering after it is dead code in paced
    # mode.  The caller's post-walk _paint_idle_feed also never runs when
    # rerun fires, so we paint the log here too for the rerender branch.
    if terminal:
        st.session_state["sim_running"] = False
        st.session_state["sim_running_lane"] = None
        st.session_state[f"sim_{lane}_complete"] = True

    slots = st.session_state["sim_wave_slots_" + lane]
    _render_day(st.session_state[f"sim_{lane}_clock"], slots["hero"])
    _paint_board(active, st.session_state[f"sim_{lane}_clock"],
                 slots["board"])

    if rerender and not terminal:
        _paint_idle_feed(lane, None)
        _time.sleep(min(60.0 / max(int(speed), 1), 10.0))
        st.rerun()
    return terminal


def render() -> None:
    import pathlib

    import streamlit as st
    from streamlit.errors import StreamlitAPIException

    from coe.simulator.timeline import TimelineError, load_timeline

    instance_name: str | None = st.session_state.get("instance")
    if not instance_name:
        st.warning("Select an instance in the sidebar.")
        st.stop()

    # --- session-state contract ------------------------------------------
    for key, default in (
        ("sim_running", False), ("sim_running_lane", None),
        ("sim_feed_live", []), ("sim_feed_scripted", []),
        ("sim_mode", "live"),
        ("sim_seen", set()),
        ("sim_live_active", None), ("sim_live_clock", 0),
        ("sim_live_paused", False), ("sim_live_complete", False),
        ("sim_scripted_complete", False),
        ("sim_scripted_active", None), ("sim_scripted_clock", 0),
        ("sim_scripted_paused", False),
        ("sim_scripted_interrupt_q", None),
        ("sim_scripted_before_entries", None),
        ("sim_scripted_instance", None),
    ):
        st.session_state.setdefault(key, default)

    # §10 state-line memo: reset per render PASS so committed versions
    # repaint fresh numbers next pass (one query per pass, not per day)
    st.session_state["sim_state_memo"] = {}

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

    _ = None  # noqa — keep anchor
    if st.session_state["sim_mode"] == "live":
        # TREE-STABLE live lane: the following element sequence is the
        # SAME in every state — [hero metric][board][chat][log]. No
        # conditional element may appear above/between (positional
        # reconcile leaves ghost copies otherwise — 2026-09-16 bug).
        if st.session_state["sim_live_complete"]:
            # board persists on completed rerenders too (option A gap
            # fix: the early return used to skip the gantt slot, so the
            # board vanished the moment sim_complete flipped) — the
            # lane still names the played clone, so the final figure
            # repaints at its constant [clock][gantt] path; board → log
            # order stays deterministic.
            _paint_board(st.session_state["sim_live_active"],
                         st.session_state["sim_live_clock"])
            _paint_idle_feed("live",
                             "Day complete. Select a new instance to replay.",
                             full=True)
            return
        active_lane = _ensure_walk_lane(instance_name, lane="live",
                                        label="live")
        _wave_slots("live")

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
        run_pressed = (
            col_run.button(
                "Run" if st.session_state["sim_live_complete"] else "Resume",
                type="primary", key="sim_live_run",
                disabled=st.session_state["sim_live_complete"])
            or bool(st.session_state.pop("sim_run_pressed", False)))
        pause_pressed = col_step.button(
            "Pause", key="sim_live_pause",
            disabled=not live_running
            or live_paused or speed == "instant"
            or st.session_state["sim_live_complete"])

        # the input card seams NOW (creation-order slot); the queued
        # narrative pushes pre-walk so this pass's walker resolves it
        # at its authored minute. staged-first/typed-second — one seam.
        staged = st.session_state.pop("sim_chat_text", None)
        chat = st.chat_input("Describe a disruption to inject mid-flight…",
                             key="sim_live_chat")
        if staged:
            st.session_state["sim_live_interrupt_q"].push(staged)
        if chat:
            st.session_state["sim_live_interrupt_q"].push(chat)

        if pause_pressed:
            st.session_state["sim_live_paused"] = True
            st.session_state["sim_running"] = False
            st.session_state["sim_running_lane"] = None
            _paint_idle_feed("live", "Paused — press Resume.", full=True)
            return
        if run_pressed:
            # Clear the pause BEFORE any early-stop so the walk resumes
            # from sim_live_clock (same ordering as the scripted lane).
            st.session_state["sim_live_paused"] = False
        terminal = None
        if run_pressed or live_running:
            terminal = _render_live(active_lane, llm_client_factory, speed,
                                    lane="live")
        elif live_paused:
            _paint_idle_feed("live", "Paused — press Resume.", full=True)
        else:
            _paint_idle_feed("live", "Press Resume to start the live day.")
        # post-walk wave (the walker already wrote hero/board through the
        # slots): single log paint per pass at the stable paths.
        terminal = bool(terminal)
        _paint_idle_feed(
            "live",
            "Day complete. Select a new instance to replay."
            if terminal else None, full=terminal)
        if terminal:
            # day-end diff pair under the log
            if st.session_state.get("sim_live_before_entries"):
                _render_diff(active_lane,
                             st.session_state["sim_live_before_entries"])
        return

    # === SCRIPTED REPLAY (spec A3 §11): the live walker, scheduled ===
    # DRY: the scripted branch is the SAME code path as the live branch —
    # one walker (`live_day` via `_render_live`), one painter, one log —
    # with the timeline's authored events as SCHEDULED interrupts instead
    # of typed chat. The only interface difference: no chat box here (the
    # timeline import is that lane's disruption input, in the sidebar).
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
        st.error(f"Timeline rejected: {exc}")
        st.stop()
    if not tl.events:
        st.error(f"Timeline '{tl.name}' has no events.")
        st.stop()

    if st.session_state["sim_scripted_complete"]:
        _render_day(st.session_state["sim_scripted_clock"])
        _paint_board(st.session_state["sim_scripted_active"],
                     st.session_state["sim_scripted_clock"])
        _paint_idle_feed(
            "scripted",
            "Day complete. Select a new instance to replay.", full=True)
        return

    active_lane = _ensure_walk_lane(instance_name, lane="scripted",
                                    label=tl.name,
                                    scheduled=_event_schedule(tl))

    # Sidebar Run/Pause — the live branch's same two-column controls;
    # wave slots created pre-walk (DOM order [hero][board][log]).
    _wave_slots("scripted")
    col_run, col_step = st.sidebar.columns(2)
    scripted_paused = st.session_state["sim_scripted_paused"]
    scripted_running = (
        st.session_state["sim_running"]
        and st.session_state.get("sim_running_lane") == "scripted")
    run_pressed = (
        col_run.button(
            "Run" if st.session_state["sim_scripted_complete"] else "Resume",
            type="primary", key="sim_scripted_run",
            disabled=st.session_state["sim_scripted_complete"])
        or bool(st.session_state.pop("sim_run_pressed", False)))
    pause_pressed = col_step.button(
        "Pause", key="sim_pause",
        disabled=not scripted_running
        or scripted_paused or speed == "instant"
        or st.session_state["sim_scripted_complete"])

    if pause_pressed:
        st.session_state["sim_scripted_paused"] = True
        st.session_state["sim_running"] = False
        st.session_state["sim_running_lane"] = None
        _paint_idle_feed("scripted", "Paused — press Resume.", full=True)
        return
    if run_pressed:
        st.session_state["sim_scripted_paused"] = False
    terminal = None
    if run_pressed or scripted_running:
        terminal = _render_live(active_lane, llm_client_factory, speed,
                                lane="scripted")
    elif scripted_paused:
        _paint_idle_feed("scripted", "Paused — press Resume.", full=True)
    else:
        _paint_idle_feed("scripted",
                         "Press Run to start the scripted day.")
    # post-walk wave — identical stanza to the live branch (A3):
    # hero + board come from _render_live (post-walk clock, current
    # marker); the log paints once; the diff pair sits under it.
    terminal = bool(terminal)
    _paint_idle_feed(
        "scripted",
        "Day complete. Select a new instance to replay."
        if terminal else None, full=terminal)
    if terminal:
        if st.session_state.get("sim_scripted_before_entries"):
            _render_diff(active_lane,
                         st.session_state["sim_scripted_before_entries"])
    return
