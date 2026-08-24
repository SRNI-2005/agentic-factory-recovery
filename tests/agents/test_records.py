"""§4.1 validation layers 1-4 + §3.2 shared state."""
import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.db


def _machine(**over):
    d = {"kind": "MACHINE", "instance_id": "factory_demo_01",
         "machine_id": "M3", "event_type": "FAILURE", "occurred_at": 512,
         "severity": "HIGH", "estimated_downtime": 90,
         "narrative_excerpt": "gearbox seized"}
    d.update(over)
    return d


def test_machine_record_parses():
    from coe.agents.records import DisruptionRecord, parse_disruption_record

    r = parse_disruption_record(_machine())
    assert r.kind == "MACHINE"
    assert r.estimated_downtime == 90
    assert not hasattr(r, "estimated_absence")


def test_worker_record_parses():
    from coe.agents.records import parse_disruption_record

    r = parse_disruption_record({
        "kind": "WORKER", "instance_id": "factory_demo_01",
        "worker_id": "W3", "event_type": "WORKER_ABSENT",
        "occurred_at": 480, "severity": "MEDIUM", "estimated_absence": 240,
        "narrative_excerpt": "sick"})
    assert r.kind == "WORKER"


def test_material_record_parses():
    from coe.agents.records import parse_disruption_record

    r = parse_disruption_record({
        "kind": "MATERIAL", "instance_id": "factory_demo_01",
        "material_sku": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
        "occurred_at": 300, "severity": "LOW",
        "narrative_excerpt": "bin empty"})
    assert r.kind == "MATERIAL"


def test_material_cannot_carry_duration():
    with pytest.raises(ValidationError):
        from coe.agents.records import parse_disruption_record

        parse_disruption_record({
            "kind": "MATERIAL", "instance_id": "i",
            "material_sku": "S", "event_type": "MATERIAL_SHORTAGE",
            "occurred_at": 0, "severity": "LOW",
            "estimated_downtime": 10,       # forbidden on MATERIAL
            "narrative_excerpt": "x"})


def test_negative_occurred_at_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.records import parse_disruption_record

        parse_disruption_record(_machine(occurred_at=-1))


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.records import parse_disruption_record

        parse_disruption_record(_machine(event_type="EXPLODED"))


def test_two_resources_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.records import parse_disruption_record

        parse_disruption_record(_machine(worker_id="W3"))


def test_instance_mismatch_rejected(clean_db):
    from sqlalchemy import insert

    from coe.agents.records import RecordValidationError, validate_record_fields
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        session.execute(insert(Instance).values(
            name="rec-inst", source_name="synthetic"))
        with pytest.raises(RecordValidationError, match="instance"):
            validate_record_fields(
                _machine(instance_id="OTHER"), session=session,
                instance_name="rec-inst")


def test_unknown_resource_rejected_per_kind(clean_db):
    from coe.agents.records import RecordValidationError, validate_record_fields
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="rec-inst2", source_name="synthetic")
        session.add(inst)
        session.flush()
        session.add(Material(instance_id=inst.id, sku="MAT-001",
                             initial_stock=100))
        session.flush()
        with pytest.raises(RecordValidationError, match="Machine"):
            validate_record_fields(_machine(instance_id="rec-inst2"),
                                   session=session,
                                   instance_name="rec-inst2")
        ok = validate_record_fields(
            {"kind": "MATERIAL", "instance_id": "rec-inst2",
             "material_sku": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
             "occurred_at": 5, "severity": "LOW", "narrative_excerpt": "x"},
            session=session, instance_name="rec-inst2")
        assert ok["material_sku"] == "MAT-001"


def test_state_defaults_and_threading():
    from coe.agents.state import RecoveryState

    s = RecoveryState(instance_name="factory_demo_01")
    assert s.errors == [] and s.warnings == []
    assert s.strategy_candidates == [] and s.round_count == 0
    s2 = s.model_copy(update={"narrative": "MC-04 seized"})
    assert s2.narrative == "MC-04 seized"
    assert s.narrative == ""          # immutable updates, langgraph-friendly
