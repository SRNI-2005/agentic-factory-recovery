"""Tier 1: pure MK01 must solve to the published optimum (makespan = 40)."""
import pytest

pytestmark = [pytest.mark.db, pytest.mark.benchmark]


@pytest.fixture(scope="module")
def solved(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.solver.engine import solve
    from coe.solver.payload_builder import build_payload

    with session_scope() as session:
        inst = session.query(Instance).filter(Instance.name == "mk01").one()
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=120,
                                num_search_workers=1)
        return payload, solve(payload)


def test_mk01_optimal_makespan_40(solved):
    _, sol = solved
    assert sol["status"] == "OPTIMAL"
    assert sol["makespan"] == 40


def test_mk01_schedule_valid_and_overlap_free(solved):
    from coe.solver.invariants import check_solution

    payload, sol = solved
    assert check_solution(payload, sol) == []

    occupancy: dict[str, list[tuple[int, int]]] = {}
    for a in sol["assignments"]:
        start_busy = a["start"] - a.get("setup_time", 0)
        occupancy.setdefault(a["machine_id"], []).append(
            (start_busy, a["end"]))
    for m, ivs in occupancy.items():
        ivs.sort()
        for (_, e_prev), (s_next, _) in zip(ivs, ivs[1:]):
            assert s_next >= e_prev, f"overlap on {m}: {ivs}"
