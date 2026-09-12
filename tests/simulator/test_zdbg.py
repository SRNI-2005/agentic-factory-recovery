import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.db


def test_zdbg_degraded(tmp_path, demo_scenario):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    import coe
    from coe.agents.degraded_client import DegradedLLMClient
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.services.fork import fork_instance
    from coe.simulator.engine import walk_timeline

    with Session(make_engine()) as s:
        source = (s.query(Instance)
                  .filter(Instance.name == "factory_demo_01").one())
        s.commit()
        forked = fork_instance(s, source)
        s.commit()
        clone = forked.name

    import subprocess
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", clone, "--workers", "1"],
        check=True, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr
    print("SOLVE stderr tail:", r.stderr[-500:])

    # replicate the recovery manually to get a real traceback
    from coe.agents.graph import build_graph
    from coe.agents.state import RecoveryState
    from coe.agents.locks import InstanceRunLock
    from coe.agents.degraded_client import DegradedLLMClient
    initial = RecoveryState(instance_name=clone, trigger="CLI",
                            narrative="MC-04 gearbox seized")
    client = DegradedLLMClient()
    app = build_graph(client)
    try:
        with InstanceRunLock(clone):
            final = RecoveryState.model_validate(app.invoke(initial))
        print("INVOKE OK", final.status if hasattr(final, "status") else "", final.committed_version_id)
    except Exception:
        import traceback
        print("INVOKE FAIL:")
        traceback.print_exc()


    data = {"name": "t1", "seed": 42, "horizon_days": 1, "events": [
        {"t": 512, "kind": "NARRATIVE", "text": "MC-04 gearbox seized",
         "severity": "HIGH"}]}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data))

    items = list(walk_timeline(str(p), instance_name=clone,
                               speed="instant",
                               llm_client_factory=lambda: DegradedLLMClient()))
    for i in items:
        print("CHUNK", json.dumps(i, default=str)[:300])
    s2 = Session(make_engine())
    iid = s2.query(Instance).filter(Instance.name == clone).one().id
    with make_engine().begin() as c:
        runs = c.execute(text(
            "SELECT id, status, disruption_record_json FROM recovery_runs WHERE instance_id=:i"),
            {"i": iid}).all()
        props = c.execute(text(
            "SELECT COUNT(*) FROM recovery_proposals WHERE instance_id=:i"),
            {"i": iid}).all()
        expl = c.execute(text(
            "SELECT se.rationale FROM schedule_explanations se "
            "JOIN schedule_versions sv ON sv.id=se.version_id "
            "WHERE sv.instance_id=:i"), {"i": iid}).all()
    print("RUNS", runs, "PROPOSALS", props, "EXPL", expl)
    from coe.config import get_settings
    gs = get_settings()
    print("SENT max_rounds:", repr(getattr(gs, "strategy_max_rounds", None)), "binding:", getattr(gs.__class__, "__name__", None))
    import coe.db.session as dbs
    print("SENT sb_session binding:", dbs.get_settings)
