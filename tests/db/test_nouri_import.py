from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

SFJW01 = Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt")


def test_import_creates_eligibility_rows(clean_db):
    from coe.parsers.nouri import import_nouri

    inst_id = import_nouri(SFJW01)
    with create_engine(clean_db).begin() as c:
        triples = c.execute(
            text(
                "SELECT count(*) FROM operation_machine_worker_times "
                "WHERE instance_id = :i"
            ),
            {"i": inst_id},
        ).scalar_one()
        workers = c.execute(
            text("SELECT count(*) FROM workers WHERE instance_id = :i"),
            {"i": inst_id},
        ).scalar_one()
    assert workers > 0
    assert triples > workers  # many eligibility rows per worker


def test_every_alternative_has_worker_coverage(clean_db):
    """Spec §6.3 invariant: no (op, machine) alternative without an eligible worker."""
    from coe.parsers.nouri import import_nouri

    inst_id = import_nouri(SFJW01)
    with create_engine(clean_db).begin() as c:
        orphans = c.execute(
            text(
                "SELECT count(*) FROM operation_machine_alternatives a "
                "WHERE a.instance_id = :i AND NOT EXISTS ("
                "  SELECT 1 FROM operation_machine_worker_times w"
                "  WHERE w.instance_id = a.instance_id"
                "    AND w.operation_id = a.operation_id"
                "    AND w.machine_id = a.machine_id)"
            ),
            {"i": inst_id},
        ).scalar_one()
    assert orphans == 0


def test_reimport_noop_and_checksum_instance(clean_db, tmp_path):
    modified = tmp_path / "SFJW-01.txt"
    original = SFJW01.read_text().split()
    original[-1] = str(int(original[-1]) + 1)  # mutate last duration
    modified.write_text(" ".join(original))
    from coe.parsers.nouri import import_nouri

    a = import_nouri(SFJW01)
    b = import_nouri(SFJW01)
    c = import_nouri(modified)
    assert a == b          # identical re-import is a no-op
    assert c != a          # changed checksum creates a new instance
