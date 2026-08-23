from coe.solver.horizon import compute_horizon


def _op(durs, workers=None, status="PENDING"):
    alts = [{"machine_id": m, "processing_time": d, "workers": (workers or {}).get(m, {})}
            for m, d in durs.items()]
    return {"status": status, "alternatives": alts}


def test_sums_max_processing_including_worker_durations():
    jobs = [{"release_time": 0, "operations": [
        _op({"M0": 10}, {"M0": {"W1": 15}}),   # worker slower than machine
        _op({"M1": 20}),
    ]}]
    h = compute_horizon(jobs=jobs, machine_downtime=[], setup_times=[])
    assert h == 35


def test_setup_and_downtime_added():
    jobs = [{"release_time": 0, "operations": [_op({"M0": 10})]}]
    h = compute_horizon(
        jobs=jobs,
        machine_downtime=[{"machine_id": "M0", "from": 100, "until": 160}],
        setup_times=[{"machine_id": "M0", "from_family": None, "to_family": "A", "duration": 7},
                     {"machine_id": "M0", "from_family": "A", "to_family": "B", "duration": 3}],
    )
    assert h == 10 + 7 + 60


def test_permanent_downtime_excluded():
    jobs = [{"release_time": 0, "operations": [_op({"M0": 10})]}]
    h = compute_horizon(jobs=jobs,
                        machine_downtime=[{"machine_id": "M0", "from": 0, "until": None}],
                        setup_times=[])
    assert h == 10


def test_frozen_end_and_release_dominate():
    jobs = [{"release_time": 500, "operations": []}]
    assert compute_horizon(jobs=jobs, machine_downtime=[], setup_times=[],
                           frozen_max_end=900) == 900


def test_empty_is_one():
    assert compute_horizon(jobs=[], machine_downtime=[], setup_times=[]) == 1


def test_completed_ops_excluded():
    jobs = [{"release_time": 0, "operations": [_op({"M0": 999}, status="COMPLETED")]}]
    assert compute_horizon(jobs=jobs, machine_downtime=[], setup_times=[]) == 1
