"""Pure CP-SAT solver: payload JSON in -> solution JSON out (spec §3.2, §6).

No database access, no LLM calls, no side effects. Deterministic by contract:
num_search_workers=1 plus insertion-ordered construction over the payload's
ordered lists. Half-open time convention: [start, end).

Hardening E: solve() runs two-phase relax-then-repair. Phase A solves the
relaxation without the AddCircuit setup layer (proven instantly tractable);
its solution is replayed onto the full phase-B model as a COMPLETE hint —
chosen combo literals, s/e ints, makespan, tardy vars, and the circuit
literals implied by phase A's per-machine order (complete hints prune
search; partial hints make CP-SAT waste time completing them — cpsat-primer
parameters chapter, or-stackexchange 10406). A starved phase A falls back to
the deterministic _greedy_plan hint. Total wall usage ≈ time_limit plus a
small phase-A overshoot tolerance; byte-determinism remains tied to OPTIMAL
termination (cutoff-jitter caveat).
"""
import time

from ortools.sat.python import cp_model
from ortools.sat.python.cp_model import CpModel, CpSolver

from coe.solver.horizon import compute_horizon


def _validate_config(cfg: dict) -> None:
    alpha = float(cfg.get("alpha", 1.0))
    beta = float(cfg.get("beta", 1.0))
    if alpha < 0 or beta < 0 or alpha + beta <= 0:
        raise ValueError(
            f"invalid objective weights alpha={alpha} beta={beta}: need "
            "alpha>=0, beta>=0, alpha+beta>0 (spec §9)")


def _require_valid_window(w_from: int, w_until, label: str) -> int:
    if w_until is None:
        raise ValueError(f"{label}: open-ended window cannot be a fixed block")
    if w_until <= w_from:
        raise ValueError(
            f"{label}: malformed window [{w_from}, {w_until})")
    return w_until - w_from


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


def _schedule_span(payload: dict, frozen_echo: list,
                   receipt_times: list[int] | None = None,
                   machine_downtime: list | None = None) -> int:
    """DEVIATION from the brief listing (documented in task-12 report): upper
    bound for CP-SAT variable domains. The §6.7 horizon cannot serve as the
    variable ub: it omits release+processing and frozen-end+processing
    stacking, never sees worker unavailability, and is inflated by long
    downtimes (turning genuinely infeasible payloads feasible). Any schedule
    can be left-shifted to fit inside this span. Riders (task-12B): latest
    counted material receipt must fit inside the domains (waiting for a
    delivery is schedulable), and TEMPORARY machine downtimes likewise only
    delay starts; open-ended windows contribute nothing to the bound."""
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
                  for uw in payload["worker_unavailability"]
                  if uw["until"] is not None)
    downtime = sum(wdw["until"] - wdw["from"]
                   for wdw in (machine_downtime or [])
                   if wdw["until"] is not None)
    # RIDER (task-13 review): sequence-dependent setups consume real time
    # between ops (§6.6), and they STACK: a machine running n pending ops
    # needs up to n-1 inter-op transitions plus at most one initial-family
    # setup. Reserving only one overall maximum under-counted multi-
    # transition stacks and produced false INFEASIBLEs, so reserve per
    # machine: transitions_m * max_row_m.
    init_fam = payload.get("machine_initial_families") or {}
    max_row: dict[str, int] = {}
    init_positive: set[str] = set()
    for row in payload["setup_times"]:
        d = int(row["duration"])
        if d > 0:
            mid = row["machine_id"]
            max_row[mid] = max(max_row.get(mid, 0), d)
            if row.get("from_family") == init_fam.get(mid):
                init_positive.add(mid)
    setup_headroom = 0
    for mid in sorted(max_row):
        eligible = sum(
            1 for job in payload["jobs"] for op in job["operations"]
            if op["status"] == "PENDING"
            and any(alt["machine_id"] == mid
                    for alt in op["alternatives"]))
        if not eligible:
            continue
        transitions = eligible - 1 + (mid in init_positive)
        setup_headroom += transitions * max_row[mid]
    return max(1, release + frozen_end + processing + unavail + downtime
               + max(receipt_times or [0]) + setup_headroom)


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


