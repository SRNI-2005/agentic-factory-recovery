"""§4.2 investigation nodes: pure queries, kind-gated no-ops."""
import pytest

pytestmark = pytest.mark.db

from coe.agents.state import RecoveryState


@pytest.fixture()
def world(clean_db):
    """Instance with: M1(cap CODE-A)/M2, W1/W2, two jobs, an active v1."""
    from coe.db.models.fjsp import (
        Job,
        JobFamily,
        Machine,
        MachineCapability,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material, OperationBom
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import (
        OperationMachineWorkerTime,
        Worker,
    )
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="inv-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id

        m1 = Machine(instance_id=iid, name="M1")
        m2 = Machine(instance_id=iid, name="M2")
        w1 = Worker(instance_id=iid, name="W1")
        w2 = Worker(instance_id=iid, name="W2")
        fam = JobFamily(instance_id=iid, name="FAM")
        session.add_all([m1, m2, w1, w2, fam])
        session.flush()

        session.add(MachineCapability(instance_id=iid, machine_id=m1.id,
                                      capability_code="CODE-A",
                                      source="mk01"))
        session.flush()

        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0)
        jb = Job(instance_id=iid, name="J-B", priority=3, release_time=0,
                 deadline=100)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob1 = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob1])
        session.flush()

        # J-A.1 routable on M1(5m) and M2(7m); only W2 knows M2.
        for m, t in ((m1, 5), (m2, 7)):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=oa.id, machine_id=m.id,
                processing_time=t))
            session.add(OperationMachineWorkerTime(
                instance_id=iid, operation_id=oa.id, machine_id=m.id,
                worker_id=(w2.id if m is m2 else w1.id),
                processing_time=t))
        session.flush()

        mat = Material(instance_id=iid, sku="MAT-X", initial_stock=5)
        session.add(mat)
        session.flush()
        session.add(OperationBom(instance_id=iid, operation_id=ob1.id,
                                 material_id=mat.id, quantity_required=8))

        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=50,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.1,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v1)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oa.id,
            machine_id=m1.id, worker_id=w1.id, start_time=0, end_time=5,
            processing_time=5, is_frozen=False, status="SCHEDULED"))
        session.flush()
    return iid


def _rec(kind, **fields):
    base = {"kind": kind, "instance_id": "inv-world",
            "event_type": {"MACHINE": "FAILURE", "WORKER": "WORKER_ABSENT",
                           "MATERIAL": "MATERIAL_SHORTAGE"}[kind],
            "occurred_at": 3, "severity": "HIGH",
            "narrative_excerpt": "x"}
    base.update(fields)
    return base


def _state(record=None):
    return RecoveryState(instance_name="inv-world",
                         reference_clock=3,
                         disruption_record=record)


def test_machine_agent_reports_capabilities(world):
    from coe.agents.nodes.investigate import machine_agent_node

    out = machine_agent_node(_state(_rec("MACHINE", machine_id="M1")))
    assert out.db_facts["failed_machine"]["machine_id"] == "M1"
    assert out.db_facts["failed_machine"]["capabilities_lost"] == ["CODE-A"]


def test_machine_agent_noops_on_worker_kind(world):
    from coe.agents.nodes.investigate import machine_agent_node

    out = machine_agent_node(_state(_rec("WORKER", worker_id="W1")))
    assert out.db_facts["failed_machine"] is None


def test_production_agent_stranded_ops(world):
    from coe.agents.nodes.investigate import production_agent_node

    out = production_agent_node(
        _state(_rec("MACHINE", machine_id="M1")))
    s = out.db_facts["stranded_operations"]
    assert len(s) == 1
    assert s[0]["operation_id"].startswith("J-A-O")   # "{job}-O{seq}"
    assert s[0]["machine_id"] == "M1"


def test_inventory_agent_horizon_and_shortage(world):
    from coe.agents.nodes.investigate import inventory_agent_node

    out = inventory_agent_node(
        _state(_rec("MATERIAL", material_sku="MAT-X")))
    facts = out.db_facts
    assert isinstance(facts["projected_horizon"], int)
    ev = facts["shortage_evidence"]
    assert ev["material_sku"] == "MAT-X"
    assert ev["total_supply"] == 5
    assert ev["total_demand"] == 8
    assert len(ev["affected_operations"]) >= 1     # J-B-O1 references MAT-X


def test_worker_agent_sole_eligibility(world):
    from coe.agents.nodes.investigate import worker_agent_node

    out = worker_agent_node(_state(_rec("WORKER", worker_id="W2")))
    aw = out.db_facts["absent_worker"]
    assert aw["worker_id"] == "W2"
    # (J-A-O1, M2) has exactly one eligible worker: W2.
    assert {"operation_id": "J-A-O1", "machine_id": "M2"} \
        in aw["sole_eligible"]


def test_worker_agent_noops_on_machine_kind(world):
    from coe.agents.nodes.investigate import worker_agent_node

    out = worker_agent_node(_state(_rec("MACHINE", machine_id="M1")))
    assert out.db_facts["absent_worker"] is None
