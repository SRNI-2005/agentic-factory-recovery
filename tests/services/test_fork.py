from datetime import datetime

import pytest

from coe.db.models.downtime import TelemetryEvent
from coe.db.models.recovery import RecoveryRun
from coe.services.fork import ForkError, fork_instance

pytestmark = pytest.mark.db


@pytest.fixture
def mini_factory(session):
    """Hermetic source instance exercising every copied table."""
    from coe.db.models.downtime import (MachineDowntimeWindow,
                                         TelemetryEvent,
                                         WorkerAbsenceWindow)
    from coe.db.models.fjsp import (
        Job, JobFamily, Machine, MachineCapability, Operation,
        OperationMachineAlternative, SetupTime)
    from coe.db.models.materials import (Material, MaterialReceipt,
                                         OperationBom)
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import RecoveryRun
    from coe.db.models.schedule import ScheduleEntry
    from coe.db.models.workers import (
        OperationMachineWorkerTime, Worker, WorkerAvailabilityWindow,
        WorkerRole)

    inst = Instance(name="fork-src", source_name="test",
                    source_version="t", source_license="test")
    session.add(inst)
    session.flush()
    fam = JobFamily(instance_id=inst.id, name="fam1")
    m1 = Machine(instance_id=inst.id, name="M1")
    m2 = Machine(instance_id=inst.id, name="M2")
    role = WorkerRole(instance_id=inst.id, role_name="operator")
    job = Job(instance_id=inst.id, name="J1", release_time=0,
              deadline=100, priority=1, status="PENDING")
    session.add_all([fam, m1, m2, role, job])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=job.id, sequence_number=1,
                   required_role_id=role.id)
    w = Worker(instance_id=inst.id, name="W1", role_id=role.id,
               status="AVAILABLE")
    session.add_all([op, w])
    session.flush()
    session.add(MachineCapability(instance_id=inst.id, machine_id=m1.id,
                                  capability_code="cnc", source="test"))
    session.add(OperationMachineAlternative(
        instance_id=inst.id, operation_id=op.id, machine_id=m1.id,
        processing_time=10))
    session.add(OperationMachineWorkerTime(
        instance_id=inst.id, operation_id=op.id, machine_id=m1.id,
        worker_id=w.id, processing_time=8))
    session.add(SetupTime(instance_id=inst.id, machine_id=m1.id,
                          from_family_id=None, to_family_id=fam.id,
                          setup_duration=5, source="test"))
    mat = Material(instance_id=inst.id, sku="MAT-1", initial_stock=50,
                   reorder_point=5)
    session.add(mat)
    session.flush()
    session.add(MaterialReceipt(instance_id=inst.id, material_id=mat.id,
                                quantity=10, available_at=200,
                                source="test"))
    session.add(OperationBom(instance_id=inst.id, operation_id=op.id,
                             material_id=mat.id, quantity_required=2))
    session.add(MachineDowntimeWindow(instance_id=inst.id, machine_id=m1.id,
                                      downtime_from=0, downtime_until=None,
                                      reason="test"))
    session.add(WorkerAvailabilityWindow(instance_id=inst.id, worker_id=w.id,
                                         available_from=0,
                                         available_until=480,
                                         source_pattern="shift"))
    session.add(WorkerAbsenceWindow(instance_id=inst.id, worker_id=w.id,
                                    absence_from=10, absence_until=None,
                                    reason="test"))
    # system-owned row that must NOT be copied (columns per downtime.py:16-53;
    # machine_id is an integer FK, exactly_one_resource CHECK satisfied):
    session.add(TelemetryEvent(
        occurred_at=1, instance_id=inst.id, message_id="t-1",
        machine_id=m1.id, worker_id=None, material_id=None,
        resource_kind="MACHINE", event_type="FAILURE", received_at=2,
        payload_json={}))
    session.flush()
    return inst