def _split_ops(payload: dict) -> tuple[list, list]:
    """(pending, frozen_echo) in payload iteration order."""
    pending, frozen_echo = [], []
    for job in payload["jobs"]:
        for op in job["operations"]:
            if op["status"] == "PENDING":
                pending.append((job, op))
            elif op.get("frozen") is not None:
                frozen_echo.append((job, op))
    return pending, frozen_echo


def _greedy_plan(payload, pending, combo_by_op, frozen_echo, *,
                 fam_of=None, setup_lookup=None):
    """Deterministic list schedule behind _greedy_hint: min-completion
    pointer dispatch (ties by job_id then sequence), setups and machine
    downtime and worker unavailability respected, append-only per machine
    so the hinted time order matches the hinted circuit order. Returns
    (live placements, per-machine hinted operation order)."""
    release_of: dict[str, int] = {}
    for j, _ in pending:
        release_of.setdefault(j["job_id"], j["release_time"])

    # hardening E fix: frozen anchors close their job's chain — a pending
    # successor may not be hinted before its frozen predecessor's end (the
    # model enforces s >= prev_end across frozen ops; ignoring it produced
    # chain-violating warm starts on every RECOVERY payload).
    last_end: dict[str, int] = {}
    for j, o in frozen_echo:
        jid_ = j["job_id"]
        last_end[jid_] = max(last_end.get(jid_, 0), o["frozen"]["end"])

    mach_busy: dict[str, list[list[int]]] = {}
    work_busy: dict[str, list[list[int]]] = {}

    def _blocks(busy, key, s, e):
        busy.setdefault(key, []).append([s, e])

    for wd in payload["machine_downtime"]:
        u = wd["until"] if wd["until"] is not None else 10 ** 9
        if u > wd["from"]:
            _blocks(mach_busy, wd["machine_id"], wd["from"], u)
    for uw in payload["worker_unavailability"]:
        u = uw["until"] if uw["until"] is not None else 10 ** 9
        if u > uw["from"]:
            _blocks(work_busy, uw["worker_id"], uw["from"], u)
    # hardening C1: frozen echoes are historic fact — the machine (and any
    # worker) stays occupied; the latest block on a machine seeds its setup
    # state so setup-aware placement starts from reality.
    last_frozen: dict[str, tuple[int, object]] = {}
    for j, o in frozen_echo:
        fz = o["frozen"]
        _blocks(mach_busy, fz["machine_id"], fz["start"], fz["end"])
        if fz.get("worker_id"):
            _blocks(work_busy, fz["worker_id"], fz["start"], fz["end"])
        prev = last_frozen.get(fz["machine_id"])
        if prev is None or fz["end"] > prev[0]:
            last_frozen[fz["machine_id"]] = (fz["end"], j.get("family_id"))

    def _fit(blocks_list, nb, d):
        t = nb
        moved = True
        while moved:
            moved = False
            for bs, be in blocks_list:
                if bs < t + d and t < be:
                    t = be
                    moved = True
        return t

    def _fit_pref(blocks_list, nb, pre, d):
        """Earliest s >= nb with [s-pre, s+d) disjoint from blocks."""
        s = max(nb, pre)
        moved = True
        while moved:
            moved = False
            for bs, be in blocks_list:
                if bs < s + d and s - pre < be:
                    s = be + pre
                    moved = True
        return s

    fam_of = fam_of or {}
    setup_lookup = setup_lookup or {}
    init_fam = payload.get("machine_initial_families") or {}
    ops_by_job: dict[str, dict[int, dict]] = {}
    for j, o in pending:
        ops_by_job.setdefault(j["job_id"], {})[o["sequence"]] = o

    # last_end seeded from frozen echoes above (chain closure)
    mach_order: dict[str, list[str]] = {}
    mach_last_end: dict[str, int] = {}
    mach_last_fam: dict[str, object] = dict(init_fam)
    for mid, (_, fam) in last_frozen.items():
        if fam is not None:
            mach_last_fam[mid] = fam
    placements: list[dict] = []
    while any(seqs for seqs in ops_by_job.values()):
        best = None
        for jid in sorted(ops_by_job):
            seqs = ops_by_job[jid]
            if not seqs:
                continue
            seq = min(seqs)
            o = seqs[seq]
            nb_job = max(release_of.get(jid, 0), last_end.get(jid, 0))
            for c in combo_by_op[o["operation_id"]]:
                m = c["machine"]
                pre = int(setup_lookup.get(
                    (m, mach_last_fam.get(m),
                     fam_of.get(o["operation_id"])), 0))
                s = _fit_pref(mach_busy.get(m, []),
                              max(nb_job, mach_last_end.get(m, 0)),
                              pre, c["dur"])
                if c["worker"]:
                    sw = _fit(work_busy.get(c["worker"], []), s, c["dur"])
                    while sw != s:
                        s = _fit_pref(mach_busy.get(m, []), sw, pre,
                                      c["dur"])
                        sw = _fit(work_busy.get(c["worker"], []), s,
                                  c["dur"])
                key = (s + c["dur"], m, c["worker"] or "")
                if best is None or key < best[0]:
                    best = (key, jid, seq, o, c, s, pre)
        _, jid, seq, o, chosen, s, pre = best
        oid_ = o["operation_id"]
        m = chosen["machine"]
        e = s + chosen["dur"]
        _blocks(mach_busy, m, s - pre, e)
        if chosen["worker"]:
            _blocks(work_busy, chosen["worker"], s, e)
        placements.append({"operation_id": oid_, "job_id": jid,
                           "machine_id": m, "worker_id": chosen["worker"],
                           "start": s, "end": e})
        last_end[jid] = e
        mach_order.setdefault(m, []).append(oid_)
        mach_last_end[m] = e
        mach_last_fam[m] = fam_of.get(oid_)
        del ops_by_job[jid][seq]

    return placements, mach_order


