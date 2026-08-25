"""Investigation nodes (spec §4.2): reproducible DB queries, zero LLM.

All four run for every record (fixed pipeline); each no-ops (writes a None
placeholder) when the record's kind does not concern it. Every collection
query carries ORDER BY (repo determinism rule).
"""
from sqlalchemy.orm import Session

from coe.agents.state import RecoveryState
from coe.db.session import make_engine
from coe.solver.identifier import op_id


def _session():
    return Session(make_engine())


def _inst(session, name):
    from coe.db.models.provenance import Instance

    return (session.query(Instance)
            .filter(Instance.name == name).one())


def _record_of(state: RecoveryState) -> dict | None:
    return state.disruption_record


def _merge(state: RecoveryState, **facts) -> RecoveryState:
    merged = dict(state.db_facts)
    merged.update(facts)
    return state.model_copy(update={"db_facts": merged})


def _active_snapshot(session, iid):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    version = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == iid,
                ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                ScheduleVersion.rolled_back.is_(False))
        .order_by(ScheduleVersion.version_number.desc(),
                  ScheduleVersion.id.desc()).first())
    if version is None:
        return None, []
    entries = (
        session.query(ScheduleEntry)
        .filter(ScheduleEntry.version_id == version.id)
        .order_by(ScheduleEntry.id).all())
    return version, entries


def _name_maps(session, iid):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.workers import Worker

    return {
        "machines": dict(session.query(Machine.id, Machine.name)
                         .filter(Machine.instance_id == iid)
                         .order_by(Machine.id).all()),
        "workers": dict(session.query(Worker.id, Worker.name)
                        .filter(Worker.instance_id == iid)
                        .order_by(Worker.id).all()),
        "jobs": dict(session.query(Job.id, Job.name)
                     .filter(Job.instance_id == iid)
                     .order_by(Job.id).all()),
        "ops": {o.id: o for o in session.query(Operation)
                .filter(Operation.instance_id == iid)
                .order_by(Operation.job_id, Operation.sequence_number).all()},
    }


def machine_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    if rec is None or rec["kind"] != "MACHINE":
        return _merge(state, failed_machine=None)
    with _session() as session:
        from coe.db.models.fjsp import Machine, MachineCapability

        inst = _inst(session, state.instance_name)
        m = (session.query(Machine)
             .filter(Machine.instance_id == inst.id,
                     Machine.name == rec["machine_id"]).one())
        caps = [c.capability_code for c in
                session.query(MachineCapability)
                .filter(MachineCapability.instance_id == inst.id,
                        MachineCapability.machine_id == m.id)
                .order_by(MachineCapability.capability_code).all()]
        return _merge(state, failed_machine={
            "machine_id": m.name, "status": m.status,
            "capabilities_lost": caps})


def _entry_overlaps_future(entries, *, names, clock, machine=None,
                           worker=None):
    """Active-version entries not finished at clock, optionally filtered."""
    out = []
    for e in sorted(entries, key=lambda x: (x.start_time, x.id)):
        if e.end_time <= clock:
            continue
        if machine is not None and names["machines"][e.machine_id] != machine:
            continue
        if worker is not None:
            if e.worker_id is None or names["workers"][e.worker_id] != worker:
                continue
        out.append(e)
    return out


def _serialize_stranded(entries, *, session, names, iid) -> list[dict]:
    from coe.db.models.fjsp import Job

    deadlines = dict(session.query(Job.id, Job.deadline)
                     .filter(Job.instance_id == iid)
                     .order_by(Job.id).all())
    return [{
        "operation_id": op_id(names["jobs"][names["_op_job"][e.operation_id]],
                              names["_op_seq"][e.operation_id]),
        "job_id": names["jobs"][names["_op_job"][e.operation_id]],
        "deadline": deadlines[names["_op_job"][e.operation_id]],
        "machine_id": names["machines"][e.machine_id],
        "start": e.start_time, "end": e.end_time,
    } for e in entries]


def _with_op_meta(names, ops) -> dict:
    names = dict(names)
    names["_op_job"] = {o.id: o.job_id for o in ops}
    names["_op_seq"] = {o.id: o.sequence_number for o in ops}
    return names


def production_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    if rec is None:
        return _merge(state, stranded_operations=[])
    with _session() as session:
        inst = _inst(session, state.instance_name)
        _, entries = _active_snapshot(session, inst.id)
        names = _name_maps(session, inst.id)
        names = _with_op_meta(names, names["ops"].values())

        if rec["kind"] == "MACHINE":
            hit = _entry_overlaps_future(
                entries, names=names, clock=state.reference_clock,
                machine=rec["machine_id"])
        elif rec["kind"] == "WORKER":
            hit = _entry_overlaps_future(
                entries, names=names, clock=state.reference_clock,
                worker=rec["worker_id"])
        else:
            hit = []
        return _merge(state,
                      stranded_operations=_serialize_stranded(
                          hit, session=session, names=names, iid=inst.id))


