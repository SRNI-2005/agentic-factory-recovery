from pathlib import Path
from shutil import copytree

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

GASS_DIR = Path("data/raw/gass")


def test_manifest_parses():
    from coe.parsers.gass import parse_manifest

    m = parse_manifest(GASS_DIR / "manifest.txt")
    assert len(m) == 6
    assert all(len(v) == 64 for v in m.values())


def test_import_stores_three_profiles(clean_db):
    from coe.parsers.gass import import_gass

    inst_id = import_gass(GASS_DIR)
    with create_engine(clean_db).begin() as c:
        rows = c.execute(
            text(
                "SELECT name, profile_type, parameters_json FROM instance_profiles "
                "WHERE source_instance_id = :i ORDER BY name"
            ),
            {"i": inst_id},
        ).all()
    by_name = {r[0]: r for r in rows}
    assert set(by_name) == {"gass-machines", "gass-orders", "gass-routings"}
    assert len(by_name["gass-machines"][2]["machines"]) == 15
    assert len(by_name["gass-orders"][2]["orders"]) == 59
    assert by_name["gass-routings"][2]["processes"][0]["code"] == "P1"


def test_tampered_file_rejected(clean_db, tmp_path):
    tampered = tmp_path / "gass"
    copytree(GASS_DIR, tampered)
    target = tampered / "1-Machine.xlsx"
    payload = bytearray(target.read_bytes())
    payload[-1] ^= 0xFF
    target.write_bytes(bytes(payload))

    from coe.parsers.common import SourceParseError
    from coe.parsers.gass import import_gass

    with pytest.raises(SourceParseError, match="checksum mismatch"):
        import_gass(tampered)


def test_reimport_noop(clean_db):
    from coe.parsers.gass import import_gass

    a = import_gass(GASS_DIR)
    b = import_gass(GASS_DIR)
    assert a == b
