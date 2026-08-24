"""Recovery-quality pin (hardening E, Option A rider): the two-phase
relax-then-repair warm start must commit a materially sane factory RECOVERY
schedule where the pre-fix engine returned mk=5584 at 45s.

Isolation narrative: factory_demo_01 baseline committed via
commit_solution_autocommit (as the capability sweep did), FAILURE ingested
on M3 at t=150 through the real ingest path, RECOVERY built at now=180 with
M3 stripped. Benchmark-marked so the default suite stays lean.

State hygiene: every committed/mutated row is removed in teardown (entries,
versions, telemetry, downtime window, machine status, operation statuses)
so downstream suites over the shared scenario see pristine state.
"""
import pytest

pytestmark = [pytest.mark.db, pytest.mark.benchmark, pytest.mark.slow]

FAIL_AT = 150
NOW = 180


def test_factory_recovery_quality_pin(built_db):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine, Operation
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.downtime import TelemetryEvent
    from coe.db.session import session_scope
    from coe.mqtt.ingest import ingest_telemetry_event
    from coe.solver.committer import commit_solution_autocommit
    from coe.solver.engine import solve
    from coe.solver.invariants import check_solution
    from coe.solver.payload_builder import build_payload

    def _inst(session):
        return session.query(Instance).filter(
            Instance.name == "factory_demo_01").one()

    def _payload(**kw):
        with session_scope() as session:
            return build_payload(session, instance_row=_inst(session),
                                 alpha=1.0, beta=1.0, **kw)

    vid = None
    try:
        base_p = _payload(time_limit_seconds=30)
        base = solve(base_p)
        print(f"BASELINE {base['status']} mk={base['makespan']} "
              f"tard={base['total_tardiness']}", flush=True)
        assert base["status"] in ("OPTIMAL", "FEASIBLE")
        vid = commit_solution_autocommit("factory_demo_01", base_p, base)

        ingest_telemetry_event(dict(
            message_id="recovery-quality-pin-m3",
            instance_id="factory_demo_01",
            resource_kind="MACHINE", machine_id="M3",
            event_type="FAILURE", occurred_at=FAIL_AT, severity="HIGH"))

        rec_p = _payload(time_limit_seconds=120, schedule_type="RECOVERY",
                         now=NOW, failed_machine_names=("M3",))
        assert "M3" not in rec_p["machines"]
        print(f"RECOVERY payload machines={len(rec_p['machines'])} "
              f"downtime={len(rec_p['machine_downtime'])} "
              f"unavail={len(rec_p['worker_unavailability'])} "
              f"warnings={len(rec_p['warnings'])}", flush=True)
        import json as _json
        from pathlib import Path as _Path
        _Path(".superpowers/sdd/rec_payload_dump.json").write_text(
            _json.dumps(rec_p, sort_keys=True, indent=1))
        sol = solve(rec_p)
        print(f"RECOVERY {sol['status']} mk={sol['makespan']} "
              f"tard={sol['total_tardiness']}", flush=True)

        assert sol["status"] in ("OPTIMAL", "FEASIBLE"), sol["status"]
        assert check_solution(rec_p, sol) == []
        live = [a for a in sol["assignments"] if not a["is_frozen"]]
        machines = set(rec_p["machines"])
        assert all(a["machine_id"] in machines for a in live)
        # pre-fix catastrophic probe: FEASIBLE mk=5584 @45s; post-fix target
        # <= ~800 at 120s, hard gate 1200 per brief.
        assert sol["makespan"] <= 1200, sol["makespan"]
    finally:
        with session_scope() as session:
            inst = _inst(session)
            if vid is not None:
                session.query(ScheduleEntry).filter(
                    ScheduleEntry.version_id == vid).delete(
                    synchronize_session=False)
                session.query(ScheduleVersion).filter(
                    ScheduleVersion.id == vid).delete(
                    synchronize_session=False)
            mid = session.query(Machine.id).filter(
                Machine.instance_id == inst.id,
                Machine.name == "M3").scalar_one()
            session.query(MachineDowntimeWindow).filter(
                MachineDowntimeWindow.machine_id == mid,
                MachineDowntimeWindow.reason == "FAILURE",
                MachineDowntimeWindow.downtime_from == FAIL_AT,
            ).delete(synchronize_session=False)
            session.query(TelemetryEvent).filter(
                TelemetryEvent.instance_id == inst.id,
                TelemetryEvent.message_id
                == "recovery-quality-pin-m3",
            ).delete(synchronize_session=False)
            mrow = session.query(Machine).filter(
                Machine.id == mid).one()
            mrow.status = "ACTIVE"
            op_ids = [o.id for o in session.query(Operation).filter(
                Operation.instance_id == inst.id).all()]
            if op_ids:
                session.query(Operation).filter(
                    Operation.id.in_(op_ids)).update(
                    {Operation.status: "PENDING"},
                    synchronize_session=False)
