import hashlib
import json

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def test_supply_covers_demand(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        shortfall = c.execute(
            text(
                """
                SELECT count(*) FROM materials m
                WHERE m.instance_id = :i
                  AND m.initial_stock + COALESCE((
                        SELECT sum(r.quantity) FROM material_receipts r
                        WHERE r.material_id = m.id AND r.available_at <= 1440), 0)
                      < (
                        SELECT COALESCE(sum(b.quantity_required), 0)
                        FROM operation_bom b WHERE b.material_id = m.id)
                """
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert shortfall == 0  # baseline must not block any material


def test_full_build_is_byte_reproducible(clean_db):
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    def canonical_dump() -> str:
        eng = create_engine(get_settings().database_url)
        tables = [
            ("machines", "name"), ("jobs", "name"), ("operations", "id"),
            ("operation_machine_alternatives", "operation_id, machine_id"),
            ("workers", "name"), ("worker_roles", "role_name"),
            ("operation_machine_worker_times", "operation_id, machine_id, worker_id"),
            ("worker_availability_windows", "id"), ("job_families", "name"),
            ("setup_times", "id"), ("materials", "sku"), ("operation_bom", "operation_id, material_id"),
            ("material_receipts", "id"), ("machine_downtime_windows", "id"),
            ("scenario_sources", "id"),
        ]
        payload = {}
        with eng.begin() as c:
            sid = c.execute(
                text("SELECT id FROM instances WHERE name='factory_demo_01'")
            ).scalar_one()
            for table, order_col in tables:
                # scenario_sources keys on scenario_id, not instance_id
                filter_col = "scenario_id" if table == "scenario_sources" else "instance_id"
                rows = c.execute(
                    text(
                        f"SELECT * FROM {table} "
                        f"WHERE {filter_col} = :sid ORDER BY {order_col}"
                    ),
                    {"sid": sid},
                ).mappings().all()
                payload[table] = [dict(r) for r in rows]
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def rebuild() -> str:
        reset_database(get_settings().database_url)
        import_mk01(Path("data/raw/mk01/mk01.txt"))
        import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
        import_gass(Path("data/raw/gass"))
        build_scenario("factory_demo_01", seed=42)
        return canonical_dump()

    h1 = rebuild()
    h2 = rebuild()
    assert h1 == h2


def test_cli_build_smoke(clean_db):
    import subprocess

    result = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "import", "mk01"], check=True, capture_output=True
    )
    subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "import", "hutter", "--path",
         "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "import", "gass"],
        check=True, capture_output=True,
    )
    result = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "scenario", "build",
         "--name", "factory_demo_01", "--seed", "42"],
        check=True, capture_output=True, text=True,
    )
    assert "scenario id=" in result.stdout
