import pytest

from coe.services.instances import get_row

pytestmark = pytest.mark.db


def test_get_row_resolves_name_or_none(clean_db, session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="lookup-src", source_name="test",
                    source_version="t", source_license="test")
    session.add(inst)
    session.flush()
    assert get_row(session, "lookup-src") is inst
    assert get_row(session, "missing-name") is None