def _emit_hint(model, placements, mach_order, combo_by_op, *, span: int,
               circuit_info=None) -> None:
    """Emit a warm start from (placements, per-machine order): chosen combo
    literals, s/e ints, then the §6.6 circuit literal completion."""
    for h in placements:
        oid_ = h["operation_id"]
        chosen = next(c for c in combo_by_op[oid_]
                      if c["machine"] == h["machine_id"]
                      and c["worker"] == h["worker_id"])
        model.AddHint(chosen["lit"], 1)
        for c in combo_by_op[oid_]:
            if c is not chosen:
                model.AddHint(c["lit"], 0)
        model.AddHint(chosen["start"], min(h["start"], span))
        model.AddHint(chosen["end"], min(h["end"], span))

    for m in sorted(circuit_info or {}):
        ci = circuit_info[m]
        order = [o for o in mach_order.get(m, []) if o in set(ci["oids"])]
        pairs = set(zip(order, order[1:]))
        oset = set(order)
        model.AddHint(ci["idle"], 0 if order else 1)
        for o in ci["oids"]:
            model.AddHint(ci["on"][o], 1 if o in oset else 0)
            model.AddHint(ci["first"][o],
                          1 if order and o == order[0] else 0)
            model.AddHint(ci["last"][o],
                          1 if order and o == order[-1] else 0)
        for (a, b), lit in ci["arcs"].items():
            model.AddHint(lit, 1 if (a, b) in pairs else 0)


