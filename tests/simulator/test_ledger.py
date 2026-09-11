"""material_transactions table exists and enforces instance scoping."""
import pytest

pytestmark = pytest.mark.db


def test_insert_and_check_constraint(clean_db):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from coe.db.models.materials import MaterialTransaction
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as s:
        inst = Instance(name="mtx-inst", source_name="synthetic")
        s.add(inst); s.flush()
        row = MaterialTransaction(
            instance_id=inst.id, operation_id=None, material_id=None,
            quantity=3, timestamp=100, transaction_type="RESTOCK",
            source="simulate")
        s.add(row); s.flush()
        n = s.execute(text(
            "SELECT count(*) FROM material_transactions")).scalar_one()
        assert n == 1
        # invalid type must violate the CHECK
        row_bad = MaterialTransaction(
            instance_id=inst.id, operation_id=None, material_id=None,
            quantity=1, timestamp=1, transaction_type="BROKEN_TY",
            source="simulate")
        s.add(row_bad)
        try:
            s.flush()
            raised = False
        except IntegrityError as e:
            assert "mtx_type" in str(e)
            raised = True
            s.rollback()
    assert raised
