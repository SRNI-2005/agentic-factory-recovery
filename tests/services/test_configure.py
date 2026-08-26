import pytest

from coe.db.models.downtime import MachineDowntimeWindow, WorkerAbsenceWindow
from coe.db.models.fjsp import Job, JobFamily, Machine, Operation
from coe.db.models.materials import Material, MaterialReceipt
from coe.db.models.provenance import Instance
from coe.db.models.workers import (
    Worker,
    WorkerAvailabilityWindow,
    WorkerRole,
)
from coe.services.configure import jobs, jobs_per_day, machines, materials, workers
from coe.services.instances import get, get_row, list_instances

pytestmark = pytest.mark.db


def _mk_instance(session, name="dash-fixture"):
    inst = Instance(name=name, source_name="test",
                    source_version="t", source_license="test")
    session.add(inst)
    session.flush()
    return inst


def test_list_instances_orders_and_reports_parent(clean_db, session):
    _mk_instance(session)
    session.add(Instance(name="child@deadbeef", source_name="test",
                         source_version="t", source_license="test",
                         source_checksum="deadbeefcafe"))
    session.flush()
    rows = list_instances(session)
    assert [r.name for r in rows] == sorted(r.name for r in rows)
    child = next(r for r in rows if r.name.startswith("child"))
    # parent linkage comes from provenance lineage written by fork;
    # before any fork exists the column is simply None
    assert child.parent is None


def test_list_instances_reports_fork_parent(clean_db, session):
    parent = _mk_instance(session, name="parent-inst")
    child = _mk_instance(session, name="child@deadbeef")
    from coe.db.models.provenance import ScenarioSource

    session.add(ScenarioSource(
        scenario_id=child.id, source_instance_id=parent.id,
        contribution_type="fork", transformation_description="fork of p"))
    session.flush()
    rows = {r.name: r for r in list_instances(session)}
    assert rows["child@deadbeef"].parent == "parent-inst"
    assert rows["parent-inst"].parent is None


def test_get_includes_parent_or_none(clean_db, session):
    parent = _mk_instance(session, name="parent-inst")
    child = _mk_instance(session, name="child@deadbeef")
    from coe.db.models.provenance import ScenarioSource

    session.add(ScenarioSource(
        scenario_id=child.id, source_instance_id=parent.id,
        contribution_type="fork", transformation_description="fork of p"))
    session.flush()
    got = get(session, "child@deadbeef")
    assert got is not None
    assert (got.name, got.source_name, got.parent) == (
        "child@deadbeef", "test", "parent-inst")
    assert get(session, "missing-name") is None


def test_active_schedule_none_when_empty(clean_db, session):
    from coe.services.schedules import active

    inst = _mk_instance(session)
    assert active(session, inst.id) is None


def test_materials_overview_orders_sku_and_receipts(clean_db, session):
    inst = _mk_instance(session)
    m1 = Material(instance_id=inst.id, sku="MAT-B", initial_stock=50,
                  reorder_point=5)
    m2 = Material(instance_id=inst.id, sku="MAT-A", initial_stock=10,
                  reorder_point=None)
    session.add_all([m1, m2])
    session.flush()
    session.add_all([
        MaterialReceipt(instance_id=inst.id, material_id=m1.id, quantity=3,
                        available_at=900, source="po"),
        MaterialReceipt(instance_id=inst.id, material_id=m1.id, quantity=7,
                        available_at=100, source="initial"),
    ])
    session.flush()

    rows = materials(session, inst.id)
    assert [r.sku for r in rows] == ["MAT-A", "MAT-B"]
    b = rows[1]
    assert b.initial_stock == 50 and b.reorder_point == 5
    assert [(r["quantity"], r["available_at"], r["source"])
            for r in b.receipts] == [(7, 100, "initial"), (3, 900, "po")]
    assert rows[0].receipts == []


