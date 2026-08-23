"""Database → solver payload JSON (spec §3.1/§5, incl. both 2026-08-23
amendments). Owns ALL builder-side DB access; emits deterministic dicts.

Conventions:
- identifiers: machines/jobs/workers/materials use DB names; operations are
  synthesized "{job.name}-O{seq}".
- every collection query carries an explicit ORDER BY (repo determinism rule).
- intervals are half-open [from, until).
"""
from sqlalchemy import select

from coe.db.models.downtime import (
    MachineDowntimeWindow,
    TelemetryEvent,
    WorkerAbsenceWindow,
)
from coe.db.models.fjsp import (
    Job,
    JobFamily,
    Machine,
    Operation,
    OperationMachineAlternative,
    SetupTime,
)
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
)
from coe.solver.horizon import compute_horizon
from coe.solver.identifier import op_id
from coe.solver.materials_check import evaluate_materials
from coe.solver.windows import complement, merge_intervals


def resolve_reference_clock(session, instance_id: int, at: int | None) -> int:
    """--at wins, else latest telemetry occurred_at, else loud failure (§10)."""
    if at is not None:
        return at
    latest = (
        session.query(TelemetryEvent.occurred_at)
        .filter(TelemetryEvent.instance_id == instance_id)
        .order_by(TelemetryEvent.occurred_at.desc(), TelemetryEvent.id.desc())
        .first()
    )
    if latest is None:
        raise ValueError(
            "no reference clock: pass --at or record telemetry first "
            "(fresh scenarios have no telemetry)"
        )
    return latest[0]


def derive_tardiness_weights(jobs, beta: float) -> dict[str, float] | None:
    """Spec §3.1 (second amendment): w_j = beta·n·(p_max+1−p_j)/Σ(p_max+1−p_i),
    deadline-bearing jobs only; mean-preserving around beta; None if no job
    has a deadline."""
    weighted = [j for j in jobs if j["deadline"] is not None]
    if not weighted:
        return None
    n = len(weighted)
    p_max = max(j["priority"] for j in weighted)
    bases = [p_max + 1 - j["priority"] for j in weighted]
    total = sum(bases)
    return {
        j["job_id"]: beta * n * base / total for j, base in zip(weighted, bases)
    }


def _cascade_blocked(ops_by_job: dict[int, list[dict]],
                     material_blocks: dict[str, dict]) -> dict[str, dict]:
    """Later-sequence siblings of a blocked op inherit PREDECESSOR_BLOCKED.
    Each per-job list arrives sequence-ordered (query sorts by sequence_number),
    so a single forward pass suffices."""
    blocked = dict(material_blocks)
    for entries in ops_by_job.values():
        spreading = False
        for e in entries:
            if e["operation_id"] in blocked:
                spreading = True
            elif spreading:
                blocked[e["operation_id"]] = {"reason": "PREDECESSOR_BLOCKED",
                                              "material_sku": None}
    return blocked


