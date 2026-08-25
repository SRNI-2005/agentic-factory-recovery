"""Pure strategy applier (spec §6.1): (payload, candidates) -> payload.

No validation lives here — callers pass catalog-validated candidates in
emission order. Later candidates targeting the same job/material REPLACE
earlier effects (last-wins); every application is recorded as a
STRATEGY_APPLIED warning. The caller strips job_tardiness_weights before
calling and re-derives after (ordering contract, P2 §3.1): explicit
TARDINESS_WEIGHT overrides come back as the second tuple element.
"""


def _warn(payload, candidate, rnd, field_changed):
    payload["warnings"].append({
        "type": "STRATEGY_APPLIED", "candidate": candidate,
        "round": rnd, "field_changed": field_changed})


def _find_job(payload, job_id):
    for j in payload["jobs"]:
        if j["job_id"] == job_id:
            return j
    raise KeyError(job_id)


def apply_candidates(payload: dict,
                     candidates: list[dict]) -> tuple[dict, dict[str, float]]:
    """Returns (transformed_payload, explicit_tardiness_weights).

    DEFER offsets REPLACE prior defers of the same job (last-wins, §6.1):
    the pre-run release_time is remembered locally so repeated applications
    substitute rather than compound. No marker keys leak into the payload.
    """
    explicit: dict[str, float] = {}
    bases: dict[str, int] = {}

    def defer(job_id, offset):
        job = _find_job(payload, job_id)
        if job_id not in bases:
            bases[job_id] = job["release_time"]
        job["release_time"] = bases[job_id] + offset

    def suspend(job_id):
        job = _find_job(payload, job_id)
        for op in job["operations"]:
            if op["status"] == "PENDING":
                op["status"] = "BLOCKED"
                op["alternatives"] = []
                op["materials"] = []
                payload["blocked_operations"].append({
                    "operation_id": op["operation_id"],
                    "reason": "JOB_SUSPENDED", "material_sku": None})
        if job_id not in payload["suspended_jobs"]:
            payload["suspended_jobs"].append(job_id)
            payload["suspended_jobs"].sort()

    def expedite(sku, quantity, available_at):
        payload["material_receipts"].append({
            "sku": sku, "quantity": quantity, "available_at": available_at,
            "source": "strategy_agent"})
        payload["material_receipts"].sort(
            key=lambda r: (r["sku"], r["available_at"], r["quantity"]))

    for item in candidates:
        c, rnd = item["candidate"], item.get("round", 0)
        t = c["type"]
        if t == "DEFER_JOB":
            defer(c["job_id"], c["release_offset"])
            _warn(payload, c, rnd, "release_time")
        elif t == "SUSPEND_JOB":
            suspend(c["job_id"])
            _warn(payload, c, rnd, "suspended_jobs")
        elif t == "EXPEDITE_MATERIAL":
            expedite(c["material_sku"], c["quantity"], c["available_at"])
            _warn(payload, c, rnd, f"material_receipts[{c['material_sku']}]")
        elif t == "WEIGHT_PRESET":
            payload["config"]["alpha"] = float(c["alpha"])
            payload["config"]["beta"] = float(c["beta"])
            _warn(payload, c, rnd, "config.alpha_beta")
        elif t == "TARDINESS_WEIGHT":
            explicit[c["job_id"]] = float(c["weight"])
            _warn(payload, c, rnd, f"job_tardiness_weights[{c['job_id']}]")
        else:
            raise ValueError(f"unreachable candidate type {t!r} "
                             "(validator gap)")
    return payload, explicit
