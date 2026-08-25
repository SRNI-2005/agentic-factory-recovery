# coe/agents/nodes/explain.py
"""Post-hoc explanation service (AI Role 3, spec §4.5).

Strictly read-side: computes a deterministic diff of the committed version
vs its parent, hands it to the LLM for prose, stores the rationale. Output
never influences scheduling state. Baseline versions (no parent) get a
constraint-summary mode instead of a diff.
"""
import json

from sqlalchemy.orm import Session

from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine

_SYSTEM_PROMPT = """You explain factory schedule changes to a production \
planner. Input: JSON describing the previous vs new schedule plus \
constraint highlights. Output: plain-text rationale, <=150 words, naming \
the concrete causes (failed resources, strategies applied, clipped \
windows) and their operational consequences. No preamble."""


def _names_and_ops(session, iid):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.workers import Worker

    names = {
        "machines": dict(session.query(Machine.id, Machine.name)
                         .filter(Machine.instance_id == iid)
                         .order_by(Machine.id).all()),
        "workers": dict(session.query(Worker.id, Worker.name)
                        .filter(Worker.instance_id == iid)
                        .order_by(Worker.id).all()),
    }
    jobs = dict(session.query(Job.id, Job.name)
                .filter(Job.instance_id == iid).order_by(Job.id).all())
    op_meta = {o.id: (jobs[o.job_id], o.sequence_number)
               for o in session.query(Operation)
               .filter(Operation.instance_id == iid)
               .order_by(Operation.job_id,
                         Operation.sequence_number).all()}
    return names, op_meta


def _entry_index(entries, names, op_meta):
    idx = {}
    for e in entries:
        jname, seq = op_meta[e.operation_id]
        idx[f"{jname}-O{seq}"] = {
            "machine_id": names["machines"][e.machine_id],
            "worker_id": (names["workers"].get(e.worker_id)
                          if e.worker_id else None),
            "start": e.start_time, "end": e.end_time}
    return idx


def compute_diff(session: Session, version, parent) -> dict:
    from coe.db.models.schedule import ScheduleEntry

    iid = version.instance_id
    names, op_meta = _names_and_ops(session, iid)
    payload = version.payload_json or {}
    diff: dict = {"moved_operations": [], "reassigned_workers": [],
                  "newly_blocked": [],
                  "applied_strategies": [
                      w for w in payload.get("warnings", [])
                      if w.get("type") == "STRATEGY_APPLIED"],
                  "clipped_windows": [
                      w for w in payload.get("warnings", [])
                      if w.get("type") in ("DOWNTIME_CLIPPED",
                                           "DOWNTIME_DROPPED",
                                           "WORKER_WINDOW_CLIPPED",
                                           "WORKER_WINDOW_DROPPED")]}
    if parent is None:
        return diff

    child_entries = (session.query(ScheduleEntry)
                     .filter(ScheduleEntry.version_id == version.id)
                     .order_by(ScheduleEntry.id).all())
    parent_entries = (session.query(ScheduleEntry)
                      .filter(ScheduleEntry.version_id == parent.id)
                      .order_by(ScheduleEntry.id).all())
    old_idx = _entry_index(parent_entries, names, op_meta)
    new_idx = _entry_index(child_entries, names, op_meta)
    blocked_now = {b["operation_id"]
                   for b in payload.get("blocked_operations", [])}

    for oid, new in sorted(new_idx.items()):
        old = old_idx.get(oid)
        if old is None:
            continue
        if (new["machine_id"], new["start"]) != (old["machine_id"],
                                                 old["start"]):
            diff["moved_operations"].append({
                "operation_id": oid,
                "from": {"machine_id": old["machine_id"],
                         "start": old["start"]},
                "to": {"machine_id": new["machine_id"],
                       "start": new["start"]}})
        if new["worker_id"] != old["worker_id"]:
            diff["reassigned_workers"].append({
                "operation_id": oid, "from": old["worker_id"],
                "to": new["worker_id"]})
    for oid in sorted(blocked_now):
        if oid in old_idx and oid not in new_idx:
            diff["newly_blocked"].append(oid)
    return diff


def _constraint_summary(version) -> dict:
    payload = version.payload_json or {}
    return {"failed_machines": payload.get("failed_machines") or [],
            "suspended_jobs": payload.get("suspended_jobs") or [],
            "objective": {"makespan": version.makespan,
                          "total_tardiness": version.total_tardiness,
                          "status": version.solver_status}}


def explain_version(instance_name: str, *, client,
                    max_retries: int | None = None) -> str | None:
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.models.schedule import ScheduleVersion

    s = get_settings()
    retries = (s.llm_max_retries if max_retries is None else max_retries)
    with Session(make_engine()) as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = (session.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst.id,
                           ScheduleVersion.solver_status.in_(("OPTIMAL",
                                                              "FEASIBLE")),
                           ScheduleVersion.rolled_back.is_(False))
                   .order_by(ScheduleVersion.version_number.desc(),
                             ScheduleVersion.id.desc()).first())
        if version is None:
            return None
        parent = (session.query(ScheduleVersion)
                  .filter(ScheduleVersion.id
                          == version.parent_version_id).one_or_none())
        diff = compute_diff(session, version, parent)
        summary = _constraint_summary(version)
        version_number = version.version_number

    feedback, prose, last_error = "", None, ""
    for _attempt in range(1 + retries):
        user = json.dumps({"diff": diff, "constraints": summary},
                          sort_keys=True)
        try:
            candidate = client.complete(system=_SYSTEM_PROMPT,
                                        user=user + feedback)
            if not candidate or not candidate.strip():
                raise ValueError("empty explanation")
            prose = candidate
            break
        except Exception as exc:
            last_error = str(exc)
            feedback = f"\n\nPrevious attempt failed: {last_error}"
    if prose is None:
        print(f"[explain] LLM failed after retries: {last_error} — "
              "explanation logged missing (§3.3)")
        return None

    with Session(make_engine()) as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = (session.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst.id,
                           ScheduleVersion.version_number
                           == version_number).one())
        existing = (session.query(ScheduleExplanation)
                    .filter(ScheduleExplanation.version_id
                            == version.id).one_or_none())
        if existing is not None:
            existing.rationale = prose
        else:
            session.add(ScheduleExplanation(
                instance_id=inst.id, version_id=version.id,
                rationale=prose))
        session.commit()
        return prose


def make_explain_node(client):
    """Graph adapter: closes the injected LLMClient into a node."""

    def _node(state: RecoveryState) -> RecoveryState:
        prose = explain_version(state.instance_name, client=client)
        return state.model_copy(update={"explanation": prose})

    return _node