def build_payload(
    session,
    *,
    instance_row,
    alpha: float,
    beta: float,
    time_limit_seconds: int,
    normalize_objectives: bool = True,
    schedule_type: str = "BASELINE",
    now: int | None = None,                       # Part 3 seam — unused here
    failed_machine_names: tuple[str, ...] = (),   # Part 3 seam — unused here
):
    iid = instance_row.id

    machines = (
        session.query(Machine)
        .filter(Machine.instance_id == iid)
        .order_by(Machine.id).all()
    )
    machine_names = [m.name for m in machines]
    machine_by_id = {m.id: m.name for m in machines}

    families = (
        session.query(JobFamily).filter(JobFamily.instance_id == iid)
        .order_by(JobFamily.id).all()
    )
    family_name = {f.id: f.name for f in families}

    jobs = (
        session.query(Job).filter(Job.instance_id == iid).order_by(Job.id).all()
    )
    job_name = {j.id: j.name for j in jobs}

    ops = (
        session.query(Operation)
        .filter(Operation.instance_id == iid)
        .order_by(Operation.job_id, Operation.sequence_number).all()
    )
    op_by_id = {o.id: o for o in ops}

    alts = (
        session.query(OperationMachineAlternative)
        .filter(OperationMachineAlternative.instance_id == iid)
        .order_by(OperationMachineAlternative.operation_id,
                  OperationMachineAlternative.machine_id).all()
    )
    worker_rows = (
        session.query(OperationMachineWorkerTime, Worker.name)
        .join(Worker, Worker.id == OperationMachineWorkerTime.worker_id)
        .filter(OperationMachineWorkerTime.instance_id == iid)
        .order_by(OperationMachineWorkerTime.operation_id,
                  OperationMachineWorkerTime.machine_id,
                  OperationMachineWorkerTime.worker_id).all()
    )
    alt_workers: dict[tuple[int, int], dict[str, int]] = {}
    for row, wname in worker_rows:
        alt_workers.setdefault((row.operation_id, row.machine_id), {})[wname] = \
            row.processing_time

    setups = (
        session.query(SetupTime).filter(SetupTime.instance_id == iid)
        .order_by(SetupTime.machine_id, SetupTime.to_family_id,
                  SetupTime.from_family_id).all()
    )
    setup_entries = [
        {"machine_id": machine_by_id[s.machine_id],
         "from_family": family_name.get(s.from_family_id),
         "to_family": family_name[s.to_family_id],
         "duration": s.setup_duration}
        for s in setups
    ]

    downtime = (
        session.query(MachineDowntimeWindow)
        .filter(MachineDowntimeWindow.instance_id == iid)
        .order_by(MachineDowntimeWindow.machine_id,
                  MachineDowntimeWindow.downtime_from,
                  MachineDowntimeWindow.id).all()
    )
    downtime_entries = [
        {"machine_id": machine_by_id[w.machine_id],
         "from": w.downtime_from, "until": w.downtime_until,
         "reason": w.reason}
        for w in downtime
    ]

    availability = (
        session.query(WorkerAvailabilityWindow)
        .filter(WorkerAvailabilityWindow.instance_id == iid)
        .order_by(WorkerAvailabilityWindow.worker_id,
                  WorkerAvailabilityWindow.available_from,
                  WorkerAvailabilityWindow.available_until).all()
    )
    absences = (
        session.query(WorkerAbsenceWindow)
        .filter(WorkerAbsenceWindow.instance_id == iid)
        .order_by(WorkerAbsenceWindow.worker_id,
                  WorkerAbsenceWindow.absence_from,
                  WorkerAbsenceWindow.absence_until).all()
    )
    worker_names = dict(
        session.query(Worker.id, Worker.name)
        .filter(Worker.instance_id == iid).order_by(Worker.id).all()
    )

    boms = (
        session.query(OperationBom, Material.sku)
        .join(Material, Material.id == OperationBom.material_id)
        .filter(OperationBom.instance_id == iid)
        .order_by(Material.sku, OperationBom.operation_id).all()
    )
    bom_by_op: dict[str, list[dict]] = {}
    for row, sku in boms:
        op_row = op_by_id[row.operation_id]
        oid_ = op_id(job_name[op_row.job_id], op_row.sequence_number)
        bom_by_op.setdefault(oid_, []).append(
            {"sku": sku, "quantity": row.quantity_required})
    receipts = (
        session.query(MaterialReceipt, Material.sku)
        .join(Material, Material.id == MaterialReceipt.material_id)
        .filter(MaterialReceipt.instance_id == iid)
        .order_by(Material.sku, MaterialReceipt.available_at,
                  MaterialReceipt.id).all()
    )
    stock_by_sku = dict(
        session.query(Material.sku, Material.initial_stock)
        .filter(Material.instance_id == iid).order_by(Material.sku).all()
    )

    # ---- assemble operation dicts (baseline: everything PENDING) ----
    entry_by_dbid: dict[int, dict] = {}
    ops_by_job: dict[int, list[dict]] = {}
    for o in ops:
        jname = job_name[o.job_id]
        entry = {
            "operation_id": op_id(jname, o.sequence_number),
            "sequence": o.sequence_number,
            "status": "PENDING",
            "alternatives": [],
            "frozen": None,
        }
        entry_by_dbid[o.id] = entry
        ops_by_job.setdefault(o.job_id, []).append(entry)

    alt_index: dict[int, list] = {}
    for a in alts:
        alt_index.setdefault(a.operation_id, []).append(a)
    for o in ops:
        entry = entry_by_dbid[o.id]
        for a in alt_index.get(o.id, []):
            entry["alternatives"].append({
                "machine_id": machine_by_id[a.machine_id],
                "processing_time": a.processing_time,
                "workers": dict(alt_workers.get((o.id, a.machine_id), {})),
            })

    # ---- horizon BEFORE window conversion (tail-coverage amendment) ----
    payload_jobs_preview = [
        {"release_time": j.release_time,
         "operations": [e for e in ops_by_job[j.id]]}
        for j in jobs
    ]
    horizon = compute_horizon(jobs=payload_jobs_preview,
                              machine_downtime=downtime_entries,
                              setup_times=setup_entries)

    # ---- worker unavailability: complement within [0, H] + absence rows ----
    avail_by_worker: dict[int, list[tuple[int, int]]] = {}
    for w in availability:
        avail_by_worker.setdefault(w.worker_id, []).append(
            (w.available_from, min(w.available_until, horizon)))
    absence_by_worker: dict[int, list[tuple[int, int]]] = {}
    for w in absences:
        s = max(0, w.absence_from)
        e = horizon if w.absence_until is None else min(horizon, w.absence_until)
        if s < e:
            absence_by_worker.setdefault(w.worker_id, []).append((s, e))

    worker_unavailability = []
    for wid in sorted(worker_names):
        busy = list(absence_by_worker.get(wid, []))
        busy.extend(complement(0, horizon, avail_by_worker.get(wid, [])))
        for us, ue in merge_intervals(busy):
            worker_unavailability.append(
                {"worker_id": worker_names[wid], "from": us, "until": ue})

    # ---- material gatekeeping + cascade ----
    blocks, mat_warnings = evaluate_materials(
        initial_stock=stock_by_sku,
        receipts=[{"sku": sku, "quantity": r.quantity,
                   "available_at": r.available_at} for r, sku in receipts],
        bom_by_op=bom_by_op,
        horizon=horizon,
    )
    blocked_map = _cascade_blocked(ops_by_job, blocks)
    blocked_operations = []
    for entries in ops_by_job.values():
        for e in entries:
            if e["operation_id"] in blocked_map:
                e["status"] = "BLOCKED"
                e["alternatives"] = []
                blocked_operations.append(
                    {"operation_id": e["operation_id"],
                     **blocked_map[e["operation_id"]]})

    # ---- assemble final payload in §5 key order ----
    payload_jobs = []
    for j in jobs:
        payload_jobs.append({
            "job_id": j.name,
            "family_id": family_name.get(j.job_family_id),
            "release_time": j.release_time,
            "deadline": j.deadline,
            "priority": j.priority,
            "operations": [
                {k: e[k] for k in ("operation_id", "sequence", "status",
                                   "alternatives", "frozen")}
                for e in ops_by_job[j.id]
            ],
        })

    payload = {
        "instance_id": instance_row.name,
        "schedule_type": schedule_type,
        "parent_version_id": None,
        "config": {"alpha": alpha, "beta": beta,
                   "time_limit_seconds": time_limit_seconds,
                   "normalize_objectives": normalize_objectives},
        "machines": machine_names,
        "machine_initial_families": {},
        "warnings": list(mat_warnings),
        "jobs": payload_jobs,
        "machine_downtime": downtime_entries,
        "worker_unavailability": worker_unavailability,
        "setup_times": setup_entries,
        "blocked_operations": blocked_operations,
    }
    weights = derive_tardiness_weights(payload_jobs, beta)
    if weights is not None:
        payload["job_tardiness_weights"] = weights
    return payload
