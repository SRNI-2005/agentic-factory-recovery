"""Database → solver payload JSON (spec §3.1/§5, incl. the 2026-08-23
amendments and the 2026-08-24 material/suspension/status amendment).
Owns ALL builder-side DB access; emits deterministic dicts.

Conventions:
- identifiers: machines/jobs/workers/materials use DB names; operations are
  synthesized "{job.name}-O{seq}".
- every collection query carries an explicit ORDER BY (repo determinism rule).
- intervals are half-open [from, until).
"""
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
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
)
from coe.solver.horizon import compute_horizon
from coe.solver.identifier import op_id
from coe.solver.materials_check import evaluate_materials
from coe.solver.windows import clip_window, complement, merge_intervals


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


def _load_active_snapshot(session, iid: int):
    """Latest non-rolled-back OPTIMAL/FEASIBLE version + entries indexed by
    operation db-id (mirrors the §4 active_schedule view semantics)."""
    version = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == iid,
                ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                ScheduleVersion.rolled_back.is_(False))
        .order_by(ScheduleVersion.version_number.desc(),
                  ScheduleVersion.id.desc()).first()
    )
    if version is None:
        return None, {}
    entries = (
        session.query(ScheduleEntry)
        .filter(ScheduleEntry.version_id == version.id)
        .order_by(ScheduleEntry.id).all()
    )
    return version, {e.operation_id: e for e in entries}


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
    machine_by_id = {m.id: m.name for m in machines}
    machine_names = [m.name for m in machines]
    # Status truth (amendment 2026-08-24 rider d): a FAILED-status machine is
    # stripped outright — conservative even without CLI failed args.
    failed_status = {m.name for m in machines if m.status == "FAILED"}

    families = (
        session.query(JobFamily).filter(JobFamily.instance_id == iid)
        .order_by(JobFamily.id).all()
    )
    family_name = {f.id: f.name for f in families}

    jobs = (
        session.query(Job).filter(Job.instance_id == iid).order_by(Job.id).all()
    )
    job_name = {j.id: j.name for j in jobs}
    # Suspension memory (amendment 2026-08-24 rider c): jobs persisted BLOCKED
    # never enter the payload; they are remembered as JOB_SUSPENDED instead.
    suspended_ids = {j.id for j in jobs if j.status == "BLOCKED"}
    suspended_names = sorted(job_name[jid] for jid in suspended_ids)
    active_jobs = [j for j in jobs if j.id not in suspended_ids]

    ops = (
        session.query(Operation)
        .filter(Operation.instance_id == iid)
        .order_by(Operation.job_id, Operation.sequence_number).all()
    )
    op_by_id = {o.id: o for o in ops}
    suspended_op_ids = {o.id for o in ops if o.job_id in suspended_ids}
    worker_rows_all = (
        session.query(Worker)
        .filter(Worker.instance_id == iid).order_by(Worker.id).all()
    )
    worker_names_by_dbid = {w.id: w.name for w in worker_rows_all}
    offline_workers = {w.id for w in worker_rows_all
                       if w.status == "UNAVAILABLE"}

    recovering = schedule_type == "RECOVERY"
    failed_set = set(failed_machine_names) | failed_status
    parent_version_id = None
    active_by_opid: dict[int, object] = {}
    if recovering:
        if now is None:
            raise ValueError("RECOVERY payloads require a reference clock (now)")
        unknown = sorted(failed_set - set(machine_names))
        if unknown:
            raise ValueError(f"unknown failed machines: {unknown}")
        parent_version, active_by_opid = _load_active_snapshot(session, iid)
        if parent_version is None:
            raise ValueError("RECOVERY requires an existing active schedule")
        parent_version_id = parent_version.id

    downtime = (
        session.query(MachineDowntimeWindow)
        .filter(MachineDowntimeWindow.instance_id == iid)
        .order_by(MachineDowntimeWindow.machine_id,
                  MachineDowntimeWindow.downtime_from,
                  MachineDowntimeWindow.id).all()
    )
    open_windows = {w.machine_id for w in downtime if w.downtime_until is None}
    stripped = ({
        machine_by_id[mid] for mid in open_windows
        if machine_by_id[mid] in failed_set
    } if recovering else set()) | failed_status
    machine_names = [n for n in machine_names if n not in stripped]

    # ---- classify operations against the clock ----
    truncation: dict[int, int] = {}
    entry_by_dbid: dict[int, dict] = {}
    ops_by_job: dict[int, list[dict]] = {}
    for o in ops:
        if o.job_id in suspended_ids:
            continue
        jname = job_name[o.job_id]
        entry = {
            "operation_id": op_id(jname, o.sequence_number),
            "sequence": o.sequence_number,
            "status": "PENDING",
            "alternatives": [],
            "frozen": None,
        }
        ae = active_by_opid.get(o.id)
        if ae is not None:
            m_ae = machine_by_id[ae.machine_id]
            w_ae = (worker_names_by_dbid.get(ae.worker_id)
                    if ae.worker_id is not None else None)
            if ae.end_time <= now:
                entry["status"] = "COMPLETED"
                entry["frozen"] = {"machine_id": m_ae, "worker_id": w_ae,
                                   "start": ae.start_time, "end": ae.end_time}
            elif ae.start_time <= now:
                if m_ae in failed_set:
                    truncation[o.id] = ae.end_time - now
                else:
                    entry["status"] = "IN_PROGRESS"
                    entry["frozen"] = {"machine_id": m_ae, "worker_id": w_ae,
                                       "start": ae.start_time,
                                       "end": ae.end_time}
        entry_by_dbid[o.id] = entry
        ops_by_job.setdefault(o.job_id, []).append(entry)

    alts = (
        session.query(OperationMachineAlternative)
        .filter(OperationMachineAlternative.instance_id == iid)
        .order_by(OperationMachineAlternative.operation_id,
                  OperationMachineAlternative.machine_id).all()
    )
    alt_index: dict[int, list] = {}
    for a in alts:
        alt_index.setdefault(a.operation_id, []).append(a)

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

    for o in ops:
        entry = entry_by_dbid.get(o.id)
        if entry is None or entry["status"] != "PENDING":
            continue
        remaining = truncation.get(o.id)
        for a in alt_index.get(o.id, []):
            m_alt = machine_by_id[a.machine_id]
            if m_alt in stripped:
                continue
            workers = dict(alt_workers.get((o.id, a.machine_id), {}))
            base = a.processing_time
            level = base
            if remaining is not None and remaining < base:
                level = remaining
                workers = {wk: max(1, round(dv * remaining / base))
                           for wk, dv in workers.items()}
            entry["alternatives"].append({
                "machine_id": m_alt,
                "processing_time": level,
                "workers": workers,
            })
        if not entry["alternatives"]:
            entry["status"] = "BLOCKED"
            entry["alternatives"] = []

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
    boms = (
        session.query(OperationBom, Material.sku)
        .join(Material, Material.id == OperationBom.material_id)
        .filter(OperationBom.instance_id == iid)
        .order_by(Material.sku, OperationBom.operation_id).all()
    )
    bom_by_op: dict[str, list[dict]] = {}
    for row, sku in boms:
        if row.operation_id in suspended_op_ids:
            continue
        op_row = op_by_id[row.operation_id]
        oid_ = op_id(job_name[op_row.job_id], op_row.sequence_number)
        bom_by_op.setdefault(oid_, []).append(
            {"sku": sku, "quantity": row.quantity_required})
    receipt_rows = (
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

    # Per-op demand lists (amendment 2026-08-24, §5): only PENDING entries
    # carry demands — frozen/completed ops must not re-consume historic usage;
    # blocked flips reset them to [] further below.
    for entries in ops_by_job.values():
        for e in entries:
            e["materials"] = (list(bom_by_op.get(e["operation_id"], []))
                              if e["status"] == "PENDING" else [])

    # ---- horizon BEFORE window conversion (tail-coverage amendment) ----
    preview_jobs = [{"release_time": j.release_time,
                     "operations": ops_by_job[j.id]} for j in active_jobs]
    raw_windows = [
        {"machine_id": machine_by_id[w.machine_id],
         "from": w.downtime_from, "until": w.downtime_until,
         "reason": w.reason}
        for w in downtime if machine_by_id[w.machine_id] not in stripped
    ]
    frozen_max_end = max(
        (e["frozen"]["end"] for entries in ops_by_job.values()
         for e in entries if e["frozen"] is not None),
        default=0)
    horizon = compute_horizon(jobs=preview_jobs,
                              machine_downtime=raw_windows,
                              setup_times=setup_entries,
                              frozen_max_end=frozen_max_end)

    frozen_by_machine: dict[str, list[tuple[int, int]]] = {}
    frozen_by_worker: dict[str, list[tuple[int, int]]] = {}
    for entries in ops_by_job.values():
        for e in entries:
            fz = e["frozen"]
            if fz is None:
                continue
            frozen_by_machine.setdefault(fz["machine_id"], []).append(
                (fz["start"], fz["end"]))
            if fz["worker_id"] is not None:
                frozen_by_worker.setdefault(fz["worker_id"], []).append(
                    (fz["start"], fz["end"]))

    warnings: list[dict] = []
    downtime_entries = []
    for w in raw_windows:
        s = w["from"]
        e = w["until"] if w["until"] is not None else horizon
        outcome = clip_window((s, e), frozen_by_machine.get(w["machine_id"], []))
        if outcome is None:
            warnings.append({"type": "DOWNTIME_DROPPED",
                             "machine_id": w["machine_id"],
                             "window": [s, e],
                             "reason": "fully covered by frozen operations"})
            continue
        if outcome != (s, e):
            warnings.append({"type": "DOWNTIME_CLIPPED",
                             "machine_id": w["machine_id"],
                             "window": [s, e],
                             "clipped_to": [outcome[0], outcome[1]],
                             "reason": "overlaps frozen operations"})
            s, e = outcome
        downtime_entries.append({"machine_id": w["machine_id"],
                                 "from": s, "until": e,
                                 "reason": w["reason"]})

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
    for wid in sorted(worker_names_by_dbid):
        wname = worker_names_by_dbid[wid]
        busy = list(absence_by_worker.get(wid, []))
        if wid in offline_workers:
            # Status truth rider (d): UNAVAILABLE worker = out all horizon.
            busy.append((0, horizon))
        busy.extend(complement(0, horizon, avail_by_worker.get(wid, [])))
        for us, ue in merge_intervals(busy):
            outcome = clip_window((us, ue), frozen_by_worker.get(wname, []))
            if outcome is None:
                warnings.append({"type": "WORKER_WINDOW_DROPPED",
                                 "worker_id": wname,
                                 "window": [us, ue],
                                 "reason": "fully covered by frozen operations"})
                continue
            if outcome != (us, ue):
                warnings.append({"type": "WORKER_WINDOW_CLIPPED",
                                 "worker_id": wname,
                                 "window": [us, ue],
                                 "clipped_to": [outcome[0], outcome[1]],
                                 "reason": "overlaps frozen operations"})
                us, ue = outcome
            worker_unavailability.append(
                {"worker_id": wname, "from": us, "until": ue})

    # ---- material gatekeeping + dead-end merge + single cascade ----
    blocks, mat_warnings = evaluate_materials(
        initial_stock=stock_by_sku,
        receipts=[{"sku": sku, "quantity": r.quantity,
                   "available_at": r.available_at}
                  for r, sku in receipt_rows],
        bom_by_op=bom_by_op,
        horizon=horizon,
    )
    for entries in ops_by_job.values():
        for e in entries:
            if e["status"] == "BLOCKED":
                blocks[e["operation_id"]] = {"reason": "NO_CAPABLE_MACHINES",
                                             "material_sku": None}
    blocked_map = _cascade_blocked(ops_by_job, blocks)
    blocked_operations = []
    for entries in ops_by_job.values():
        for e in entries:
            if e["operation_id"] in blocked_map:
                e["status"] = "BLOCKED"
                e["alternatives"] = []
                e["materials"] = []
                blocked_operations.append(
                    {"operation_id": e["operation_id"],
                     **blocked_map[e["operation_id"]]})
    warnings.extend(mat_warnings)

    setup_pairs = {(s["machine_id"], s["from_family"], s["to_family"])
                   for s in setup_entries}
    for m, ff, tf in sorted(setup_pairs,
                            key=lambda t: (t[0], t[1] or "", t[2] or "")):
        if ff is not None and tf is not None and (m, tf, ff) not in setup_pairs:
            warnings.append({"type": "SETUP_MATRIX_ASYMMETRIC",
                             "machine_id": m, "from_family": ff,
                             "to_family": tf})

    # ---- suspension memory entries (rider c): one per op of a BLOCKED job ----
    suspended_entries = [
        {"operation_id": op_id(job_name[o.job_id], o.sequence_number),
         "reason": "JOB_SUSPENDED", "material_sku": None}
        for o in sorted((o for o in ops if o.job_id in suspended_ids),
                        key=lambda o: (job_name[o.job_id],
                                       o.sequence_number))
    ]
    blocked_operations = suspended_entries + blocked_operations

    # ---- material physics inputs for the engine reservoir (§6.11) ----
    in_horizon_receipts = [
        {"sku": sku, "quantity": r.quantity, "available_at": r.available_at}
        for r, sku in receipt_rows if r.available_at < horizon
    ]
    in_horizon_receipts.sort(
        key=lambda d: (d["sku"], d["available_at"], d["quantity"]))
    capacity_by_sku = dict(stock_by_sku)
    for r in in_horizon_receipts:
        capacity_by_sku[r["sku"]] += r["quantity"]
    materials_out = [{"sku": s, "capacity": capacity_by_sku[s]}
                     for s in sorted(capacity_by_sku)]

    # ---- initial family seeding from the active snapshot ----
    machine_initial_families: dict[str, str] = {}
    if recovering:
        last_entry: dict[int, tuple[tuple[int, int], int]] = {}
        for op_dbid, ae in active_by_opid.items():
            key = (ae.end_time, ae.id)
            prev = last_entry.get(ae.machine_id)
            if prev is None or key > prev[0]:
                last_entry[ae.machine_id] = (key, op_dbid)
        for mid_, (_key, op_dbid) in sorted(last_entry.items()):
            if machine_by_id[mid_] in stripped:
                continue
            fam = family_name.get(
                next(j for j in jobs
                     if j.id == op_by_id[op_dbid].job_id).job_family_id)
            if fam is not None:
                machine_initial_families[machine_by_id[mid_]] = fam

    payload_jobs = []
    for j in active_jobs:
        payload_jobs.append({
            "job_id": j.name,
            "family_id": family_name.get(j.job_family_id),
            "release_time": j.release_time,
            "deadline": j.deadline,
            "priority": j.priority,
            "operations": [
                {k: e[k] for k in ("operation_id", "sequence", "status",
                                   "materials", "alternatives", "frozen")}
                for e in ops_by_job[j.id]
            ],
        })

    payload = {
        "instance_id": instance_row.name,
        "schedule_type": schedule_type,
        "parent_version_id": parent_version_id,
        "config": {"alpha": alpha, "beta": beta,
                   "time_limit_seconds": time_limit_seconds,
                   "normalize_objectives": normalize_objectives},
        "machines": machine_names,
        "machine_initial_families": machine_initial_families,
        "warnings": warnings,
        "jobs": payload_jobs,
        "machine_downtime": downtime_entries,
        "materials": materials_out,
        "material_receipts": in_horizon_receipts,
        "worker_unavailability": worker_unavailability,
        "setup_times": setup_entries,
        "blocked_operations": blocked_operations,
        "suspended_jobs": suspended_names,
    }
    weights = derive_tardiness_weights(payload_jobs, beta)
    if weights is not None:
        payload["job_tardiness_weights"] = weights
    return payload
