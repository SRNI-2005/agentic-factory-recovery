"""Pure CP-SAT solver: payload JSON in -> solution JSON out (spec §3.2, §6).

No database access, no LLM calls, no side effects. Deterministic by contract:
num_search_workers=1 plus insertion-ordered construction over the payload's
ordered lists. Half-open time convention: [start, end).
"""
import time

from ortools.sat.python import cp_model
from ortools.sat.python.cp_model import CpModel, CpSolver

from coe.solver.horizon import compute_horizon

_STATUS_NAME = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
}


def _validate_config(cfg: dict) -> None:
    alpha = float(cfg.get("alpha", 1.0))
    beta = float(cfg.get("beta", 1.0))
    if alpha < 0 or beta < 0 or alpha + beta <= 0:
        raise ValueError(
            f"invalid objective weights alpha={alpha} beta={beta}: need "
            "alpha>=0, beta>=0, alpha+beta>0 (spec §9)")


def _combos(op: dict) -> list[tuple[str, str | None, int]]:
    """(machine, worker|None, duration) per eligible combination."""
    out = []
    for alt in op["alternatives"]:
        ws = alt.get("workers") or {}
        if ws:
            out.extend((alt["machine_id"], w, d) for w, d in ws.items())
        else:
            out.append((alt["machine_id"], None, alt["processing_time"]))
    return out


def _effective_beta(payload: dict, job_id: str) -> float:
    weights = payload.get("job_tardiness_weights") or {}
    return float(weights.get(job_id, payload["config"].get("beta", 1.0)))


def _schedule_span(payload: dict, frozen_echo: list) -> int:
    """DEVIATION from the brief listing (documented in task-12 report): upper
    bound for CP-SAT variable domains. The §6.7 horizon cannot serve as the
    variable ub: it omits release+processing and frozen-end+processing
    stacking, never sees worker unavailability, and is inflated by long
    downtimes (turning genuinely infeasible payloads feasible). Any schedule
    can be left-shifted to fit inside this span."""
    release = max((j["release_time"] for j in payload["jobs"]), default=0)
    frozen_end = max((o["frozen"]["end"] for _, o in frozen_echo), default=0)
    processing = 0
    for job in payload["jobs"]:
        for op in job["operations"]:
            if op["status"] != "PENDING":
                continue
            durs = [alt["processing_time"] for alt in op["alternatives"]]
            durs += [d for alt in op["alternatives"]
                     for d in (alt.get("workers") or {}).values()]
            processing += max(durs) if durs else 0
    unavail = sum(uw["until"] - uw["from"]
                  for uw in payload["worker_unavailability"])
    return max(1, release + frozen_end + processing + unavail)


def echo_assignment(job, op) -> dict:
    fz = op["frozen"]
    return {
        "operation_id": op["operation_id"],
        "job_id": job["job_id"],
        "machine_id": fz["machine_id"],
        "worker_id": fz.get("worker_id"),
        "start": fz["start"],
        "end": fz["end"],
        "processing_time": fz["end"] - fz["start"],
        "setup_time": 0,
        "is_frozen": True,
    }


