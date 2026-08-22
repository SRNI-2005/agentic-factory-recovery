from pathlib import Path

import pytest

pytestmark = pytest.mark.db

MK01_PATH = Path("data/raw/mk01/mk01.txt")


def _counts(url):
    from sqlalchemy import create_engine, text

    with create_engine(url).begin() as c:
        return {
            t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("instances", "machines", "jobs", "operations",
                      "operation_machine_alternatives")
        }


def test_import_creates_expected_shape(clean_db):
    from coe.parsers.mk01 import import_mk01

    inst_id = import_mk01(MK01_PATH)
    counts = _counts(clean_db)
    assert (counts["machines"], counts["jobs"], counts["operations"]) == (6, 10, 55)
    assert counts["operation_machine_alternatives"] > 100

    from sqlalchemy import create_engine, text

    with create_engine(clean_db).begin() as c:
        row = c.execute(
            text(
                "SELECT oma.machine_id, oma.processing_time "
                "FROM operation_machine_alternatives oma "
                "JOIN operations o ON o.id = oma.operation_id "
                "JOIN jobs j ON j.id = o.job_id "
                "WHERE j.source_id='0' AND o.source_id='0:0' "
                "ORDER BY oma.machine_id"
            )
        ).all()
        machines = dict(
            c.execute(
                text("SELECT id, name FROM machines WHERE instance_id = :i"),
                {"i": inst_id},
            ).all()
        )
    assert {(machines[m], t) for m, t in row} == {("M0", 5), ("M2", 4)}


def test_reimport_is_noop(clean_db):
    from coe.parsers.mk01 import import_mk01

    a = import_mk01(MK01_PATH)
    b = import_mk01(MK01_PATH)
    assert a == b
    assert _counts(clean_db)["instances"] == 1


def test_changed_checksum_creates_variant_instance(clean_db, tmp_path):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.parsers.mk01 import import_mk01

    a = import_mk01(MK01_PATH, instance_name="mk01")

    tokens = MK01_PATH.read_text().split()
    tokens[-1] = str(int(tokens[-1]) + 1) if int(tokens[-1]) > 0 else str(
        int(tokens[-1]) - 2
    )
    mutated = tmp_path / "mk01.txt"
    mutated.write_text(" ".join(tokens))

    b = import_mk01(mutated, instance_name="mk01")
    assert b != a

    with session_scope() as session:
        original = session.query(Instance).filter(Instance.name == "mk01").one()
        variant = (
            session.query(Instance).filter(Instance.name.like("mk01@%")).one_or_none()
        )
        assert variant is not None
        assert len(variant.name.split("@")[1]) == 8
        assert variant.source_checksum != original.source_checksum

    assert import_mk01(MK01_PATH, instance_name="mk01") == a
    assert _counts(clean_db)["instances"] == 2


def test_failed_import_leaves_no_partial_instance(clean_db):
    bad = Path("tests/fixtures/bad_mk01.txt")
    bad.parent.mkdir(exist_ok=True)
    bad.write_text("2 2\n1 1 5\n1 9 9\n")  # machine index 9 out of range for 2 machines
    try:
        from coe.parsers.mk01 import import_mk01
        from coe.parsers.common import SourceParseError

        with pytest.raises(SourceParseError):
            import_mk01(bad)
        assert _counts(clean_db)["instances"] == 0
    finally:
        bad.unlink()
