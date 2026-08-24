"""Pure post-solve invariant checks (spec §6.2-adjacent gate list enforced
at CLI commit (coe/cli.py::_solve_common); shared by Phase 3 gate/verifier).

Shared verbatim by the Phase 2 committer and (later) Phase 3's pre-commit
gate + post-commit verifier, so the definition can never drift apart.
"""
from collections import defaultdict


def check_solution(payload: dict, solution: dict) -> list[str]:
    violations: list[str] = []
    machines = set(payload["machines"])

    frozen_expected: dict[str, dict] = {}
    blocked: set[str] = set()
    alts: dict[str, list] = {}
    seqs_by_job: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for job in payload["jobs"]:
        for op in job["operations"]:
            oid_ = op["operation_id"]
            if op.get("frozen") is not None:
                frozen_expected[oid_] = op["frozen"]
            if op["status"] == "BLOCKED":
                blocked.add(oid_)
            alts[oid_] = op["alternatives"]
            seqs_by_job[job["job_id"]].append((op["sequence"], oid_))

    seen: dict[str, dict] = {}
    for a in solution["assignments"]:
        oid_ = a["operation_id"]
        if oid_ in seen:
            violations.append(f"duplicate assignment for {oid_}")
            continue
        seen[oid_] = a
        # Sixth gate (hardening 2026-08-24): duration arithmetic holds for
        # every assignment; frozen echoes included (they carry setup 0).
        # DEVIATION from brief listing: spec §5 pins setup_time as "setup
        # duration before this operation", so [start, end) covers exactly
        # the processing time — the setup lives in [start - setup, start)
        # and must not enter the sum (it would reject every real schedule).
        if a["end"] != a["start"] + a["processing_time"]:
            violations.append(f"duration arithmetic violated on {oid_}")
        if oid_ in blocked:
            violations.append(f"blocked operation {oid_} appears in schedule")
        exp = frozen_expected.get(oid_)
        if exp is not None:
            # Frozen echo: historic fact. Checked for drift only — exempt
            # from machine-membership/eligibility/duration (rider 2026-08-24).
            if (a["machine_id"] != exp["machine_id"]
                    or a.get("worker_id") != exp.get("worker_id")
                    or a["start"] != exp["start"]
                    or a["end"] != exp["end"]):
                violations.append(f"frozen drift on {oid_}")
            continue
        if a["machine_id"] not in machines:
            violations.append(
                f"{oid_} assigned to unavailable machine {a['machine_id']}")
            continue
        elig = [x for x in alts.get(oid_, [])
                if x["machine_id"] == a["machine_id"]]
        if not elig:
            violations.append(
                f"{oid_} ineligible on {a['machine_id']}")
            continue
        alt = elig[0]
        ws = alt.get("workers") or {}
        wid = a.get("worker_id")
        if ws:
            if wid not in ws:
                violations.append(f"{oid_} worker {wid} ineligible")
            elif a["processing_time"] != ws[wid]:
                violations.append(
                    f"{oid_} duration {a['processing_time']} != worker "
                    f"{wid} duration {ws[wid]}")
        elif wid is not None or a["processing_time"] != alt["processing_time"]:
            violations.append(
                f"{oid_} duration/worker mismatch vs machine-level alt")

    for oid_ in frozen_expected:
        if oid_ not in seen:
            violations.append(f"frozen operation {oid_} missing from solution")

    for seqs in seqs_by_job.values():
        prev_end = None
        for _, oid_ in sorted(seqs):
            a = seen.get(oid_)
            if a is None:
                continue
            if prev_end is not None and a["start"] < prev_end:
                violations.append(f"precedence violated before {oid_}")
            prev_end = a["end"]

    return violations
