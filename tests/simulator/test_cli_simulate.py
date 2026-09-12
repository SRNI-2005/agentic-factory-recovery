# tests/simulator/test_cli_simulate.py
# NB: the brief's Step-1 fixture combined a schema (pydantic) error with a
# "monotonic" assertion; no loader message can contain both. Split into the
# TimelineError (monotonic) case and the ValidationError (schema) case.
import json
import pytest

pytestmark = pytest.mark.db


def _run(*argv, tmp_path=None):
    import subprocess
    return subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "simulate", "timeline",
         *argv], capture_output=True, text=True)


def test_timeline_error_exits_1_with_message(tmp_path, demo_scenario):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "events": [
        {"t": 5, "kind": "NARRATIVE", "text": "hi"},
        {"t": 3, "kind": "NARRATIVE", "text": "hi"}]}))
    r = _run("--file", str(bad))
    assert r.returncode != 0 and "monotonic" in (r.stdout + r.stderr)


def test_schema_error_exits_1_with_message(tmp_path, demo_scenario):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "events": [
        {"t": 5, "kind": "NARRATIVE", "text": "hi", "at": 5}]}))
    r = _run("--file", str(bad))
    assert r.returncode != 0 and "Extra inputs" in (r.stdout + r.stderr)


def test_unknown_instance_exits_cleanly(tmp_path, demo_scenario):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"name": "x", "events": [
        {"t": 5, "kind": "NARRATIVE", "text": "hi"}]}))
    r = _run("--file", str(ok), "--instance", "bogus")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "unknown instance" in out
    assert "Traceback" not in out


def test_bad_speed_exits_cleanly(tmp_path, demo_scenario):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"name": "x", "events": [
        {"t": 5, "kind": "NARRATIVE", "text": "hi"}]}))
    r = _run("--file", str(ok), "--speed", "fast")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "invalid speed" in out
    assert "Traceback" not in out
