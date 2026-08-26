"""Focused tests for CLI workbook import and template export (Task B10)."""
import pytest
from pathlib import Path

pytestmark = pytest.mark.db


@pytest.fixture()
def built_demo(clean_db):
    from tests.solver.conftest import build_all_sources_and_scenario
    build_all_sources_and_scenario()


@pytest.fixture()
def sample_workbook(built_demo):
    """Round-trip: export factory_demo_01 then re-import it."""
    from coe.db.session import session_scope
    from coe.parsers.workbook import export_workbook

    with session_scope() as session:
        from coe.db.models.provenance import Instance
        inst = (session.query(Instance)
                .filter(Instance.name == "factory_demo_01").one())
        data = export_workbook(session, inst.id)
    return data


def _cli(*args):
    import subprocess
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", *args],
        capture_output=True, text=True,
    )
    return r


class TestImportWorkbook:

    def test_import_requires_path(self):
        r = _cli("import", "workbook")
        assert r.returncode == 2  # argparse exits 2 on missing required args
        assert "required" in r.stderr.lower()

    def test_import_with_name(self, sample_workbook, tmp_path):
        wb_path = tmp_path / "test.xlsx"
        wb_path.write_bytes(sample_workbook)
        r = _cli("import", "workbook", "--path", str(wb_path),
                 "--name", "wb-imported")
        assert r.returncode == 0, r.stderr
        assert "wb-imported" in r.stdout

    def test_import_defaults_name_from_meta(self, sample_workbook, tmp_path):
        wb_path = tmp_path / "test.xlsx"
        wb_path.write_bytes(sample_workbook)
        r = _cli("import", "workbook", "--path", str(wb_path))
        assert r.returncode == 0, r.stderr
        assert "instance name=" in r.stdout

    def test_import_rejects_bad_workbook(self, built_demo, tmp_path):
        wb_path = tmp_path / "bad.xlsx"
        wb_path.write_bytes(b"not a workbook")
        r = _cli("import", "workbook", "--path", str(wb_path))
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "rejected" in combined.lower() or "error" in combined.lower()


class TestTemplateExport:

    def test_export_requires_instance(self):
        r = _cli("template", "export")
        assert r.returncode == 2  # argparse exits 2 on missing required args
        assert "required" in r.stderr.lower()

    def test_export_creates_file(self, built_demo, tmp_path):
        out = tmp_path / "sub" / "out.xlsx"
        r = _cli("template", "export", "--instance", "factory_demo_01",
                 "--out", str(out))
        assert r.returncode == 0, r.stderr
        assert out.exists()
        assert out.stat().st_size > 0
        assert "factory_demo_01" in r.stdout

    def test_export_default_path(self, built_demo):
        r = _cli("template", "export", "--instance", "factory_demo_01")
        assert r.returncode == 0, r.stderr
        default_out = Path("data/templates/factory_workbook.xlsx")
        assert default_out.exists()

    def test_export_unknown_instance(self, built_demo):
        r = _cli("template", "export", "--instance", "nonexistent")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "unknown instance" in combined.lower()