def solve(payload: dict) -> dict:
    t0 = time.monotonic()
    cfg = payload["config"]
    _validate_config(cfg)
    alpha = float(cfg["alpha"])
    normalize = bool(cfg.get("normalize_objectives", True))
    time_limit = float(cfg.get("time_limit_seconds", 60))
    seed = int(cfg.get("random_seed", 42))

    pending, frozen_echo = [], []
    for job in payload["jobs"]:
        for op in job["operations"]:
            if op["status"] == "PENDING":
                pending.append((job, op))
            elif op.get("frozen") is not None:
                frozen_echo.append((job, op))

    def terms_for(ends_by_job: dict[str, int]) -> list[tuple[float, int]]:
        out = []
        for job in payload["jobs"]:
            dl = job["deadline"]
            if dl is None or job["job_id"] not in ends_by_job:
                continue
            out.append((_effective_beta(payload, job["job_id"]),
                        max(0, ends_by_job[job["job_id"]] - dl)))
        return out

    def finish(status, assignments, makespan, terms, horizon_used, dur=None):
        total = round(sum(t for _, t in terms))
        if normalize and horizon_used > 0:
            obj = alpha * makespan / horizon_used + sum(
                w * t / horizon_used for w, t in terms)
        else:
            obj = alpha * makespan + sum(w * t for w, t in terms)
        return {"status": status,
                "objective_value": round(float(obj), 9),
                "makespan": int(makespan),
                "total_tardiness": total,
                "assignments": assignments,
                "solve_duration_seconds": dur if dur is not None
                else round(time.monotonic() - t0, 6)}

    # ---- short circuit: nothing pending ----
    if not pending:
        ends_by_job = {j["job_id"]: o["frozen"]["end"] for j, o in frozen_echo}
        mk = max(ends_by_job.values(), default=0)
        return finish("OPTIMAL",
                      [echo_assignment(j, o) for j, o in frozen_echo],
                      mk, terms_for(ends_by_job), max(mk, 1))

    horizon = compute_horizon(
        jobs=payload["jobs"],
        machine_downtime=payload["machine_downtime"],
        setup_times=payload["setup_times"],
        frozen_max_end=max((o["frozen"]["end"] for _, o in frozen_echo),
                           default=0),
    )
    span = _schedule_span(payload, frozen_echo)

    model = CpModel()
    machine_iv: dict[str, list] = {}
    worker_iv: dict[str, list] = {}

    # frozen anchors: fixed busy blocks on their resources
    for j, o in frozen_echo:
        fz = o["frozen"]
        iv = model.NewIntervalVar(fz["start"], fz["end"] - fz["start"],
                                  fz["end"], f"fz_{o['operation_id']}")
        machine_iv.setdefault(fz["machine_id"], []).append(iv)
        if fz.get("worker_id"):
            worker_iv.setdefault(fz["worker_id"], []).append(iv)

    # per-job chains across ALL ops; frozen constants anchor the chain
    ops_in_job: dict[str, list[dict]] = {}
    for j, o in [*frozen_echo, *pending]:
        ops_in_job.setdefault(j["job_id"], []).append(o)
    for olist in ops_in_job.values():
        olist.sort(key=lambda o: o["sequence"])

    combo_by_op: dict[str, list[dict]] = {}
    last_end_expr: dict[str, object] = {}
    prev_end: dict[str, object] = {}
    release_of = {j["job_id"]: j["release_time"] for j in payload["jobs"]}
    for jid, olist in ops_in_job.items():
        for o in olist:
            if o.get("frozen") is not None:
                prev_end[jid] = o["frozen"]["end"]
                continue
            oid_ = o["operation_id"]
            s = model.NewIntVar(release_of[jid], span, f"s_{oid_}")
            e = model.NewIntVar(release_of[jid], span, f"e_{oid_}")
            if prev_end.get(jid) is not None:
                model.Add(s >= prev_end[jid])
            prev_end[jid] = e
            last_end_expr[jid] = e

            combos = []
            for m, w, d in _combos(o):
                lit = model.NewBoolVar(f"x_{oid_}_{m}_{w}")
                sv = model.NewIntVar(0, span, f"sv_{oid_}_{m}_{w}")
                ev = model.NewIntVar(0, span, f"ev_{oid_}_{m}_{w}")
                iv = model.NewOptionalIntervalVar(sv, d, ev, lit,
                                                  f"iv_{oid_}_{m}_{w}")
                model.Add(s == sv).OnlyEnforceIf(lit)
                model.Add(e == ev).OnlyEnforceIf(lit)
                machine_iv.setdefault(m, []).append(iv)
                if w is not None:
                    worker_iv.setdefault(w, []).append(iv)
                combos.append({"lit": lit, "machine": m, "worker": w,
                               "dur": d, "start": s, "end": e})
            model.AddExactlyOne([c["lit"] for c in combos])
            combo_by_op[oid_] = combos

    # downtime + unavailability as fixed blocks
    for wdw in payload["machine_downtime"]:
        until = wdw["until"] if wdw["until"] is not None else horizon
        size = max(1, until - wdw["from"])
        iv = model.NewIntervalVar(wdw["from"], size, until,
                                  f"dt_{wdw['machine_id']}_{wdw['from']}")
        machine_iv.setdefault(wdw["machine_id"], []).append(iv)
    for i, uw in enumerate(payload["worker_unavailability"]):
        iv = model.NewIntervalVar(uw["from"], max(1, uw["until"] - uw["from"]),
                                  uw["until"], f"uw_{uw['worker_id']}_{i}")
        worker_iv.setdefault(uw["worker_id"], []).append(iv)

    for ivs in machine_iv.values():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)
    for ivs in worker_iv.values():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # ---- tardiness, makespan, objective ----
    tardy_vars = []
    for job in payload["jobs"]:
        end_expr = last_end_expr.get(job["job_id"])
        if job["deadline"] is None or end_expr is None:
            continue
        t = model.NewIntVar(0, span, f"t_{job['job_id']}")
        model.AddMaxEquality(t, [0, end_expr - job["deadline"]])
        tardy_vars.append((_effective_beta(payload, job["job_id"]), t))

    all_ends = [prev_end[j] for j in ops_in_job if j in prev_end]
    makespan_var = model.NewIntVar(0, span, "makespan")
    model.AddMaxEquality(makespan_var, all_ends or [0])

    if normalize:
        obj_expr = alpha * makespan_var / horizon + sum(
            (w / horizon) * t for w, t in tardy_vars)
    else:
        obj_expr = alpha * makespan_var + sum(w * t for w, t in tardy_vars)
    model.Minimize(obj_expr)

    solver = CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    status_code = solver.Solve(model)
    duration = round(time.monotonic() - t0, 6)

    label = _STATUS_NAME.get(status_code, "INFEASIBLE")
    if label == "INFEASIBLE":
        # CONTRACT deviation from listing: frozen echoes are retained on
        # INFEASIBLE; only the live set is emptied (Interfaces §13 bullet).
        assignments = [echo_assignment(j, o) for j, o in frozen_echo]
        mk = max((a["end"] for a in assignments), default=0)
        return finish(label, assignments, mk,
                      terms_for({j["job_id"]: o["frozen"]["end"]
                                 for j, o in frozen_echo}),
                      horizon, dur=duration)

    assignments = [echo_assignment(j, o) for j, o in frozen_echo]
    ends_solved: dict[str, int] = {}
    for j, o in pending:
        oid_ = o["operation_id"]
        chosen = next(c for c in combo_by_op[oid_]
                      if solver.BooleanValue(c["lit"]))
        st = int(solver.Value(chosen["start"]))
        en = int(solver.Value(chosen["end"]))
        assignments.append({
            "operation_id": oid_,
            "job_id": j["job_id"],
            "machine_id": chosen["machine"],
            "worker_id": chosen["worker"],
            "start": st,
            "end": en,
            "processing_time": en - st,
            "setup_time": 0,
            "is_frozen": False,
        })
        ends_solved[j["job_id"]] = max(ends_solved.get(j["job_id"], 0), en)

    merged = {j["job_id"]: o["frozen"]["end"] for j, o in frozen_echo}
    merged.update(ends_solved)
    return finish(label, assignments, max(a["end"] for a in assignments),
                  terms_for(merged), horizon, dur=duration)
