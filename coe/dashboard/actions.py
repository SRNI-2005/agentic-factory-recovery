"""Dashboard event actions — thin wrappers with Streamlit-friendly error handling.

Every function returns (ok_message, error_message).  The caller decides
how to surface them; we never import Streamlit here so the module stays
testable without a running Streamlit process.

DB-writing actions accept an optional ``session`` kwarg so tests can pass
a fixture session without a nested ``session_scope()``.  The dashboard UI
never passes it.
"""
from __future__ import annotations


def machine_down_action(
    instance_name: str, machine_name: str, at: int | None = None
) -> tuple[str | None, str | None]:
    from coe.services.actions import machine_down

    try:
        mid = machine_down(instance_name, machine_name, at=at)
        return f"Published MACHINE FAILURE (message_id={mid})", None
    except Exception as exc:
        return None, f"machine down failed: {exc}"


def machine_restore_action(
    instance_name: str, machine_name: str, at: int | None = None, *,
    session=None,
) -> tuple[str | None, str | None]:
    from coe.db.session import session_scope
    from coe.services.actions import restore_machine

    def _run(s):
        return restore_machine(s, instance_name, machine_name, at=at)

    try:
        if session is not None:
            now = _run(session)
            return f"Restored {machine_name} at t={now}", None
        with session_scope() as s:
            now = _run(s)
        return f"Restored {machine_name} at t={now}", None
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"machine restore failed: {exc}"


def worker_absent_action(
    instance_name: str, worker_name: str,
    at: int | None = None, duration: int | None = None
) -> tuple[str | None, str | None]:
    from coe.services.actions import worker_absent

    try:
        mid = worker_absent(instance_name, worker_name, at=at, duration=duration)
        return f"Published WORKER ABSENT (message_id={mid})", None
    except Exception as exc:
        return None, f"worker absent failed: {exc}"


def worker_return_action(
    instance_name: str, worker_name: str, at: int | None = None
) -> tuple[str | None, str | None]:
    from coe.services.actions import worker_return

    try:
        mid = worker_return(instance_name, worker_name, at=at)
        return f"Published WORKER RETURN (message_id={mid})", None
    except Exception as exc:
        return None, f"worker return failed: {exc}"


def material_shortage_action(
    instance_name: str, sku: str, at: int | None = None
) -> tuple[str | None, str | None]:
    from coe.services.actions import material_shortage

    try:
        mid = material_shortage(instance_name, sku, at=at)
        return f"Published MATERIAL SHORTAGE (message_id={mid})", None
    except Exception as exc:
        return None, f"material shortage failed: {exc}"


def suspend_job_action(
    instance_name: str, job_name: str, *, session=None
) -> tuple[str | None, str | None]:
    from coe.db.session import session_scope
    from coe.services.actions import suspend_job

    def _run(s):
        return suspend_job(s, instance_name, job_name)

    try:
        if session is not None:
            _run(session)
            return f"Suspended job {job_name}", None
        with session_scope() as s:
            _run(s)
        return f"Suspended job {job_name}", None
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"suspend job failed: {exc}"


def resume_job_action(
    instance_name: str, job_name: str, *, session=None
) -> tuple[str | None, str | None]:
    from coe.db.session import session_scope
    from coe.services.actions import resume_job

    def _run(s):
        return resume_job(s, instance_name, job_name)

    try:
        if session is not None:
            _run(session)
            return f"Resumed job {job_name}", None
        with session_scope() as s:
            _run(s)
        return f"Resumed job {job_name}", None
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"resume job failed: {exc}"
