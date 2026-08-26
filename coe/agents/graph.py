# coe/agents/graph.py
"""LangGraph assembly + recovery runner (spec §3.1, §3.3, §7).

Fixed linear pipeline, one bounded negotiation sub-loop, two bounded
material-reactive back-edges sharing STRATEGY_MAX_ROUNDS via round_count
plus a single-intervention-pass guard. LLM nodes: translate / strategy /
explain only (criterion 11).
"""
import time

from langgraph.graph import END, START, StateGraph

from coe.agents.nodes.explain import make_explain_node
from coe.agents.nodes.investigate import (
    inventory_agent_node,
    machine_agent_node,
    production_agent_node,
    worker_agent_node,
)
from coe.agents.nodes.manager import run_manager_compile
from coe.agents.nodes.strategy import run_strategy_round
from coe.agents.nodes.translate import TranslationFailed, run_translate
from coe.agents.runs import InstanceRunLock, RunLockTimeout, record_run, write_proposals
from coe.agents.safety import run_gate, verify_commit
from coe.agents.state import RecoveryState
from coe.config import get_settings


def _solve_for_test(payload: dict) -> dict:
    """Indirection over engine.solve: the monkeypatch seam Task 18 uses to
    stub CP-SAT in end-to-end tests."""
    from coe.solver.engine import solve

    return solve(payload)


def _ingest_node(state: RecoveryState) -> RecoveryState:
    """§3.1 `ingest` node for BOTH entry points: MQTT uses the wire
    message_id; CLI derives cli-{hash} (criterion 13). Idempotent."""
    from coe.agents.nodes.translate import run_ingest

    return run_ingest(state)


def make_solve_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.cli import _recovery_floor

        payload = dict(state.compiled_payload)
        cfg = dict(payload["config"])
        cfg["time_limit_seconds"] = _recovery_floor(
            cfg["time_limit_seconds"])
        payload["config"] = cfg
        solution = _solve_for_test(payload)

        update = {"solution": solution, "compiled_payload": payload}
        if solution["status"] in ("INFEASIBLE", "UNKNOWN"):
            had_shortfall = any(
                w.get("type") == "MATERIAL_SHORTFALL"
                for w in payload.get("warnings", []))
            update["solve_infeasible_material"] = had_shortfall
            update["material_reactive"] = had_shortfall
        return state.model_copy(update=update)

    return _node


def make_gate_node():
    def _node(state: RecoveryState) -> RecoveryState:
        result = run_gate(state.compiled_payload, state.solution)
        return state.model_copy(update={"gate_result": result})

    return _node


def make_commit_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.db.models.provenance import Instance
        from coe.db.session import session_scope
        from coe.solver.committer import commit_solution

        with session_scope() as session:
            inst = (session.query(Instance)
                    .filter(Instance.name == state.instance_name).one())
            version = commit_solution(
                session, instance_row=inst,
                payload=state.compiled_payload, solution=state.solution,
                now=state.reference_clock)
            vid = version.id
        return state.model_copy(update={"committed_version_id": vid})

    return _node


def make_verify_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.solver.committer import RollbackFloor

        try:
            result = verify_commit(state.instance_name)
        except RollbackFloor as exc:
            result = {"passed": True, "violations": [str(exc)],
                      "version_number": None, "rolled_back_from": None}
            state = state.model_copy(update={
                "errors": state.errors + [f"verifier floor: {exc}"]})
        return state.model_copy(update={"verify_result": result})

    return _node


