"""§5 closed catalog + §4.3(2) verdicts."""
import pytest
from pydantic import ValidationError


# NOTE: plan-code deviation — an Annotated discriminated-union alias carries
# no .model_validate on CPython 3.14 (setattr delegates to the frozen Union
# origin), so schema-level checks go through the module TypeAdapter.
def _schema_validate(data):
    from coe.agents.catalog import _candidate_adapter

    return _candidate_adapter.validate_python(data)


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        _schema_validate({"type": "TELEPORT", "job_id": "J"})


def test_weight_preset_zero_sum_rejected():
    with pytest.raises(ValidationError):
        _schema_validate({"type": "WEIGHT_PRESET", "alpha": 0, "beta": 0})


def test_weight_out_of_bounds_schema_level():
    with pytest.raises(ValidationError):
        _schema_validate(
            {"type": "TARDINESS_WEIGHT", "job_id": "J-1", "weight": 11})


@pytest.fixture()
def world(clean_db):
    """J-HIST holds completed history; J-FRESH untouched; MAT-X known."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="cat-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m = Machine(instance_id=iid, name="M1")
        w = Worker(instance_id=iid, name="W1")
        session.add_all([m, w])
        session.flush()

        jh = Job(instance_id=iid, name="J-HIST", priority=1)
        jf = Job(instance_id=iid, name="J-FRESH", priority=2, deadline=200)
        session.add_all([jh, jf])
        session.flush()
        oh = Operation(instance_id=iid, job_id=jh.id, sequence_number=1)
        of = Operation(instance_id=iid, job_id=jf.id, sequence_number=1)
        session.add_all([oh, of])
        session.flush()
        for o in (oh, of):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=o.id, machine_id=m.id,
                processing_time=5))

        session.add(Material(instance_id=iid, sku="MAT-X",
                             initial_stock=10))
        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=20,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.1,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v1)
        session.flush()
        # history: J-HIST op fully in the past relative to clock=100
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oh.id,
            machine_id=m.id, worker_id=w.id, start_time=0, end_time=5,
            processing_time=5, is_frozen=True, status="FROZEN"))
        session.flush()
    return iid


def _validate(candidate, *, world, db_facts=None, clock=100, prior=None):
    from coe.agents.catalog import validate_candidate
    from coe.db.session import session_scope

    with session_scope() as session:
        return validate_candidate(
            candidate, session=session, instance_name="cat-world",
            db_facts=db_facts or {"projected_horizon": 500},
            reference_clock=clock, prior_this_round=prior or [])


def test_tardiness_valid_and_reasons(world):
    assert _validate({"type": "TARDINESS_WEIGHT", "job_id": "J-FRESH",
                      "weight": 0.5}, world=world) == ("VALID", "ok")
    assert _validate({"type": "TARDINESS_WEIGHT", "job_id": "J-GHOST",
                      "weight": 1}, world=world)[0] == "INVALID"
    v, r = _validate({"type": "TARDINESS_WEIGHT", "job_id": "J-HIST",
                      "weight": 1}, world=world)
    assert (v, r) == ("INVALID", "job_not_pending")


def test_suspend_rejects_history(world):
    assert _validate({"type": "SUSPEND_JOB", "job_id": "J-FRESH"},
                     world=world) == ("VALID", "ok")
    v, r = _validate({"type": "SUSPEND_JOB", "job_id": "J-HIST"},
                     world=world)
    assert v == "INVALID" and r == "suspension_has_history"


def test_expedite_beyond_horizon_warns(world):
    ok = _validate({"type": "EXPEDITE_MATERIAL", "material_sku": "MAT-X",
                    "quantity": 5, "available_at": 100}, world=world)
    late = _validate({"type": "EXPEDITE_MATERIAL", "material_sku": "MAT-X",
                      "quantity": 5, "available_at": 900}, world=world)
    ghost = _validate({"type": "EXPEDITE_MATERIAL", "material_sku": "NOPE",
                       "quantity": 5, "available_at": 100}, world=world)
    assert ok == ("VALID", "ok")
    assert late == ("VALID_WITH_WARNING", "effect_beyond_horizon")
    assert ghost == ("INVALID", "unknown_material")


def test_duplicate_detection_canonical(world):
    c = {"type": "DEFER_JOB", "job_id": "J-FRESH", "release_offset": 30}
    dup = {"release_offset": 30, "job_id": "J-FRESH",
           "type": "DEFER_JOB"}          # same candidate, different key order
    assert _validate(c, world=world) == ("VALID", "ok")
    assert _validate(dup, world=world, prior=[c])[0] == "INVALID_DUPLICATE"
