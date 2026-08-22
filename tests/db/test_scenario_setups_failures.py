import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def test_matrix_symmetric_with_initials(demo_scenario):
    """For every A->B row an identical-duration B->A exists; every machine+family has an initial row."""
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        pairs = c.execute(
            text(
                "SELECT s.machine_id, fa.name, fb.name, s.setup_duration "
                "FROM setup_times s "
                "JOIN job_families fa ON fa.id = s.from_family_id "
                "JOIN job_families fb ON fb.id = s.to_family_id "
                "WHERE s.instance_id = :i"
            ),
            {"i": demo_scenario},
        ).all()
        initials = c.execute(
            text(
                "SELECT machine_id, count(DISTINCT to_family_id) FROM setup_times "
                "WHERE instance_id=:i AND from_family_id IS NULL GROUP BY machine_id"
            ),
            {"i": demo_scenario},
        ).all()
        fam_count = c.execute(
            text("SELECT count(*) FROM job_families WHERE instance_id=:i"),
            {"i": demo_scenario},
        ).scalar_one()
    lookup = {(m, a, b): d for m, a, b, d in pairs}
    for m, a, b, d in pairs:
        assert lookup.get((m, b, a)) == d, f"asymmetric pair on machine {m}: {a}->{b}"
    for m, cnt in initials:
        assert cnt == fam_count


def test_every_job_has_family(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        orphans = c.execute(
            text(
                "SELECT count(*) FROM jobs WHERE instance_id=:i "
                "AND job_family_id IS NULL"
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert orphans == 0


def test_maintenance_windows_sane(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        bad = c.execute(
            text(
                "SELECT count(*) FROM machine_downtime_windows WHERE instance_id=:i "
                "AND (reason != 'MAINTENANCE' OR downtime_until IS NULL "
                "OR downtime_from < 60)"
            ),
            {"i": demo_scenario},
        ).scalar_one()
        overlapping = c.execute(
            text(
                """
                SELECT count(*) FROM machine_downtime_windows a
                JOIN machine_downtime_windows b
                  ON a.instance_id = b.instance_id
                 AND a.machine_id = b.machine_id
                 AND a.id < b.id
                WHERE a.instance_id = :i
                  AND a.downtime_from < b.downtime_until
                  AND b.downtime_from < a.downtime_until
                """
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert bad == 0
    assert overlapping == 0
