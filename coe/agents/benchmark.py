"""Fidelity benchmark (spec §8): seeded corpus + deterministic metrics."""
import json
import random
from pathlib import Path

_MACHINE_CASES = [
    ("MC-04 gearbox seized mid-shift, sparks everywhere",
     {"event_type": "FAILURE", "severity": "HIGH",
      "estimated_downtime": 90}),
    ("M2 spindle making loud knocking, we stopped it",
     {"event_type": "FAILURE", "severity": "MEDIUM",
      "estimated_downtime": 45}),
    ("scheduled maintenance on M3 next shift, about an hour",
     {"event_type": "MAINTENANCE", "severity": "LOW",
      "estimated_downtime": 60}),
    ("M1 hydraulics dead, mechanic says half a day",
     {"event_type": "FAILURE", "severity": "CRITICAL",
      "estimated_downtime": 240}),
]
_WORKER_CASES = [
    ("W-03 called in sick this morning",
     {"event_type": "WORKER_ABSENT", "severity": "MEDIUM",
      "estimated_absence": 240}),
    ("operator W1 out for the rest of the day",
     {"event_type": "WORKER_ABSENT", "severity": "HIGH",
      "estimated_absence": 480}),
    ("W2 at training until early afternoon",
     {"event_type": "WORKER_ABSENT", "severity": "LOW",
      "estimated_absence": 180}),
    ("W-04 no-show, probably overslept",
     {"event_type": "WORKER_ABSENT", "severity": "LOW", }),
]
_MATERIAL_CASES = [
    ("STEEL-304 bin is empty, delivery stuck at supplier",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "HIGH"}),
    ("MAT-001 stock ran dry overnight",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "MEDIUM"}),
    ("we are out of ALU-6061, resupply expected soon",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "MEDIUM"}),
    ("BRASS-260 exhausted, purchasing chasing truck",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "HIGH"}),
]


