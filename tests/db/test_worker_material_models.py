import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _fixture_ids(session):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.provenance import Instance

    inst = Instance(name="t-wm", source_name="test")
    session.add(inst)
    session.flush()
    m = Machine(instance_id=inst.id, name="M1")
    j = Job(instance_id=inst.id, name="J1")
    session.add_all([m, j])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=j.id, sequence_number=1)
    session.add(op)
    session.flush()
    return inst, m, op


def test_worker_eligibility_composite_pk(clean_db):
    from coe.db.models.materials import Material  # noqa: F401
    from coe.db.models.workers import (
        OperationMachineWorkerTime as Omwt,
        Worker,
        WorkerRole,
    )
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, m, op = _fixture_ids(s)
        role = WorkerRole(instance_id=inst.id, role_name="operator")
        s.add(role)
        s.flush()
        w = Worker(instance_id=inst.id, name="W1", role_id=role.id)
        s.add(w)
        s.flush()
        s.add(Omwt(instance_id=inst.id, operation_id=op.id, machine_id=m.id,
                   worker_id=w.id, processing_time=10))
        s.flush()
        try:
            s.add(Omwt(instance_id=inst.id, operation_id=op.id, machine_id=m.id,
                       worker_id=w.id, processing_time=12))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_availability_window_order_check(clean_db):
    from coe.db.models.workers import Worker, WorkerAvailabilityWindow
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, _, _ = _fixture_ids(s)
        w = Worker(instance_id=inst.id, name="W2")
        s.add(w)
        s.flush()
        try:
            s.add(WorkerAvailabilityWindow(
                instance_id=inst.id, worker_id=w.id,
                available_from=100, available_until=50,
                source_pattern="shift"))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_bom_quantity_positive(clean_db):
    from coe.db.models.materials import Material, OperationBom
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, m, op = _fixture_ids(s)
        mat = Material(instance_id=inst.id, sku="STEEL-304", initial_stock=100)
        s.add(mat)
        s.flush()
        try:
            s.add(OperationBom(instance_id=inst.id, operation_id=op.id,
                               material_id=mat.id, quantity_required=0))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised
