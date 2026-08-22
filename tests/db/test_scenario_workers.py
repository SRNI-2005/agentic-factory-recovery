import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def _rows(url, sql, params):
    with create_engine(url).begin() as c:
        return c.execute(text(sql), params).all()


def test_every_alternative_covered(demo_scenario):
    from coe.config import get_settings

    orphans = _rows(
        get_settings().database_url,
        """
        SELECT count(*) FROM operation_machine_alternatives a
        WHERE a.instance_id = :i AND NOT EXISTS (
            SELECT 1 FROM operation_machine_worker_times w
            WHERE w.instance_id = a.instance_id
              AND w.operation_id = a.operation_id
              AND w.machine_id = a.machine_id)
        """,
        {"i": demo_scenario},
    )[0][0]
    assert orphans == 0


def test_worker_times_within_skill_band(demo_scenario):
    from coe.config import get_settings

    violations = _rows(
        get_settings().database_url,
        """
        SELECT count(*) FROM operation_machine_worker_times w
        JOIN operation_machine_alternatives a
          ON a.instance_id = w.instance_id
         AND a.operation_id = w.operation_id
         AND a.machine_id = w.machine_id
        WHERE w.instance_id = :i
          AND w.processing_time > ceil(a.processing_time * 1.20)
        """,
        {"i": demo_scenario},
    )[0][0]
    assert violations == 0


def test_windows_valid_and_patterned(demo_scenario):
    from coe.config import get_settings

    bad = _rows(
        get_settings().database_url,
        """
        SELECT count(*) FROM worker_availability_windows
        WHERE instance_id = :i AND (
            available_until <= available_from OR source_pattern IS NULL)
        """,
        {"i": demo_scenario},
    )[0][0]
    assert bad == 0
    total = _rows(
        get_settings().database_url,
        "SELECT count(*) FROM worker_availability_windows WHERE instance_id=:i",
        {"i": demo_scenario},
    )[0][0]
    assert total >= 12  # at least one window per worker
