import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _mk_instance(session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="t-fjsp", source_name="test")
    session.add(inst)
    session.flush()
    return inst


def test_operation_unique_job_sequence(clean_db):
    from coe.db.session import session_scope
    from coe.db.models.fjsp import Operation
    from coe.db.models.provenance import Instance

    with session_scope() as s:
        inst = Instance(name="t-opseq", source_name="test")
        s.add(inst)
        s.flush()
        j = _simple_job(s, inst.id)
        s.add(Operation(instance_id=inst.id, job_id=j.id, sequence_number=1))
        s.add(Operation(instance_id=inst.id, job_id=j.id, sequence_number=1))
        try:
            s.flush()
            raised = False
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_bad_status_rejected(clean_db):
    from sqlalchemy import text
    from coe.db.session import make_engine
    from coe.db.session import session_scope
    from coe.db.models.provenance import Instance

    with session_scope() as s:
        inst = Instance(name="t-status", source_name="test")
        s.add(inst)
        s.flush()
        try:
            s.execute(
                text(
                    "INSERT INTO machines (instance_id, name, status) "
                    "VALUES (:i, 'MC-X', 'BROKEN')"
                ),
                {"i": inst.id},
            )
            s.commit()
            ok = False
        except IntegrityError:
            ok = True
            s.rollback()
    assert ok


def test_alternative_composite_pk(clean_db):
    """Same (op, machine) twice in one instance must fail; different instances OK."""
    from coe.db.session import session_scope
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative as Alt,
    )
    from coe.db.models.provenance import Instance

    with session_scope() as s:
        i1 = Instance(name="t-alt-a", source_name="test")
        s.add(i1)
        s.flush()
        m = Machine(instance_id=i1.id, name="M1")
        j = Job(instance_id=i1.id, name="J1")
        s.add_all([m, j])
        s.flush()
        op = Operation(instance_id=i1.id, job_id=j.id, sequence_number=1)
        s.add(op)
        s.flush()
        s.add(Alt(instance_id=i1.id, operation_id=op.id, machine_id=m.id, processing_time=5))
        s.flush()  # first insert fine
        s.add(Alt(instance_id=i1.id, operation_id=op.id, machine_id=m.id, processing_time=7))
        try:
            s.flush()
            dup_rejected = False
        except IntegrityError:
            dup_rejected = True
            s.rollback()
    assert dup_rejected


def _simple_job(session, instance_id):
    from coe.db.models.fjsp import Job

    j = Job(instance_id=instance_id, name="J1")
    session.add(j)
    session.flush()
    return j