def build_graph(client, *, max_retries: int | None = None):
    """Assemble §3.1. ``max_retries`` threads the runner's override into
    every LLM-retry loop (translate/strategy); None = settings default."""

    def translate_node(state):
        return run_translate(state, client=client,
                             max_retries=max_retries)

    def strategy_node(state):
        passes = state.material_reactive_passes \
            + (1 if state.material_reactive else 0)
        out = run_strategy_round(state, client=client,
                                 max_retries=max_retries)
        return out.model_copy(update={"material_reactive_passes": passes})

    def route_entry(state):
        return "ingest" if state.source_message_id else "translate"

    g = StateGraph(RecoveryState)
    g.add_node("entry", lambda s: s)
    g.add_node("translate", translate_node)
    g.add_node("ingest", _ingest_node)
    g.add_node("machine_agent", machine_agent_node)
    g.add_node("production_agent", production_agent_node)
    g.add_node("inventory_agent", inventory_agent_node)
    g.add_node("worker_agent", worker_agent_node)
    g.add_node("strategy", strategy_node)
    g.add_node("manager_compile", run_manager_compile)
    g.add_node("solve_node", make_solve_node())
    g.add_node("gate_node", make_gate_node())
    g.add_node("commit_node", make_commit_node())
    g.add_node("verify_node", make_verify_node())
    g.add_node("explain_node", make_explain_node(client))

    g.add_edge(START, "entry")
    g.add_conditional_edges("entry", route_entry,
                            {"ingest": "ingest", "translate": "translate"})
    g.add_edge("translate", "ingest")
    g.add_edge("ingest", "machine_agent")
    g.add_edge("machine_agent", "production_agent")
    g.add_edge("production_agent", "inventory_agent")
    g.add_edge("inventory_agent", "worker_agent")
    g.add_edge("worker_agent", "strategy")
    g.add_conditional_edges("strategy", route_after_strategy,
                            {"strategy": "strategy",
                             "manager_compile": "manager_compile"})
    g.add_conditional_edges("manager_compile", route_after_compile,
                            {"strategy": "strategy",
                             "solve_node": "solve_node"})
    g.add_conditional_edges("solve_node", route_after_solve,
                            {"strategy": "strategy",
                             "gate_node": "gate_node", "END": END})
    g.add_conditional_edges("gate_node", route_after_gate,
                            {"commit_node": "commit_node", "END": END})
    g.add_edge("commit_node", "verify_node")
    g.add_conditional_edges("verify_node", route_after_verify,
                            {"explain_node": "explain_node", "END": END})
    g.add_edge("explain_node", END)
    return g.compile()


# ---- module-level routers (pure; unit-testable without building the graph).
# They return the literal string "END"; build_graph's path maps translate it
# to langgraph's END sentinel ("__end__").


def route_after_strategy(state: RecoveryState) -> str:
    max_rounds = get_settings().strategy_max_rounds
    if state.strategy_final or state.round_count >= max_rounds:
        return "manager_compile"
    return "strategy"


def route_after_compile(state: RecoveryState) -> str:
    max_rounds = get_settings().strategy_max_rounds
    if (state.material_reactive and state.material_reactive_passes == 0
            and state.round_count < max_rounds):
        return "strategy"                        # back-edge 1
    return "solve_node"


def route_after_solve(state: RecoveryState) -> str:
    status = (state.solution or {}).get("status")
    if status in ("OPTIMAL", "FEASIBLE"):
        return "gate_node"
    max_rounds = get_settings().strategy_max_rounds
    if (state.solve_infeasible_material
            and state.material_reactive_passes == 0
            and state.round_count < max_rounds):
        return "strategy"                        # back-edge 2
    return "END"                                 # SOLVE_INFEASIBLE terminal


def route_after_gate(state: RecoveryState) -> str:
    return "commit_node" if state.gate_result["passed"] else "END"


def route_after_verify(state: RecoveryState) -> str:
    if state.verify_result["passed"]:
        return "explain_node"
    return "END"                                 # VERIFIER_ROLLBACK


def _terminal_status(state: RecoveryState) -> str:
    """Terminal run-status decision order (§3.3): SOLVE_INFEASIBLE →
    GATE_FAILED → VERIFIER_ROLLBACK → COMMITTED. Pure; unit-testable.
    VERIFIER_ROLLBACK keys off ``passed`` so it covers both the violation
    case and verify_commit's no-committed-version degenerate."""
    if (state.solution or {}).get("status") in ("INFEASIBLE", "UNKNOWN"):
        return "SOLVE_INFEASIBLE"        # UNKNOWN mapped; see report notes
    if not (state.gate_result or {}).get("passed"):
        return "GATE_FAILED"
    if not (state.verify_result or {}).get("passed", True):
        return "VERIFIER_ROLLBACK"
    return "COMMITTED"


def execute_recovery(instance_name: str, *, trigger: str,
                     narrative: str | None = None,
                     record: dict | None = None,
                     source_message_id: str | None = None,
                     reference_clock: int | None = None,
                     client=None, lock_wait: float | None = None,
                     max_retries: int | None = None) -> dict:
    """One full graph execution under the per-instance lock (§7).

    Records exactly one recovery_runs row per invocation (criterion 8),
    flushing buffered proposals even on failure paths.
    """
    started = time.time()
    if client is None:
        from coe.agents.llm_client import make_llm_client

        client = make_llm_client()

    initial = RecoveryState(
        instance_name=instance_name, trigger=trigger,
        narrative=narrative or "", disruption_record=record,
        source_message_id=source_message_id,
        reference_clock=reference_clock)

    app = build_graph(client, max_retries=max_retries)
    status = "COMMITTED"
    try:
        with InstanceRunLock(instance_name, wait_seconds=lock_wait):
            # langgraph hands back a plain channel dict; re-validate so
            # callers always get a typed RecoveryState.
            final_state = RecoveryState.model_validate(app.invoke(initial))
    except TranslationFailed as exc:
        final_state = initial
        status = "TRANSLATION_FAILED"
        record_json = {"narrative": exc.narrative,
                       "validation_error": exc.error}
        if source_message_id is not None:
            record_json["message_id"] = source_message_id   # §3.4 dedup key
    except RunLockTimeout:
        raise  # lock contention must propagate to caller
    except Exception as exc:
        final_state = initial
        status = "STREAMING_ERROR"
        record_json = {"error": f"{type(exc).__name__}: {exc}"}
        if source_message_id is not None:
            record_json["message_id"] = source_message_id
    else:
        status = _terminal_status(final_state)

        rec = final_state.disruption_record or {}
        record_json = dict(rec)
        if source_message_id is not None:
            record_json["message_id"] = source_message_id   # §3.4 dedup key

    run_id = record_run(
        instance_name, trigger=trigger, status=status,
        disruption_record_json=record_json, started_at=started,
        finished_at=time.time(),
        final_status_version_id=getattr(final_state,
                                        "committed_version_id", None))
    verdicts = getattr(final_state, "round_verdicts", [])
    if verdicts:
        write_proposals(instance_name, run_id, verdicts)
    return {"status": status, "state": final_state, "run_id": run_id}


def execute_recovery_streaming(instance_name: str, *, trigger: str,
                               narrative: str | None = None,
                               record: dict | None = None,
                               source_message_id: str | None = None,
                               reference_clock: int | None = None,
                               client=None, lock_wait: float | None = None,
                               max_retries: int | None = None):
    """Streaming twin of execute_recovery (dashboard design §4 Cockpit).

    Yields {'node': <name>} at each LangGraph update boundary, then a
    single terminal dict with the same shape execute_recovery returns.
    Recording semantics are identical: exactly one recovery_runs row,
    proposals flushed even on failure paths.
    """
    import time as _time

    started = _time.time()
    if client is None:
        from coe.agents.llm_client import make_llm_client

        client = make_llm_client()

    initial = RecoveryState(
        instance_name=instance_name, trigger=trigger,
        narrative=narrative or "", disruption_record=record,
        source_message_id=source_message_id,
        reference_clock=reference_clock)

    app = build_graph(client, max_retries=max_retries)
    status = "COMMITTED"
    final_state = initial
    try:
        with InstanceRunLock(instance_name, wait_seconds=lock_wait):
            for chunk in app.stream(initial, stream_mode="updates"):
                for node in chunk:
                    yield {"node": node}
                # Accumulate onto the previous final_state instead of
                # resetting from initial: nodes currently return full
                # RecoveryStates, but if one ever returns a langgraph-style
                # partial dict, fields set by earlier chunks must survive.
                merged = final_state
                for node, upd in chunk.items():
                    if upd is not None:
                        merged = merged.model_copy(update=dict(upd))
                final_state = merged
    except TranslationFailed as exc:
        final_state = initial
        status = "TRANSLATION_FAILED"
        record_json = {"narrative": exc.narrative,
                       "validation_error": exc.error}
        if source_message_id is not None:
            record_json["message_id"] = source_message_id   # §3.4 dedup key
    except RunLockTimeout:
        raise  # lock contention must propagate to caller
    except Exception as exc:
        final_state = initial
        status = "STREAMING_ERROR"
        record_json = {"error": f"{type(exc).__name__}: {exc}"}
        if source_message_id is not None:
            record_json["message_id"] = source_message_id
        run_id = record_run(
            instance_name, trigger=trigger, status=status,
            disruption_record_json=record_json, started_at=started,
            finished_at=_time.time(),
            final_status_version_id=None)
        yield {"status": status, "state": str(exc), "run_id": run_id}
        return
    else:
        status = _terminal_status(final_state)

        rec = final_state.disruption_record or {}
        record_json = dict(rec)
        if source_message_id is not None:
            record_json["message_id"] = source_message_id   # §3.4 dedup key

    run_id = record_run(
        instance_name, trigger=trigger, status=status,
        disruption_record_json=record_json, started_at=started,
        finished_at=_time.time(),
        final_status_version_id=getattr(final_state,
                                        "committed_version_id", None))
    verdicts = getattr(final_state, "round_verdicts", [])
    if verdicts:
        write_proposals(instance_name, run_id, verdicts)
    yield {"status": status, "state": final_state, "run_id": run_id}