def _greedy_hint(model, payload, pending, combo_by_op, *, span: int,
                 circuit_info=None, fam_of=None, setup_lookup=None,
                 frozen_echo=()) -> None:
    """DEVIATION (task-17, integration finding): the spec pins
    num_search_workers=1 for determinism (spec §9 Configuration), but at demo
    scale (168 ops x ~24 combos) the single-worker search behind the §6.6
    AddCircuit setup layer found no feasible point within 240s (14-job slice:
    131 solutions only after 120s), so baseline recovery never commits. This
    warm-starts CP-SAT's hint-repair with the deterministic list schedule
    from _greedy_plan (frozen echoes occupy their resources). Only the
    material reservoir is left for repair (presolve proves it trivial on
    this data). Purely a search aid: optimal set, seed and worker count are
    untouched, so the determinism contract (§11 Tier 4 / §12-9) holds."""
    placements, mach_order = _greedy_plan(
        payload, pending, combo_by_op, frozen_echo,
        fam_of=fam_of, setup_lookup=setup_lookup)
    _emit_hint(model, placements, mach_order, combo_by_op, span=span,
               circuit_info=circuit_info)



def _add_setups(model, *, payload, pending, combo_by_op, machine_iv, fam_of):
    """Sequence-dependent setups via AddCircuit with dummy nodes (§6.6).

    Node 0 = Dummy Start, node 1 = Dummy End; each eligible pending op is an
    optional node tied to its machine-assignment literals. Arc transits are 0;
    setups are explicit optional intervals [start_j - d, start_j) gated by the
    incoming arc literal (family pairs are static, so no dynamic AND needed).
    Returns ({operation_id: [(arc_literal, minutes), ...]},
             {machine: circuit-literal bookkeeping for hinting})."""
    lookup: dict[tuple[str, str | None, str], int] = {}
    for row in payload["setup_times"]:
        d = int(row["duration"])
        if d > 0:
            key = (row["machine_id"], row.get("from_family"),
                   row["to_family"])
            lookup[key] = max(lookup.get(key, 0), d)

    init_fam = payload.get("machine_initial_families") or {}
    mach_ops: dict[str, list[str]] = {}
    for _, op in pending:
        for c in combo_by_op.get(op["operation_id"], []):
            mach_ops.setdefault(c["machine"], []).append(op["operation_id"])

    setup_choices: dict[str, list[tuple[object, int]]] = {}
    # DEVIATION (task-17): circuit literals are returned so _greedy_hint can
    # emit a complete warm start (see _greedy_hint docstring).
    circuit_info: dict[str, dict] = {}

    # determinism: machines in sorted order, oids in first-appearance order
    for m in sorted(mach_ops):
        oids = list(dict.fromkeys(mach_ops[m]))
        node = {o: i + 2 for i, o in enumerate(oids)}
        f = {o: fam_of.get(o) for o in oids}

        def d_of(ff, tf, _m=m):
            return lookup.get((_m, ff, tf), 0)

        has_any = any(d_of(init_fam.get(m), f[o]) > 0 for o in oids) or any(
            d_of(f[a], f[b]) > 0 for a in oids for b in oids if a != b)
        if not has_any:
            continue  # MK01 fast path: no positive row touches this machine

        on = {o: model.NewBoolVar(f"on_{m}_{o}") for o in oids}
        idle = model.NewBoolVar(f"idle_{m}")
        first = {}
        last = {}
        arc_lit: dict[tuple[str, str], object] = {}
        arcs: list[tuple[int, int, object]] = []
        arcs.append((0, 1, idle))            # S->E iff nothing lands here
        # DEVIATION from brief listing: ortools 9.15 rejects None as an arc
        # literal ("Invalid boolean literal"); True is the constant-true arc.
        arcs.append((1, 0, True))            # E->S closes every loop

        for o in oids:
            combos_m = [c for c in combo_by_op[o] if c["machine"] == m]
            for c in combos_m:
                model.AddImplication(c["lit"], on[o])
            model.AddBoolOr([*[c["lit"] for c in combos_m], on[o].Not()])
            model.AddImplication(on[o], idle.Not())

            sv = combos_m[0]["start"]
            # DEVIATION from brief listing: on[o] cannot gate both S->o and
            # o->E — with >=2 assigned ops every S->x arc would share a true
            # literal and the circuit over-subscribes Start (INFEASIBLE).
            # Dedicated literals are the real incoming/outgoing arcs; the
            # circuit ties them to on[o] via the self-loop.
            first[o] = model.NewBoolVar(f"fst_{m}_{o}")
            last[o] = model.NewBoolVar(f"lst_{m}_{o}")
            arcs.append((node[o], node[o], on[o].Not()))
            arcs.append((0, node[o], first[o]))
            d_init = d_of(init_fam.get(m), f[o])
            if d_init > 0:
                # Anchor: the initial setup cannot precede time zero (nothing
                # else on the machine forces this; non-first positions are
                # covered by predecessor overlap).
                model.Add(sv >= d_init).OnlyEnforceIf(first[o])
                iv = model.NewOptionalIntervalVar(
                    sv - d_init, d_init, sv, first[o], f"su_{m}_init_{o}")
                machine_iv.setdefault(m, []).append(iv)
                setup_choices.setdefault(o, []).append((first[o], d_init))
            arcs.append((node[o], 1, last[o]))

        for a in oids:
            for b in oids:
                if a == b:
                    continue
                arc = model.NewBoolVar(f"arc_{m}_{a}_{b}")
                arcs.append((node[a], node[b], arc))
                arc_lit[(a, b)] = arc
                # DEVIATION from brief listing: circuit succession must
                # constrain time, else the solver runs ops opposite to their
                # arc order and parks the setup interval before t=0.
                model.Add(
                    combo_by_op[b][0]["start"]
                    >= combo_by_op[a][0]["end"]).OnlyEnforceIf(arc)
                d = d_of(f[a], f[b])
                if d > 0:
                    sb = combo_by_op[b][0]["start"]
                    iv = model.NewOptionalIntervalVar(
                        sb - d, d, sb, arc, f"su_{m}_{a}_{b}")
                    machine_iv.setdefault(m, []).append(iv)
                    setup_choices.setdefault(b, []).append((arc, d))

        model.AddCircuit(arcs)
        circuit_info[m] = {"oids": oids, "on": on, "idle": idle,
                           "first": first, "last": last, "arcs": arc_lit}

    return setup_choices, circuit_info, lookup


