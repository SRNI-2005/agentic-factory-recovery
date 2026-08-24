"""Closed strategy catalog (spec §5) + candidate verdicts (§4.3 step 2).

Anything outside the union dies at schema level before reaching the
applier. Verdicts are deterministic functions of DB state + db_facts.
"""
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class TardinessWeightCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["TARDINESS_WEIGHT"]
    job_id: str
    weight: float = Field(ge=0, le=10)


class DeferJobCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["DEFER_JOB"]
    job_id: str
    release_offset: int = Field(ge=0)


class SuspendJobCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SUSPEND_JOB"]
    job_id: str


class ExpediteMaterialCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["EXPEDITE_MATERIAL"]
    material_sku: str
    quantity: float = Field(gt=0)
    available_at: int = Field(ge=0)


class WeightPresetCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["WEIGHT_PRESET"]
    alpha: float = Field(ge=0)
    beta: float = Field(ge=0)

    @model_validator(mode="after")
    def _positive_sum(self) -> "WeightPresetCandidate":
        if self.alpha + self.beta <= 0:
            raise ValueError("alpha + beta must be > 0")
        return self


StrategyCandidate = Annotated[
    Union[TardinessWeightCandidate, DeferJobCandidate,
          SuspendJobCandidate, ExpediteMaterialCandidate,
          WeightPresetCandidate],
    Field(discriminator="type"),
]

_candidate_adapter: TypeAdapter = TypeAdapter(StrategyCandidate)


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _job_row(session, iid, job_id):
    from coe.db.models.fjsp import Job

    return (session.query(Job)
            .filter(Job.instance_id == iid, Job.name == job_id)
            .one_or_none())


def _latest_active_version_id(session, iid):
    from coe.db.models.schedule import ScheduleVersion

    row = (session.query(ScheduleVersion.id)
           .filter(ScheduleVersion.instance_id == iid,
                   ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                   ScheduleVersion.rolled_back.is_(False))
           .order_by(ScheduleVersion.version_number.desc(),
                     ScheduleVersion.id.desc()).first())
    return None if row is None else row[0]


def _op_ids(session, iid, job) -> list[int]:
    from coe.db.models.fjsp import Operation

    return [row[0] for row in session.query(Operation.id)
            .filter(Operation.instance_id == iid,
                    Operation.job_id == job.id).all()]


def _has_unfinished_op(session, iid, job, clock) -> bool:
    """True iff some operation of the job has no active-version entry that
    ended at/before the clock (i.e. work remains that this run can shape)."""
    from coe.db.models.schedule import ScheduleEntry

    vid = _latest_active_version_id(session, iid)
    if vid is None:
        return True
    done = set(row[0] for row in
               session.query(ScheduleEntry.operation_id)
               .filter(ScheduleEntry.version_id == vid,
                       ScheduleEntry.end_time <= clock).all())
    ops = _op_ids(session, iid, job)
    return any(o not in done for o in ops)


def _history_exists(session, iid, job, clock) -> bool:
    """§5 SUSPEND_JOB rule: any active entry starting at/before clock."""
    from coe.db.models.schedule import ScheduleEntry

    vid = _latest_active_version_id(session, iid)
    if vid is None:
        return False
    rows = (session.query(ScheduleEntry)
            .filter(ScheduleEntry.version_id == vid,
                    ScheduleEntry.operation_id.in_(
                        _op_ids(session, iid, job))).all())
    return any(e.start_time <= clock for e in rows)


def validate_candidate(data: dict, *, session, instance_name: str,
                       db_facts: dict, reference_clock: int,
                       prior_this_round: list[dict]) -> tuple[str, str]:
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance

    inst = (session.query(Instance)
            .filter(Instance.name == instance_name).one())

    if any(_canonical(data) == _canonical(p) for p in prior_this_round):
        return "INVALID_DUPLICATE", "duplicate"

    try:
        cand = _candidate_adapter.validate_python(data)
    except Exception as exc:
        first = getattr(exc, "errors", lambda: [{"msg": str(exc)}])()[0]
        return "INVALID", f"out_of_bounds: {first.get('msg', str(exc))}"

    t = cand.type
    if t in ("TARDINESS_WEIGHT", "DEFER_JOB", "SUSPEND_JOB"):
        job = _job_row(session, inst.id, cand.job_id)
        if job is None:
            return "INVALID", "unknown_job"
        # SUSPEND history precedence: a job whose active-schedule work has
        # already started/finished is exactly the §5 no-resuspend case.
        if t == "SUSPEND_JOB" and _history_exists(
                session, inst.id, job, reference_clock):
            return "INVALID", "suspension_has_history"
        if not _has_unfinished_op(session, inst.id, job, reference_clock):
            return "INVALID", "job_not_pending"
        return "VALID", "ok"
    if t == "EXPEDITE_MATERIAL":
        hit = (session.query(Material.id)
               .filter(Material.instance_id == inst.id,
                       Material.sku == cand.material_sku)
               .one_or_none())
        if hit is None:
            return "INVALID", "unknown_material"
        horizon = db_facts.get("projected_horizon")
        if horizon is not None and cand.available_at >= horizon:
            return "VALID_WITH_WARNING", "effect_beyond_horizon"
        return "VALID", "ok"
    return "VALID", "ok"      # WEIGHT_PRESET: schema already bounded it
