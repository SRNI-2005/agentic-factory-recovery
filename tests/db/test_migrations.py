import pytest
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.db


def test_provenance_tables_exist(clean_db):
    insp = inspect(create_engine(clean_db))
    assert insp.has_table("instances")
    assert insp.has_table("scenario_sources")
    assert insp.has_table("instance_profiles")


def test_instances_name_unique(clean_db):
    insp = inspect(create_engine(clean_db))
    uqs = [c["column_names"] for c in insp.get_unique_constraints("instances")]
    assert ["name"] in uqs