def _build_model(payload: dict, *, include_setups: bool) -> dict:
    """Single construction site for both phases (hardening E): variable
    creation ORDER is identical for both include_setups values up to the
    setups block — include_setups=False skips exactly the one _add_setups
    call site, so phase A's model is a construction-prefix of phase B's
    (hint mapping rides captured VALUES; ordering is belt-and-braces).
    Returns the context solve() needs for hinting/extraction."""
    pending, frozen_echo = _split_ops(payload)

    horizon = compute_horizon(
        jobs=payload["jobs"],
        machine_downtime=payload["machine_downtime"],
        setup_times=payload["setup_times"],
        frozen_max_end=max((o["frozen"]["end"] for _, o in frozen_echo),
                           default=0),
    )
    span = _schedule_span(
        payload, frozen_echo,
        receipt_times=[r["available_at"]
                       for r in payload.get("material_receipts", [])],
        machine_downtime=payload["machine_downtime"])

    model = CpModel()
    machine_iv: dict[str, list] = {}
    worker_iv: dict[str, list] = {}
    demands_by_sku: dict[str, list] = {}

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
            if not combos:
                raise ValueError(
                    f"operation {oid_} has no eligible combinations")
            model.AddExactlyOne([c["lit"] for c in combos])
            combo_by_op[oid_] = combos

            # consumption gated on an any-combo boolean (circuit-presence
            # implication pattern); events in payload iteration order (§6.11)
            mats = o.get("materials") or []
            if mats:
                b_any = model.NewBoolVar(f"bany_{oid_}")
                for c in combos:
                    model.AddImplication(c["lit"], b_any)
                model.AddBoolOr([c["lit"] for c in combos] + [b_any.Not()])
                for mat in mats:
                    demands_by_sku.setdefault(mat["sku"], []).append(
                        (s, -int(mat["quantity"]), b_any))

    # downtime + unavailability as fixed blocks (malformed windows fail
    # loudly: max(1, ...) silently mis-modeled `until <= from`)
    for wdw in payload["machine_downtime"]:
        until = wdw["until"] if wdw["until"] is not None else horizon
        size = _require_valid_window(
            wdw["from"], until, f"downtime/{wdw['machine_id']}")
        iv = model.NewIntervalVar(wdw["from"], size, until,
                                  f"dt_{wdw['machine_id']}_{wdw['from']}")
        machine_iv.setdefault(wdw["machine_id"], []).append(iv)
    for i, uw in enumerate(payload["worker_unavailability"]):
        size = _require_valid_window(
            uw["from"], uw["until"], f"unavailable/{uw['worker_id']}")
        iv = model.NewIntervalVar(uw["from"], size, uw["until"],
                                  f"uw_{uw['worker_id']}_{i}")
        worker_iv.setdefault(uw["worker_id"], []).append(iv)

    fam_of = {ob["operation_id"]: jb.get("family_id") for jb, ob in pending}
    setup_choices: dict[str, list[tuple[object, int]]] = {}
    circuit_info: dict[str, dict] = {}
    setup_rows: dict[tuple[str, str | None, str], int] = {}
    if include_setups:
        setup_choices, circuit_info, setup_rows = _add_setups(
            model, payload=payload, pending=pending,
            combo_by_op=combo_by_op,
            machine_iv=machine_iv, fam_of=fam_of)

    for ivs in machine_iv.values():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)
    for ivs in worker_iv.values():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # ---- temporal material capacity: one reservoir per demanded sku (§6.11)
    refills_by_sku: dict[str, list] = {}
    for r in payload.get("material_receipts", []):
        refills_by_sku.setdefault(r["sku"], []).append(
            (int(r["available_at"]), int(r["quantity"]), True))
    cap_by_sku = {m["sku"]: int(m.get("capacity", 0))
                  for m in payload.get("materials", [])}
    for sku, events in demands_by_sku.items():
        events += refills_by_sku.get(sku, [])
        model.AddReservoirConstraintWithActive(
            [t for t, _, _ in events],
            [d for _, d, _ in events],
            [a for _, _, a in events],
            -cap_by_sku.get(sku, 0),
            sum(abs(d) for _, d, _ in events))

    # ---- tardiness, makespan, objective ----
    tardy_vars = []
    for job in payload["jobs"]:
        end_expr = last_end_expr.get(job["job_id"])
        if job["deadline"] is None or end_expr is None:
            continue
        t = model.NewIntVar(0, span, f"t_{job['job_id']}")
        model.AddMaxEquality(t, [0, end_expr - job["deadline"]])
        tardy_vars.append((job["job_id"],
                           _effective_beta(payload, job["job_id"]), t))

    all_ends = [prev_end[j] for j in ops_in_job if j in prev_end]
    makespan_var = model.NewIntVar(0, span, "makespan")
    model.AddMaxEquality(makespan_var, all_ends or [0])

    # DEVIATION (task-14): ortools forbids division on linear expressions,
    # so the model minimizes the unscaled weighted sum; normalization (§9)
    # divides by horizon > 0, a positive scale that preserves the argmin,
    # while finish() reports the normalized ratio. Both phases minimize the
    # same expression so phase A's argmin seeds phase B's search.
    obj_expr = (float(payload["config"]["alpha"]) * makespan_var
                + sum(w * t for _, w, t in tardy_vars))
    model.Minimize(obj_expr)

    return {"model": model, "horizon": horizon, "span": span,
            "combo_by_op": combo_by_op, "machine_iv": machine_iv,
            "worker_iv": worker_iv, "setup_choices": setup_choices,
            "circuit_info": circuit_info, "setup_rows": setup_rows,
            "tardy_vars": tardy_vars, "makespan_var": makespan_var,
            "prev_end": prev_end, "pending": pending,
            "frozen_echo": frozen_echo}


