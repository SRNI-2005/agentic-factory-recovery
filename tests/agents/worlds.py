"""Shared tiny factory worlds for pipeline integration tests (§11).

Each builder takes ``clean_db`` so the database is freshly migrated before
seeding. Bodies are verbatim from the Task 14 brief's ``g_world`` fixture
so graph-level and e2e tests exercise identical data.
"""


def build_g_world(clean_db):
    """Two machines, two single-op jobs, active baseline v1."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    with session_scope() as session:
        inst = Instance(name="g-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m1 = Machine(instance_id=iid, name="M1")
        m2 = Machine(instance_id=iid, name="M2")
        session.add_all([m1, m2])
        session.flush()
        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0,
                 deadline=100)
        jb = Job(instance_id=iid, name="J-B", priority=2, release_time=0,
                 deadline=100)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob])
        session.flush()
        for o in (oa, ob):
            for m, t in ((m1, 5), (m2, 6)):
                session.add(OperationMachineAlternative(
                    instance_id=iid, operation_id=o.id, machine_id=m.id,
                    processing_time=t))

        jobs = [{"job_id": j, "family_id": None, "release_time": 0,
                 "deadline": 100, "priority": p,
                 "operations": [{"operation_id": f"{j}-O1", "sequence": 1,
                                 "status": "PENDING", "materials": [],
                                 "alternatives": [
                                     {"machine_id": "M1",
                                      "processing_time": t1, "workers": {}},
                                     {"machine_id": "M2",
                                      "processing_time": t2, "workers": {}}],
                                 "frozen": None}]}
                for j, p, t1, t2 in (("J-A", 1, 5, 6), ("J-B", 2, 5, 6))]
        payload = {
            "instance_id": "g-world", "schedule_type": "BASELINE",
            "parent_version_id": None,
            "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                       "normalize_objectives": True, "random_seed": 42,
                       "num_search_workers": 1},
            "machines": ["M1", "M2"], "failed_machines": [],
            "machine_initial_families": {}, "warnings": [], "jobs": jobs,
            "machine_downtime": [], "materials": [],
            "material_receipts": [], "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": []}
        solution = {"status": "OPTIMAL", "objective_value": 1.0,
                    "makespan": 10, "total_tardiness": 0,
                    "assignments": [
                        {"operation_id": "J-A-O1", "job_id": "J-A",
                         "machine_id": "M1", "worker_id": None, "start": 0,
                         "end": 5, "processing_time": 5, "setup_time": 0,
                         "is_frozen": False},
                        {"operation_id": "J-B-O1", "job_id": "J-B",
                         "machine_id": "M1", "worker_id": None, "start": 5,
                         "end": 10, "processing_time": 5, "setup_time": 0,
                         "is_frozen": False}],
                    "solve_duration_seconds": 0.01}
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)
