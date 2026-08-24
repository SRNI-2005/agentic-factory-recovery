"""Spec §12 acceptance sweep — filename sorts last on purpose.

Audits the END STATE left by earlier solver suites (factory_demo_01 carries
CLI-created versions; mk01 carries committer versions) plus two standalone
checks. Criterion map (second-amendment numbering included):
  c3  -> cli stdout status line        (test_cli_solve::baseline)
  c5  -> recovery tests + invariants   (earlier files)
  c14 -> test_restore_reinclusion_here
  c16 -> test_empty_pending_commits_trivial_optimal
  c17 -> test_committed_durations_match_worker_times
Everything else maps 1:1 onto tasks recorded in this plan's appendix.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from coe.solver.engine import solve

pytestmark = pytest.mark.db

FIX = Path(__file__).resolve().parent / "fixtures"


def _sql(q, **params):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        return c.execute(text(q), params)


def test_committed_durations_match_worker_times():
    """c17: every active-version entry's duration equals the assigned
    worker's duration, or the machine-level alternative when workerless."""
    mismatches = _sql(
        """
        SELECT count(*) FROM schedule_entries se
        JOIN instances i ON i.id = se.instance_id
        JOIN schedule_versions sv ON sv.id = se.version_id
        WHERE i.name = 'factory_demo_01'
          AND sv.version_number = 1
          AND (
            CASE
              WHEN se.worker_id IS NOT NULL THEN
                se.processing_time != COALESCE((
                  SELECT wt.processing_time
                  FROM operation_machine_worker_times wt
                  WHERE wt.operation_id = se.operation_id
                    AND wt.machine_id = se.machine_id
                    AND wt.worker_id = se.worker_id), -1)
              ELSE
                se.processing_time != COALESCE((
                  SELECT a.processing_time
                  FROM operation_machine_alternatives a
                  WHERE a.operation_id = se.operation_id
                    AND a.machine_id = se.machine_id), -1)
            END
          )
        """
    ).scalar_one()
    assert mismatches == 0


def test_empty_pending_commits_trivial_optimal():
    """c16: all-frozen payload commits OPTIMAL with makespan = frozen end."""
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    p = json.loads((FIX / "empty_pending.json").read_text())
    p["parent_version_id"] = None          # standalone commit; no FK anchor
    sol = solve(p)
    assert sol["status"] == "OPTIMAL"
    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == "factory_demo_01").one())
        version = commit_solution(session, instance_row=inst,
                                  payload=p, solution=sol)
        vid = version.id
    row = _sql(
        "SELECT makespan, solver_status, total_tardiness "
        "FROM schedule_versions WHERE id = :v", v=vid).one()
    assert tuple(row) == (15, "OPTIMAL", 5)


def test_restore_reinclusion(env):
    """c14: a stripped failed machine re-enters the next built payload after
    its window is closed and status flipped back.

    Deviation from plan code: uses the ``env`` fixture instead of bare
    ``built_db`` — RECOVERY builds require an active snapshot, which
    ``built_db`` alone does not provide when this file runs standalone
    (full-suite order masks it via the CLI baseline commit). The seeded
    environment supplies one and rolls back all mutations.
    """
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine

    from coe.solver.payload_builder import build_payload

    session = env["session"]
    inst = env["instance"]
    sid = inst.id
    mid = session.query(Machine.id).filter(
        Machine.instance_id == sid, Machine.name == "M2").scalar_one()
    session.add(MachineDowntimeWindow(
        instance_id=sid, machine_id=mid, downtime_from=900,
        downtime_until=None, reason="FAILURE", source_event_ids=[]))

    p1 = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                       time_limit_seconds=30, schedule_type="RECOVERY",
                       now=env["now"], failed_machine_names=("M2",))
    assert "M2" not in p1["machines"]

    window = (session.query(MachineDowntimeWindow)
              .filter(MachineDowntimeWindow.machine_id == mid,
                      MachineDowntimeWindow.downtime_until.is_(None))
              .one())
    window.downtime_until = max(window.downtime_from + 1, 1100)

    p2 = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                       time_limit_seconds=30, schedule_type="RECOVERY",
                       now=env["now"], failed_machine_names=("M2",))
    assert "M2" in p2["machines"]
