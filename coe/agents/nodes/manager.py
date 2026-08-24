"""Manager compile node (spec §4.4): DB -> payload -> applier -> weights."""
import json

from sqlalchemy.orm import Session

from coe.agents.applier import apply_candidates
from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine
from coe.solver.payload_builder import (
    build_payload,
    derive_tardiness_weights,
)


class NoBaselineError(RuntimeError):
    """RECOVERY needs an active schedule; tell the operator to baseline."""


def _session():
    return Session(make_engine())


def _canon(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _latest_verdicts(state: RecoveryState) -> dict:
    """canonical(candidate json) -> latest verdict entry."""
    latest: dict[str, dict] = {}
    for v in state.round_verdicts:
        key = _canon(v["candidate"])
        prev = latest.get(key)
        if prev is None or v["round"] >= prev["round"]:
            latest[key] = v
    return latest


def run_manager_compile(state: RecoveryState) -> RecoveryState:
    s = get_settings()
    with _session() as session:
        from coe.db.models.provenance import Instance

        inst = (session.query(Instance)
                .filter(Instance.name == state.instance_name).one())

        rec = state.disruption_record or {}
        failed = ((rec["machine_id"],)
                  if rec.get("kind") == "MACHINE" else ())

        try:
            payload = build_payload(
                session, instance_row=inst,
                alpha=s.solver_alpha_weight, beta=s.solver_beta_weight,
                time_limit_seconds=s.solver_time_limit_seconds,
                random_seed=s.solver_random_seed,
                num_search_workers=s.solver_num_search_workers,
                normalize_objectives=s.solver_normalize_objectives,
                schedule_type="RECOVERY",
                now=state.reference_clock,
                failed_machine_names=failed)
        except ValueError as exc:
            if "active schedule" in str(exc):
                raise NoBaselineError(
                    f"{state.instance_name} has no active schedule — run "
                    "`solve baseline` before recovering (§4.4)") from exc
            raise

    payload.pop("job_tardiness_weights", None)     # ordering contract §6.1

    latest = _latest_verdicts(state)
    applicable = [
        {"candidate": c["candidate"], "round": c["round"]}
        for c in state.strategy_candidates
        if latest.get(_canon(c["candidate"]), {}).get("verdict")
        in ("VALID", "VALID_WITH_WARNING")
    ]
    payload, explicit = apply_candidates(payload, applicable)

    derived = derive_tardiness_weights(payload["jobs"],
                                       payload["config"]["beta"]) or {}
    merged = {**derived, **explicit}
    if merged:
        payload["job_tardiness_weights"] = merged

    reactive = any(w.get("type") == "MATERIAL_SHORTFALL"
                   for w in payload["warnings"])
    return state.model_copy(update={
        "compiled_payload": payload,
        "material_reactive": reactive,
    })
