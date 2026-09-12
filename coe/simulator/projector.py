"""Day playback: derive the committed schedule's state at clock t.

Completions are the committed entries' end times, NOT re-solved — the
active schedule is the single playback source. The projector NEVER writes.

Classification boundaries mirror the payload builder's freeze semantics
(P2 §3.1): end_time == t is COMPLETED, start_time == t (still running at
t) is IN_PROGRESS. Effective stock equals Task 3's RECOVERY deduction at
the same clock: initial stock minus BOM consumption of entries started at
or before t (suspended jobs excluded) plus receipts with available_at <= t.

Playback is physical truth: the consumed-up-to-clock arithmetic counts every
committed entry that has started, regardless of whether the machine it ran on
has since been marked FAILED — those bars were physically consumed before the
failure. The recovery payload builder re-derives its own demands for solving
(re-classifying pre-failure work); this projector is the audit rail that shows
real stock, so the dashboard may exceed the next solve's effective capacity
while a mid-flight failure exists.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow, WorkerAbsenceWindow
from coe.db.models.fjsp import Job, Machine, Operation
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.provenance import Instance
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.db.models.workers import Worker


@dataclass
class DayState:
    """Playback mirror of the committed schedule at ``clock``.

    completed_ops/in_progress list op names from committed entries only;
    BLOCKED jobs' committed entries still appear (playback mirror).
    """

    clock: int
    completed_ops: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    effective_stock: dict[str, int] = field(default_factory=dict)
    active_downtime: dict[str, list] = field(default_factory=dict)
    worker_absence: dict[str, list] = field(default_factory=dict)

    def feed_line(self) -> str:
        return (f"t={self.clock} done={len(self.completed_ops)} "
                f"running={len(self.in_progress)} "
                f"down_machines={list(self.active_downtime)}")


def project_day(session: Session, *, instance_name: str, t: int) -> DayState:
    inst = (session.query(Instance)
            .filter(Instance.name == instance_name).one())
    iid = inst.id

    version = (session.query(ScheduleVersion)
               .filter(ScheduleVersion.instance_id == iid,
                       ScheduleVersion.solver_status.in_(("OPTIMAL",
                                                          "FEASIBLE")),
                       ScheduleVersion.rolled_back.is_(False))
               .order_by(ScheduleVersion.version_number.desc(),
                         ScheduleVersion.id.desc()).first())
    ds = DayState(clock=t)
    if version is None:
        return ds

    # ---- op play from the committed schedule (never re-solved) ----
    entries = (session.query(ScheduleEntry, Operation, Job)
               .join(Operation, Operation.id == ScheduleEntry.operation_id)
               .join(Job, Job.id == Operation.job_id)
               .filter(ScheduleEntry.instance_id == iid,
                       ScheduleEntry.version_id == version.id)
               .order_by(ScheduleEntry.end_time, ScheduleEntry.id).all())
    for entry, op, job in entries:
        np = f"{job.name}-O{op.sequence_number}"
        if entry.end_time <= t:
            ds.completed_ops.append(np)
        elif entry.start_time <= t < entry.end_time:
            ds.in_progress.append(np)

    # ---- active downtime / absence windows (half-open, open-until stays) ----
    machines = dict(session.query(Machine.id, Machine.name)
                    .filter(Machine.instance_id == iid)
                    .order_by(Machine.name).all())
    for w in (session.query(MachineDowntimeWindow)
              .filter(MachineDowntimeWindow.instance_id == iid)
              .order_by(MachineDowntimeWindow.machine_id,
                        MachineDowntimeWindow.downtime_from,
                        MachineDowntimeWindow.id).all()):
        if w.downtime_from <= t and (w.downtime_until is None
                                     or w.downtime_until > t):
            ds.active_downtime.setdefault(machines[w.machine_id], []).append(
                [w.downtime_from, w.downtime_until])
    workers = dict(session.query(Worker.id, Worker.name)
                   .filter(Worker.instance_id == iid)
                   .order_by(Worker.name).all())
    for w in (session.query(WorkerAbsenceWindow)
              .filter(WorkerAbsenceWindow.instance_id == iid)
              .order_by(WorkerAbsenceWindow.worker_id,
                        WorkerAbsenceWindow.absence_from,
                        WorkerAbsenceWindow.id).all()):
        if w.absence_from <= t and (w.absence_until is None
                                    or w.absence_until > t):
            ds.worker_absence.setdefault(workers[w.worker_id], []).append(
                [w.absence_from, w.absence_until])

    # ---- effective stock at t (matches payload_builder RECOVERY now=t) ----
    stock = dict(session.query(Material.sku, Material.initial_stock)
                 .filter(Material.instance_id == iid)
                 .order_by(Material.sku).all())
    consumed: dict[str, int] = {}
    rows = (session.query(ScheduleEntry.start_time, Material.sku,
                          OperationBom.quantity_required)
            .join(Operation, Operation.id == ScheduleEntry.operation_id)
            .join(Job, Job.id == Operation.job_id)
            .join(OperationBom, OperationBom.operation_id == Operation.id)
            .join(Material, Material.id == OperationBom.material_id)
            .filter(ScheduleEntry.instance_id == iid,
                    ScheduleEntry.version_id == version.id,
                    ScheduleEntry.start_time <= t,
                    Job.status != "BLOCKED")
            .order_by(Material.sku, ScheduleEntry.id).all())
    for _start, sku, qty in rows:
        consumed[sku] = consumed.get(sku, 0) + qty

    folded: dict[str, int] = {}
    for sku, qty in (
        session.query(Material.sku, MaterialReceipt.quantity)
        .join(MaterialReceipt, MaterialReceipt.material_id == Material.id)
        .filter(MaterialReceipt.instance_id == iid,
                MaterialReceipt.available_at <= t)
        .order_by(Material.sku, MaterialReceipt.available_at,
                  MaterialReceipt.id).all()
    ):
        folded[sku] = folded.get(sku, 0) + qty

    ds.effective_stock = {
        sku: stock.get(sku, 0) - consumed.get(sku, 0) + folded.get(sku, 0)
        for sku in sorted(set(stock) | set(folded))
    }
    return ds