def test_fork_copies_all_domain_tables_with_matching_counts(clean_db, session, mini_factory):
    from sqlalchemy import inspect as sa_inspect

    from coe.db.models.downtime import (MachineDowntimeWindow,
                                         WorkerAbsenceWindow)
    from coe.db.models.fjsp import (
        Job, JobFamily, Machine, MachineCapability, Operation,
        OperationMachineAlternative, SetupTime)
    from coe.db.models.materials import (Material, MaterialReceipt,
                                         OperationBom)
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import (
        OperationMachineWorkerTime, Worker, WorkerAvailabilityWindow,
        WorkerRole)

    fork = fork_instance(session, mini_factory)
    for Model in [JobFamily, Machine, MachineCapability, WorkerRole, Worker,
                  Job, Operation, OperationMachineAlternative,
                  OperationMachineWorkerTime, SetupTime, Material,
                  MaterialReceipt, OperationBom, MachineDowntimeWindow,
                  WorkerAvailabilityWindow, WorkerAbsenceWindow]:
        src = (session.query(Model)
               .filter(Model.instance_id == mini_factory.id).count())
        dst = (session.query(Model)
               .filter(Model.instance_id == fork.id).count())
        assert src == dst, Model.__name__


def test_fork_skips_telemetry_and_recovery_history(clean_db, session, mini_factory):
    from coe.db.models.downtime import TelemetryEvent
    from coe.db.models.recovery import RecoveryRun

    session.add(RecoveryRun(
        instance_id=mini_factory.id, trigger="CLI", status="COMMITTED",
        disruption_record_json={}, started_at=datetime(2026, 1, 1),
        finished_at=None))
    session.flush()
    fork = fork_instance(session, mini_factory)
    assert session.query(TelemetryEvent).filter(
        TelemetryEvent.instance_id == fork.id).count() == 0
    assert session.query(RecoveryRun).filter(
        RecoveryRun.instance_id == fork.id).count() == 0


def test_fork_remaps_schedule_foreign_keys(clean_db, session, mini_factory):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.schedule import ScheduleEntry

    mach = (session.query(Machine)
            .filter(Machine.instance_id == mini_factory.id).first())
    op = (session.query(Operation)
          .filter(Operation.instance_id == mini_factory.id).first())
    ver = _mk_version(session, mini_factory.id, 1)  # local helper below
    entry = ScheduleEntry(instance_id=mini_factory.id, version_id=ver.id,
                          operation_id=op.id, machine_id=mach.id,
                          worker_id=None, start_time=0, end_time=10,
                          processing_time=10, setup_time=0,
                          status="SCHEDULED", is_frozen=False)
    session.add(entry)
    session.flush()

    fork = fork_instance(session, mini_factory)
    f_entry = (session.query(ScheduleEntry)
               .filter(ScheduleEntry.instance_id == fork.id).one())
    assert f_entry.id != entry.id
    f_op = session.query(Operation).filter(
        Operation.instance_id == fork.id,
        Operation.job_id.in_(
            session.query(Job.id).filter(Job.instance_id == fork.id))
    ).one()
    assert f_entry.operation_id == f_op.id
    assert f_entry.machine_id != mach.id


def test_fork_records_lineage(clean_db, session, mini_factory):
    from coe.db.models.provenance import ScenarioSource

    fork = fork_instance(session, mini_factory)
    ss = (session.query(ScenarioSource)
          .filter(ScenarioSource.scenario_id == fork.id,
                  ScenarioSource.contribution_type == "fork").one())
    assert ss.source_instance_id == mini_factory.id


def test_fork_name_collision_raises(clean_db, session, mini_factory):
    fork_instance(session, mini_factory, new_name="my-fork")
    with pytest.raises(ForkError):
        fork_instance(session, mini_factory, new_name="my-fork")


def test_fork_default_name_pattern(clean_db, session, mini_factory):
    fork = fork_instance(session, mini_factory)
    stem, _, suffix = fork.name.partition("@")
    assert stem == "fork-src" and len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def _mk_version(session, inst_id, number, makespan=100):
    from coe.db.models.schedule import ScheduleVersion

    ver = ScheduleVersion(
        instance_id=inst_id, version_number=number,
        schedule_type="BASELINE", solver_status="FEASIBLE",
        objective_value=float(makespan), makespan=makespan,
        total_tardiness=0, alpha_weight=0.5, beta_weight=0.5,
        time_limit_seconds=30, solve_duration_seconds=0.42,
        failed_machine_ids=None, parent_version_id=None,
        rolled_back=False, payload_hash="0" * 64, payload_json={})
    session.add(ver)
    session.flush()
    return ver