def generate_corpus(seed: int, out_dir: Path) -> Path:
    """Deterministic corpus: same seed => byte-identical files (§8)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed + 21)
    refs = {"MACHINE": ["MC-04", "M2", "M3", "M1"],
            "WORKER": ["W-03", "W1", "W2", "W-04"],
            "MATERIAL": ["STEEL-304", "MAT-001", "ALU-6061", "BRASS-260"]}
    banks = [("MACHINE", _MACHINE_CASES), ("WORKER", _WORKER_CASES),
             ("MATERIAL", _MATERIAL_CASES)]
    field_name = {"MACHINE": "machine_id", "WORKER": "worker_id",
                  "MATERIAL": "material_sku"}
    occurred_choices = [300, 480, 512]

    lines, counts = [], {"MACHINE": 0, "WORKER": 0, "MATERIAL": 0}
    n = 0
    for kind, cases in banks:
        for text, spec in cases:
            ref = refs[kind][counts[kind]]
            counts[kind] += 1
            truth = {
                "kind": kind,
                "instance_id": "",          # filled by the harness
                field_name[kind]: ref,
                "event_type": spec["event_type"],
                "occurred_at": occurred_choices[n % len(occurred_choices)],
                "severity": spec["severity"],
                "narrative_excerpt": text,
            }
            if kind == "MACHINE" and "estimated_downtime" in spec:
                truth["estimated_downtime"] = spec["estimated_downtime"]
            if kind == "WORKER" and "estimated_absence" in spec:
                truth["estimated_absence"] = spec["estimated_absence"]
            lines.append({
                "case_id": f"case-{n:02d}",
                "kind": kind,
                "narrative": text,
                "ground_truth": truth,
                "resources": {
                    "machines": refs["MACHINE"],
                    "workers": refs["WORKER"],
                    "skus": refs["MATERIAL"],
                    "stock": {sku: 40 for sku in refs["MATERIAL"]},
                },
            })
            n += 1

    # seeded shuffle keeps family mix while varying presentation order;
    # the rng stream feeds ONLY this shuffle (content stays seed-stable)
    rng.shuffle(lines)
    (out_dir / "corpus.jsonl").write_text(
        "".join(json.dumps(l, sort_keys=True) + "\n" for l in lines))
    (out_dir / "_meta.json").write_text(json.dumps({
        "seed": seed, "synthetic": True,
        "families": counts}, sort_keys=True))
    return out_dir


def materialize_case(case: dict, instance_name: str) -> None:
    """Create Instance + referenced Machine/Worker/Material rows (layer 3)."""
    from coe.db.models.fjsp import Machine
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    res = case["resources"]
    with session_scope() as session:
        inst = Instance(name=instance_name, source_name="synthetic-bench")
        session.add(inst)
        session.flush()
        for m in res["machines"]:
            session.add(Machine(instance_id=inst.id, name=m))
        for w in res["workers"]:
            session.add(Worker(instance_id=inst.id, name=w))
        for sku, stock in res["stock"].items():
            session.add(Material(instance_id=inst.id, sku=sku,
                                 initial_stock=stock))


_FIELD_BY_KIND = {"MACHINE": "machine_id", "WORKER": "worker_id",
                  "MATERIAL": "material_sku"}
_SCORED_FIELDS = ("event_type", "occurred_at", "severity")


def _field_matches(kind, got: dict, want: dict) -> tuple[float, int]:
    checks = [_SCORED_FIELDS, (_FIELD_BY_KIND[kind],)]
    total = hits = 0
    for group in checks:
        for f in group:
            total += 1
            if got.get(f) == want.get(f):
                hits += 1
    for dur in ("estimated_downtime", "estimated_absence"):
        if dur in want or dur in got:
            total += 1
            if got.get(dur) == want.get(dur):
                hits += 1
    return hits, total


def canonical_score(makespan: int, tardiness_by_job: dict) -> float:
    """Rescoring seam (§8): identical formula on both sides."""
    return (makespan + sum(tardiness_by_job.values())) / max(makespan, 1)


_OK_STATUSES = ("OPTIMAL", "FEASIBLE")


def _strategy_ask(client, case: dict) -> tuple[bool, list]:
    """Mid-pipeline candidate proposal; schema-shaped plans only."""
    ask = json.dumps({"instance": f"bench-{case['case_id']}",
                      "narrative": case["narrative"]}, sort_keys=True)
    try:
        proposal = json.loads(client.complete(system="strategy", user=ask))
        if not isinstance(proposal.get("candidates"), list) \
                or not isinstance(proposal.get("final"), bool):
            raise ValueError("missing candidates/final fields")
        return True, proposal["candidates"]
    except Exception:
        return False, []


def _strategy_compare(case: dict, candidates: list, strategy_solver) -> str:
    """Two tiny canonical_scored solves; returns the comparison verdict."""
    base = strategy_solver(case, [], phase="baseline")
    strat = strategy_solver(case, candidates, phase="strategy")
    if base.get("status") not in _OK_STATUSES:
        return "baseline_infeasible"
    if strat.get("status") not in _OK_STATUSES:
        return "degraded"       # failed strategy-side commit = degradation
    sb = canonical_score(base["makespan"],
                         base.get("tardiness_by_job") or {})
    ss = canonical_score(strat["makespan"],
                         strat.get("tardiness_by_job") or {})
    return "ok" if ss <= sb else "degraded"


def run_fidelity(corpus_dir: Path, *, client,
                 strategy_solver=None,
                 solve_budget_seconds: int = 30) -> dict:
    """§8 fidelity run. Translation + strategy + explain legs share ONE
    pass so the scripted-client response stream stays per-case interleaved
    (three asks per case, mirroring the three AI roles).

    solve_budget_seconds budgets the production mini-solve leg (wired when
    a strategy_solver is composed from the CP-SAT engine; until that
    integration lands, injected solvers drive the leg and None reports the
    neutral non-degradation default while validity stays measured).
    """
    from coe.agents.records import parse_disruption_record, \
        validate_record_fields
    from coe.config import get_settings

    s = get_settings()
    cases = [json.loads(x) for x in
             (Path(corpus_dir) / "corpus.jsonl").read_text().splitlines()]

    per_kind: dict[str, list] = {}
    case_rows = []
    valid = compared = nondegraded = baseline_infeasible = 0
    for case in cases:
        # --- translation leg -------------------------------------------
        inst = f"bench-{case['case_id']}"
        truth = dict(case["ground_truth"], instance_id=inst)
        raw = client.complete(system="translate", user=case["narrative"])
        try:
            data = json.loads(raw)
            # §4.1 layer 2: instance binding is harness-authoritative —
            # ground truth carries instance_id "" and the harness fills it,
            # exactly as the CLI value overrides any LLM-authored binding.
            data["instance_id"] = inst
            got = parse_disruption_record(data).model_dump()
            from coe.db.session import make_engine
            from sqlalchemy.orm import Session

            with Session(make_engine()) as session:
                validate_record_fields(got, session=session,
                                       instance_name=inst)
            passed = True
        except Exception:
            got, passed = {}, False
        hits, total = _field_matches(case["kind"], got, truth)
        row = {"case_id": case["case_id"], "kind": case["kind"],
               "field_hits": hits, "field_total": total,
               "corpus_pass": passed}
        per_kind.setdefault(case["kind"], []).append(row)
        case_rows.append(row)

        # --- strategy leg (same pass => interleaved client asks) -------
        proposal_ok, candidates = _strategy_ask(client, case)
        valid += proposal_ok
        if strategy_solver is not None:
            verdict = _strategy_compare(case, candidates, strategy_solver)
            if verdict == "baseline_infeasible":
                baseline_infeasible += 1
            else:
                compared += 1
                nondegraded += verdict == "ok"

        # --- explain ask (AI role 3; prose not scored in §8 v1) --------
        client.complete(system="explain",
                        user=json.dumps({"instance": inst,
                                         "narrative": case["narrative"]},
                                        sort_keys=True))

    aggregate_rows = [r for rows in per_kind.values() for r in rows]
    report = {
        "translation": {
            "per_kind": {
                k: {
                    "exact_match_rate":
                        sum(r["field_hits"] for r in v)
                        / max(sum(r["field_total"] for r in v), 1),
                    "corpus_pass_rate":
                        sum(r["corpus_pass"] for r in v) / len(v),
                } for k, v in sorted(per_kind.items())},
            "aggregate": {
                "exact_match_rate":
                    sum(r["field_hits"] for r in aggregate_rows)
                    / max(sum(r["field_total"] for r in aggregate_rows), 1),
                "corpus_pass_rate":
                    sum(r["corpus_pass"] for r in aggregate_rows)
                    / max(len(aggregate_rows), 1),
            },
        },
        "strategy": {
            "validity_rate": valid / max(len(cases), 1),
            "non_degradation_rate":
                nondegraded / max(compared, 1)
                if strategy_solver is not None else 1.0,
            "baseline_infeasible": baseline_infeasible,
        },
        "cases": sorted(case_rows, key=lambda r: r["case_id"]),
    }
    report["threshold_met"] = (report["translation"]["aggregate"]
                               ["corpus_pass_rate"]
                               >= s.benchmark_translation_accuracy)
    return report


def write_report(report: dict, out: Path) -> None:
    out.write_text(json.dumps(report, sort_keys=True, indent=2))