def inventory_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    with _session() as session:
        from coe.db.models.provenance import Instance as Inst

        inst = _inst(session, state.instance_name)

        # Projected horizon: preview BASELINE payload through the real P2
        # builder (pure read; gives the exact horizon the solver will see).
        from coe.solver.payload_builder import build_payload

        payload = build_payload(
            session, instance_row=session.query(Inst)
            .filter(Inst.id == inst.id).one(),
            alpha=1.0, beta=1.0, time_limit_seconds=1)
        horizon = max([op["frozen"]["end"]
                       for j in payload["jobs"]
                       for op in j["operations"]
                       if op.get("frozen")] + [0]) or \
            _fallback_horizon(payload)

        evidence = None
        if rec is not None and rec["kind"] == "MATERIAL":
            evidence = _shortage_evidence(session, inst.id,
                                          rec["material_sku"])
        return _merge(state, projected_horizon=horizon,
                      shortage_evidence=evidence)


def _fallback_horizon(payload: dict) -> int:
    """Conservative span estimate when no frozen anchors exist: latest
    release plus the longest remaining chain of max-duration ops."""
    def chain(j):
        rel = j["release_time"]
        total = sum(max((a["processing_time"] for a in o["alternatives"]),
                        default=0) for o in j["operations"])
        return rel + total

    return max((chain(j) for j in payload["jobs"]), default=0) + 1


def _shortage_evidence(session, iid, sku) -> dict:
    from coe.db.models.fjsp import Operation
    from coe.db.models.materials import Material, MaterialReceipt, OperationBom

    stock = (session.query(Material.initial_stock)
             .filter(Material.instance_id == iid, Material.sku == sku)
             .scalar())
    receipts = (session.query(MaterialReceipt.quantity)
                .join(Material, Material.id == MaterialReceipt.material_id)
                .filter(MaterialReceipt.instance_id == iid,
                        Material.sku == sku)
                .order_by(MaterialReceipt.available_at).all())
    total_supply = (stock or 0) + sum(q for (q,) in receipts)
    rows = (session.query(OperationBom, Operation)
            .join(Operation, Operation.id == OperationBom.operation_id)
            .join(Material, Material.id == OperationBom.material_id)
            .filter(OperationBom.instance_id == iid, Material.sku == sku)
            .order_by(Operation.job_id, Operation.sequence_number).all())
    total_demand = 0
    affected = []
    from coe.db.models.fjsp import Job

    job_names = dict(session.query(Job.id, Job.name)
                     .filter(Job.instance_id == iid).order_by(Job.id).all())
    for bom, op in rows:
        total_demand += bom.quantity_required
        affected.append(op_id(job_names[op.job_id], op.sequence_number))
    return {"material_sku": sku, "total_supply": total_supply,
            "total_demand": total_demand, "affected_operations": affected}


def worker_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    if rec is None or rec["kind"] != "WORKER":
        return _merge(state, absent_worker=None)
    with _session() as session:
        from coe.db.models.workers import (
            OperationMachineWorkerTime,
            Worker,
        )

        inst = _inst(session, state.instance_name)
        w = (session.query(Worker)
             .filter(Worker.instance_id == inst.id,
                     Worker.name == rec["worker_id"]).one())
        rows = (session.query(OperationMachineWorkerTime)
                .filter(OperationMachineWorkerTime.instance_id == inst.id,
                        OperationMachineWorkerTime.worker_id == w.id)
                .order_by(OperationMachineWorkerTime.operation_id,
                          OperationMachineWorkerTime.machine_id).all())
        names = _name_maps(session, inst.id)
        names = _with_op_meta(names, names["ops"].values())
        sole = []
        for r in rows:
            others = (session.query(OperationMachineWorkerTime.worker_id)
                      .filter(
                          OperationMachineWorkerTime.instance_id == inst.id,
                          OperationMachineWorkerTime.operation_id
                          == r.operation_id,
                          OperationMachineWorkerTime.machine_id
                          == r.machine_id)
                      .order_by(OperationMachineWorkerTime.worker_id).all())
            if len(others) == 1:
                sole.append({
                    "operation_id": op_id(
                        names["jobs"][names["_op_job"][r.operation_id]],
                        names["_op_seq"][r.operation_id]),
                    "machine_id": names["machines"][r.machine_id]})
        _, entries = _active_snapshot(session, inst.id)
        mine = _entry_overlaps_future(entries, names=names,
                                      clock=state.reference_clock,
                                      worker=w.name)
        return _merge(state, absent_worker={
            "worker_id": w.name, "sole_eligible": sole,
            "assignment_count": len(mine)})