def test_machines_overview_reports_open_window(clean_db, session):
    inst = _mk_instance(session)
    m_down = Machine(instance_id=inst.id, name="M2", status="FAILED")
    m_up = Machine(instance_id=inst.id, name="M1", status="ACTIVE")
    session.add_all([m_down, m_up])
    session.flush()
    session.add(MachineDowntimeWindow(instance_id=inst.id, machine_id=m_down.id,
                                      downtime_from=10, downtime_until=None,
                                      reason="failure"))
    session.add(MachineDowntimeWindow(instance_id=inst.id, machine_id=m_down.id,
                                      downtime_from=0, downtime_until=5,
                                      reason="old"))
    session.flush()

    rows = machines(session, inst.id)
    assert [(r.name, r.status) for r in rows] == [("M1", "ACTIVE"),
                                                  ("M2", "FAILED")]
    assert rows[0].down_since is None
    assert rows[1].down_since == 10


def test_workers_overview_roles_availability_absence(clean_db, session):
    inst = _mk_instance(session)
    role = WorkerRole(instance_id=inst.id, role_name="operator")
    session.add(role)
    session.flush()
    w1 = Worker(instance_id=inst.id, name="W1", role_id=role.id)
    w2 = Worker(instance_id=inst.id, name="W2", role_id=None)
    session.add_all([w1, w2])
    session.flush()
    session.add(WorkerAvailabilityWindow(instance_id=inst.id, worker_id=w1.id,
                                         available_from=0, available_until=480,
                                         source_pattern="shift"))
    session.add(WorkerAbsenceWindow(instance_id=inst.id, worker_id=w1.id,
                                    absence_from=100, absence_until=None,
                                    reason="absent"))
    session.add(WorkerAbsenceWindow(instance_id=inst.id, worker_id=w1.id,
                                    absence_from=10, absence_until=20,
                                    reason="past"))
    session.flush()

    rows = workers(session, inst.id)
    assert [r.name for r in rows] == ["W1", "W2"]
    assert rows[0].role == "operator"
    assert rows[0].availability == [(0, 480)]
    assert rows[0].absent_since == 100
    assert rows[1].role is None
    assert rows[1].availability == []
    assert rows[1].absent_since is None


def test_jobs_overview_family_ops_ordered_by_name(clean_db, session):
    inst = _mk_instance(session)
    fam = JobFamily(instance_id=inst.id, name="fam-b")
    other = JobFamily(instance_id=inst.id, name="fam-a")
    session.add_all([fam, other])
    session.flush()
    j1 = Job(instance_id=inst.id, name="J2", job_family_id=fam.id,
             release_time=5, deadline=100, priority=2, status="PENDING")
    j2 = Job(instance_id=inst.id, name="J1", job_family_id=None,
             release_time=0, deadline=None, priority=1, status="BLOCKED")
    session.add_all([j1, j2])
    session.flush()
    session.add(Operation(instance_id=inst.id, job_id=j1.id, sequence_number=1))
    session.add(Operation(instance_id=inst.id, job_id=j1.id, sequence_number=2))

    rows = jobs(session, inst.id)
    assert [r.name for r in rows] == ["J1", "J2"]
    assert rows[1].family == "fam-b"
    assert rows[1].ops == 2
    assert rows[0].family is None
    assert rows[0].ops == 0
    assert (rows[1].release_time, rows[1].deadline, rows[1].priority,
            rows[1].status) == (5, 100, 2, "PENDING")


def test_jobs_per_day_groups_by_deadline(clean_db, session):
    inst = _mk_instance(session)
    session.add_all([
        Job(instance_id=inst.id, name="J1", release_time=0, deadline=1400,
            priority=1, status="PENDING"),
        Job(instance_id=inst.id, name="J2", release_time=0, deadline=1500,
            priority=1, status="PENDING"),
    ])
    grouped = jobs_per_day(session, inst.id)
    assert grouped == {0: ["J1"], 1: ["J2"]}  # deadline//1440
