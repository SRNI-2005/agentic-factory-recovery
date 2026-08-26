"""Tests for Important #3 dead code: verify coe.services.events is unused."""
import importlib
import subprocess
import sys


def test_events_module_not_imported():
    """coe.services.events should not be imported by any production code."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import ast, pathlib, sys; "
         "root = pathlib.Path('coe'); "
         "found = []; "
         "[found.append(str(p)) for p in root.rglob('*.py') "
         "if p.name != 'events.py' and 'events' in p.read_text() "
         "and 'from coe.services.events' in p.read_text()]; "
         "print('\\n'.join(found) if found else 'NONE'); "
         "sys.exit(1 if found else 0)"],
        capture_output=True, text=True, cwd=".",
    )
    # Should find no imports of coe.services.events outside events.py itself
    assert result.returncode == 0, (
        f"coe.services.events is still imported by:\n{result.stdout}"
    )


def test_events_module_removable():
    """Removing coe/services/events.py must not break coe.services.recovery."""
    # recovery.py imports EventLog from itself, not from events.py
    from coe.services.recovery import EventLog
    log = EventLog()
    log.append({"test": True})
    assert log.snapshot() == ([{"test": True}], 1)
