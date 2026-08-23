"""Conservative horizon (spec §6.7, as amended 2026-08-23).

H = max( Σ op-max-processing + Σ per-machine max-setup + Σ temporary downtime,
         max frozen end, max release time, 1 )

All inputs are payload-shaped plain dicts; permanent downtimes (`until is None`)
are excluded because their machine never reaches the engine.
"""


def _op_max_duration(op: dict) -> int:
    durs: list[int] = []
    for alt in op["alternatives"]:
        durs.append(alt["processing_time"])
        durs.extend(alt.get("workers", {}).values())
    return max(durs) if durs else 0


def compute_horizon(*, jobs, machine_downtime, setup_times, frozen_max_end: int = 0) -> int:
    processing = sum(
        _op_max_duration(op)
        for job in jobs
        for op in job["operations"]
        if op["status"] == "PENDING"
    )
    max_setup_per_machine: dict[str, int] = {}
    for row in setup_times:
        mid = row["machine_id"]
        max_setup_per_machine[mid] = max(max_setup_per_machine.get(mid, 0), row["duration"])
    setups = sum(max_setup_per_machine.values())
    downtime = sum(
        w["until"] - w["from"] for w in machine_downtime if w["until"] is not None
    )
    releases = [j["release_time"] for j in jobs]
    return max(processing + setups + downtime, frozen_max_end,
               max(releases) if releases else 0, 1)
