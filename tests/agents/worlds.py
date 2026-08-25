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


def build_shortage_world(name: str, receipt_at: int | None) -> str:
    """One machine, two single-op jobs BOTH consuming MAT-X (stock 5,
    demand 10 -> shortfall); optional covering receipt of 10 at receipt_at.
    Active baseline v1 committed. Returns the instance name."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material, MaterialReceipt, OperationBom
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    with session_scope() as session:
        inst = Instance(name=name, source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m1 = Machine(instance_id=iid, name="M1")
        session.add(m1)
        session.flush()
        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0,
                 deadline=60)
        jb = Job(instance_id=iid, name="J-B", priority=3, release_time=0,
                 deadline=90)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob])
        session.flush()
        for o in (oa, ob):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=o.id, machine_id=m1.id,
                processing_time=5))
        mat = Material(instance_id=iid, sku="MAT-X", initial_stock=5,
                       reorder_point=None)
        session.add(mat)
        session.flush()
        for o in (oa, ob):
            session.add(OperationBom(instance_id=iid, operation_id=o.id,
                                     material_id=mat.id,
                                     quantity_required=5))
        if receipt_at is not None:
            session.add(MaterialReceipt(
                instance_id=iid, material_id=mat.id, quantity=10,
                available_at=receipt_at, source="synthetic"))

        jobs = [{"job_id": j, "family_id": None, "release_time": 0,
                 "deadline": dl, "priority": p,
                 "operations": [{"operation_id": f"{j}-O1", "sequence": 1,
                                 "status": "PENDING",
                                 "materials": [{"sku": "MAT-X",
                                                "quantity": 5}],
                                 "alternatives": [
                                     {"machine_id": "M1",
                                      "processing_time": 5, "workers": {}}],
                                 "frozen": None}]}
                for j, p, dl in (("J-A", 1, 60), ("J-B", 3, 90))]
        payload = {
            "instance_id": name, "schedule_type": "BASELINE",
            "parent_version_id": None,
            "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                       "normalize_objectives": True, "random_seed": 42,
                       "num_search_workers": 1},
            "machines": ["M1"], "failed_machines": [],
            "machine_initial_families": {}, "warnings": [], "jobs": jobs,
            "machine_downtime": [],
            "materials": [{"sku": "MAT-X", "capacity": 5}],
            "material_receipts": (
                [] if receipt_at is None
                else [{"sku": "MAT-X", "quantity": 10,
                       "available_at": receipt_at}]),
            "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": []}
        # Baseline work sits AFTER the acceptance runs' reference clock (5)
        # so both ops stay PENDING in the RECOVERY build and a SUSPEND_JOB
        # sacrifice passes the catalog's suspension_has_history validator.
        solution = {"status": "OPTIMAL", "objective_value": 1.0,
                    "makespan": 20, "total_tardiness": 0,
                    "assignments": [
                        {"operation_id": "J-A-O1", "job_id": "J-A",
                         "machine_id": "M1", "worker_id": None, "start": 10,
                         "end": 15, "processing_time": 5, "setup_time": 0,
                         "is_frozen": False},
                        {"operation_id": "J-B-O1", "job_id": "J-B",
                         "machine_id": "M1", "worker_id": None, "start": 15,
                         "end": 20, "processing_time": 5, "setup_time": 0,
                         "is_frozen": False}],
                    "solve_duration_seconds": 0.01}
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)
    return name
