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


@pytest.mark.slow
def test_commit_writes_consume_rows(demo_scenario):
    """Two baseline commits on a pristine fork; ledger deltas per version.
    Incompatible with the shared session clone (ledger counts depend on a
    clone written ONLY by this test's solves) — kept slow/starved from the
    quick gate."""
    import subprocess

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.services.fork import fork_instance

    with Session(make_engine()) as s:
        source = (s.query(Instance)
                  .filter(Instance.name == "factory_demo_01").one())
        forked = fork_instance(s, source)
        s.commit()
        clone_name = forked.name

    def consume_count():
        with make_engine().begin() as c:
            return c.execute(text(
                "SELECT COUNT(*) FROM material_transactions mt "
                "JOIN instances i ON i.id = mt.instance_id "
                "WHERE i.name = :name "
                "AND mt.transaction_type = 'CONSUME' "
                "AND mt.source = 'commit'"), {"name": clone_name}).scalar()

    def run_baseline():
        subprocess.run(
            ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
             "--instance", clone_name],
            check=True, capture_output=True)

    run_baseline()
    c1 = consume_count()
    with make_engine().begin() as c:
        first_version = c.execute(text(
            "SELECT sv.id FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :name "
            "ORDER BY sv.version_number DESC LIMIT 1"),
            {"name": clone_name}).scalar_one()
        first_expected = c.execute(
            text(_ledger_expectation_sql()), {"vid": first_version}).scalar()
    assert c1 == first_expected

    # Re-commit on the same clone: the ledger has no version_id, each commit
    # appends CONSUME rows for its own version's non-frozen entries, so the
    # growth must equal the second version's expectation exactly.
    run_baseline()
    c2 = consume_count()
    assert c2 > c1
    with make_engine().begin() as c:
        last_version = c.execute(text(
            "SELECT sv.id FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :name "
            "ORDER BY sv.version_number DESC LIMIT 1"),
            {"name": clone_name}).scalar_one()
        assert last_version != first_version
        expected = c.execute(
            text(_ledger_expectation_sql()), {"vid": last_version}).scalar()
    assert c2 - c1 == expected


def _ledger_expectation_sql() -> str:
    return (
        "SELECT COUNT(*) FROM schedule_entries se "
        "JOIN operations o ON o.id = se.operation_id "
        "JOIN operation_bom ob ON ob.operation_id = o.id "
        "AND ob.instance_id = se.instance_id "
        "WHERE se.version_id = :vid AND se.is_frozen = false"
    )