def _hint_from_solution(model, payload, ctx_a: dict, ctx_b: dict,
                        solver_a) -> None:
    """Relax-then-repair warm start (hardening E): phase A chooses each
    pending op's (machine, worker) combo on the setup-free relaxation; the
    timing is then re-derived by the deterministic setup-aware list
    dispatch (_greedy_plan restricted to A's choices), so the replayed hint
    is not only COMPLETE over every decision variable family — chosen combo
    literals, s/e ints, §6.6 circuit literals, makespan and ALL tardy vars —
    but also FEASIBLE for the full model. That pairing is the validated
    behavior (cpsat-primer parameters chapter; or-stackexchange 10406:
    complete+feasible hints are tagged [hint] immediately, partial ones get
    repaired expensively). DEVIATION from the raw design sketch (documented
    in the task report): hinting A's raw s/e values directly made the hint
    mass-violate setup intervals — measured on factory_demo_01 baseline,
    phase B burned its whole budget in hint repair and returned UNKNOWN
    where the feasible-hint variant commits at once. Deliberately NOT
    pinned via fix_variables_to_their_hinted_value."""
    pending = ctx_b["pending"]
    filtered: dict[str, list[dict]] = {}
    for _, op in ctx_a["pending"]:
        oid_ = op["operation_id"]
        ca = next(c for c in ctx_a["combo_by_op"][oid_]
                  if solver_a.BooleanValue(c["lit"]))
        cb = next(c for c in ctx_b["combo_by_op"][oid_]
                  if (c["machine"], c["worker"])
                  == (ca["machine"], ca["worker"]))
        filtered[oid_] = [cb]

    fam_of = {ob["operation_id"]: jb.get("family_id") for jb, ob in pending}
    placements, mach_order = _greedy_plan(
        payload, pending, filtered, ctx_b["frozen_echo"],
        fam_of=fam_of, setup_lookup=ctx_b["setup_rows"])
    _emit_hint(model, placements, mach_order, ctx_b["combo_by_op"],
               span=ctx_b["span"], circuit_info=ctx_b["circuit_info"])

    # makespan + tardy hints consistent with the re-timed placements
    span = ctx_b["span"]
    ends: dict[str, int] = {}
    for h in placements:
        ends[h["job_id"]] = max(ends.get(h["job_id"], 0), h["end"])
    frozen_end = max((o["frozen"]["end"] for _, o in ctx_b["frozen_echo"]),
                     default=0)
    deadline_of = {j["job_id"]: j["deadline"] for j in payload["jobs"]}
    tardy_b = {jid: tv for jid, _, tv in ctx_b["tardy_vars"]}
    for jid, tv in tardy_b.items():
        model.AddHint(tv, min(max(0, ends.get(jid, 0) - deadline_of[jid]),
                              span))
    model.AddHint(ctx_b["makespan_var"],
                  min(max([*(ends.values() or [0]), frozen_end]), span))


