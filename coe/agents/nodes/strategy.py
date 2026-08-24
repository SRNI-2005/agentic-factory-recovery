"""Strategy round node (AI Role 2, spec §4.3).

Negotiation rounds are LLM-driven EXCEPT the material-reactive duty
(Amendment 2026-08-24), which is a fixed deterministic procedure executed
without any LLM participation. Every emitted candidate is validated before
entering state; verdicts accumulate in round_verdicts (also the audit
buffer flushed to recovery_proposals at run finish).
"""
import json

from sqlalchemy.orm import Session

from coe.agents.catalog import _candidate_adapter, validate_candidate
from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine

_SYSTEM_PROMPT = """You are a factory recovery strategist. Given database \
facts and prior verdicts, emit STRICT JSON:
{"candidates": [<catalog entries>], "final": true|false}
Catalog (closed, discriminated on "type"):
{"type":"TARDINESS_WEIGHT","job_id":str,"weight":number 0..10}
{"type":"DEFER_JOB","job_id":str,"release_offset":int>=0}
{"type":"SUSPEND_JOB","job_id":str}
{"type":"EXPEDITE_MATERIAL","material_sku":str,"quantity":number>0,\
"available_at":int>=0}
{"type":"WEIGHT_PRESET","alpha":number>=0,"beta":number>=0}  (sum>0)
Rules: only candidates from the catalog; final=true when you have nothing \
further to propose; prefer minimal interventions."""


def _consuming_jobs(payload: dict, sku: str) -> list[dict]:
    out = []
    for j in payload["jobs"]:
        if j["job_id"] in (payload.get("suspended_jobs") or []):
            continue
        demand = sum(m["quantity"]
                     for o in j["operations"] if o["status"] == "PENDING"
                     for m in o.get("materials", [])
                     if m["sku"] == sku)
        if demand > 0:
            slack = (j["deadline"] - j["release_time"]
                     if j.get("deadline") is not None else float("inf"))
            out.append({"job_id": j["job_id"], "priority": j["priority"],
                        "slack": slack,
                        "release_time": j["release_time"]})
    out.sort(key=lambda x: (x["priority"], x["slack"]))
    return out


def material_reactive_plan(state: RecoveryState) -> dict:
    """Amendment 2026-08-24 steps 1-5, fully deterministic (no LLM)."""
    payload = state.compiled_payload or {}
    warnings = [w for w in payload.get("warnings", [])
                if w.get("type") == "MATERIAL_SHORTFALL"]
    horizon = state.db_facts.get("projected_horizon")
    candidates, notes = [], []
    for w in warnings:
        sku, supply, demand = (w["material_sku"], w["total_supply"],
                               w["total_demand"])
        consumers = _consuming_jobs(payload, sku)
        if not consumers:
            continue
        sacrifice = consumers[-1]
        protector = consumers[0]["job_id"]
        covering = [r for r in payload.get("material_receipts", [])
                    if r["sku"] == sku and r["quantity"] >= demand - supply
                    and (horizon is None or r["available_at"] < horizon)]
        covering.sort(key=lambda r: (r["available_at"], r["quantity"]))
        if covering:
            r = covering[0]
            candidates.append({
                "type": "DEFER_JOB", "job_id": sacrifice["job_id"],
                "release_offset":
                    max(0, r["available_at"] - sacrifice["release_time"])})
            notes.append(f"{sacrifice['job_id']} deferred so {protector} "
                         f"keeps the {sku}")
        else:
            candidates.append({"type": "SUSPEND_JOB",
                               "job_id": sacrifice["job_id"]})
            notes.append(f"{sacrifice['job_id']} suspended so {protector} "
                         f"keeps the {sku}")
    return {"candidates": candidates, "final": True,
            "note": "; ".join(notes)}


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in LLM response: {text[:200]!r}")
    return json.loads(stripped[start:end + 1])


def run_strategy_round(state: RecoveryState, *, client=None,
                       max_retries: int | None = None) -> RecoveryState:
    settings = get_settings()
    retries = (settings.llm_max_retries if max_retries is None
               else max_retries)
    rnd = state.round_count + 1

    if state.material_reactive:
        plan = material_reactive_plan(state)
        source = "deterministic"
    elif client is not None:
        user = json.dumps({"instance": state.instance_name,
                           "db_facts": state.db_facts,
                           "prior_verdicts": state.round_verdicts},
                          sort_keys=True)
        feedback = ""
        plan, source = None, "llm"
        for _attempt in range(1 + retries):
            try:
                raw = client.complete(system=_SYSTEM_PROMPT,
                                      user=user + feedback)
                parsed = _extract_json(raw)
                if not isinstance(parsed.get("candidates"), list) \
                        or not isinstance(parsed.get("final"), bool):
                    raise ValueError("missing candidates/final fields")
                plan = parsed
                break
            except (ValueError, json.JSONDecodeError) as exc:
                feedback = f"\n\nRejected: {exc}. Respond again."
                plan = None
        if plan is None:
            return state.model_copy(update={
                "round_count": rnd, "strategy_final": True,
                "warnings": state.warnings + [
                    "strategy_loop fallback: proceeding without strategy "
                    "(§3.3)"]})
        raw_candidates = plan["candidates"]
    else:
        return state.model_copy(update={"round_count": rnd,
                                        "strategy_final": True})

    new_candidates, verdicts = [], []
    prior_this_round: list[dict] = []
    with Session(make_engine()) as session:
        for data in raw_candidates:
            try:
                _candidate_adapter.validate_python(data)
            except Exception:
                continue        # non-catalog junk dies at schema (§5)
            verdict, reason = validate_candidate(
                data, session=session,
                instance_name=state.instance_name,
                db_facts=state.db_facts,
                reference_clock=state.reference_clock,
                prior_this_round=prior_this_round)
            prior_this_round.append(data)
            new_candidates.append({"candidate": data, "round": rnd})
            verdicts.append({"candidate": data, "round": rnd,
                             "verdict": verdict, "reason": reason})

    warnings = list(state.warnings)
    if source == "deterministic" and plan.get("note"):
        warnings.append(plan["note"])

    final_flag = bool(plan.get("final")) or source == "deterministic"
    return state.model_copy(update={
        "round_count": rnd,
        "strategy_final": final_flag,
        "strategy_candidates": state.strategy_candidates + new_candidates,
        "round_verdicts": state.round_verdicts + verdicts,
        "warnings": warnings,
    })