def solve(payload: dict) -> dict:
    t0 = time.monotonic()
    cfg = payload["config"]
    _validate_config(cfg)
    alpha = float(cfg["alpha"])
    normalize = bool(cfg.get("normalize_objectives", True))
    time_limit = float(cfg.get("time_limit_seconds", 60))
    seed = int(cfg.get("random_seed", 42))
    workers = int(cfg.get("num_search_workers", 1))

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

    pending, frozen_echo = _split_ops(payload)

    # ---- short circuit: nothing pending ----
    if not pending:
        ends_by_job = {j["job_id"]: o["frozen"]["end"] for j, o in frozen_echo}
        mk = max(ends_by_job.values(), default=0)
        return finish("OPTIMAL",
                      [echo_assignment(j, o) for j, o in frozen_echo],
                      mk, terms_for(ends_by_job), max(mk, 1))

    # ---- phase A: relaxation without the AddCircuit setup layer ----
    ctx_a = _build_model(payload, include_setups=False)
    solver_a = CpSolver()
    solver_a.parameters.max_time_in_seconds = min(
        10.0, max(2.0, 0.2 * time_limit))
    solver_a.parameters.num_search_workers = workers
    solver_a.parameters.random_seed = seed
    ta0 = time.monotonic()
    status_a = solver_a.Solve(ctx_a["model"])
    a_elapsed = round(time.monotonic() - ta0, 6)

    # ---- phase B: full model, warm-started from A or greedy fallback ----
    ctx_b = _build_model(payload, include_setups=True)
    model = ctx_b["model"]
    if status_a in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        _hint_from_solution(model, payload, ctx_a, ctx_b, solver_a)
    else:
        fam_of = {ob["operation_id"]: jb.get("family_id")
                  for jb, ob in pending}
        _greedy_hint(model, payload, pending, ctx_b["combo_by_op"],
                     span=ctx_b["span"],
                     circuit_info=ctx_b["circuit_info"], fam_of=fam_of,
                     setup_lookup=ctx_b["setup_rows"],
                     frozen_echo=frozen_echo)

    solver = CpSolver()
    solver.parameters.max_time_in_seconds = max(5.0, time_limit - a_elapsed)
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    status_code = solver.Solve(model)
    duration = round(time.monotonic() - t0, 6)

    # Status honesty (amendment 2026-08-24 third): UNKNOWN passes through as
    # "UNKNOWN"; INFEASIBLE strictly means proven impossibility.
    if status_code == cp_model.OPTIMAL:
        label = "OPTIMAL"
    elif status_code == cp_model.FEASIBLE:
        label = "FEASIBLE"
    elif status_code == cp_model.INFEASIBLE:
        label = "INFEASIBLE"
    else:
        label = "UNKNOWN"
    if label in ("INFEASIBLE", "UNKNOWN"):
        # CONTRACT deviation from listing: frozen echoes are retained on
        # INFEASIBLE/UNKNOWN; only the live set is emptied (Interfaces §13
        # bullet).
        assignments = [echo_assignment(j, o) for j, o in frozen_echo]
        mk = max((a["end"] for a in assignments), default=0)
        return finish(label, assignments, mk,
                      terms_for({j["job_id"]: o["frozen"]["end"]
                                 for j, o in frozen_echo}),
                      ctx_b["horizon"], dur=duration)

    assignments = [echo_assignment(j, o) for j, o in frozen_echo]
    ends_solved: dict[str, int] = {}
    for j, o in pending:
        oid_ = o["operation_id"]
        chosen = next(c for c in ctx_b["combo_by_op"][oid_]
                      if solver.BooleanValue(c["lit"]))
        st = int(solver.Value(chosen["start"]))
        en = int(solver.Value(chosen["end"]))
        setup_used = sum(
            d for lit, d in ctx_b["setup_choices"].get(oid_, [])
            if solver.BooleanValue(lit))
        assignments.append({
            "operation_id": oid_,
            "job_id": j["job_id"],
            "machine_id": chosen["machine"],
            "worker_id": chosen["worker"],
            "start": st,
            "end": en,
            "processing_time": en - st,
            "setup_time": setup_used,
            "is_frozen": False,
        })
        ends_solved[j["job_id"]] = max(ends_solved.get(j["job_id"], 0), en)

    merged = {j["job_id"]: o["frozen"]["end"] for j, o in frozen_echo}
    merged.update(ends_solved)
    return finish(label, assignments, max(a["end"] for a in assignments),
                  terms_for(merged), ctx_b["horizon"], dur=duration)
