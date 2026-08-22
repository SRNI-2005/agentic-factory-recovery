import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import Job, JobFamily, Machine, SetupTime
from coe.db.models.provenance import InstanceProfile, ScenarioSource


def _fam_num(code: str) -> int:
    """Leading integer of a GASS process-code suffix ('P2b' -> 2). Real data
    contains lettered variants (P2b), so a bare int(suffix) would crash."""
    i = 1
    while i < len(code) and code[i].isdigit():
        i += 1
    return int(code[1:i])


def _gass_profiles(session: Session, gass_source_id: int) -> tuple[dict[str, int], list[str]]:
    prof = (
        session.query(InstanceProfile)
        .filter(
            InstanceProfile.source_instance_id == gass_source_id,
            InstanceProfile.name == "gass-machines",
        )
        .one()
    )
    # Natural numeric order (M1, M2, ... M15) — lexicographic sort would put
    # M10 right after M1 and silently mis-map demo machines to GASS classes.
    codes_in_order = sorted(
        ((m["code"], m["setup_time"]) for m in prof.parameters_json["machines"]),
        key=lambda pair: int(pair[0][1:]),
    )
    setup_base = {
        code: max(1, round(st / 30)) for code, st in codes_in_order
    }
    routing_prof = (
        session.query(InstanceProfile)
        .filter(
            InstanceProfile.source_instance_id == gass_source_id,
            InstanceProfile.name == "gass-routings",
        )
        .one()
    )
    process_codes = [p["code"] for p in routing_prof.parameters_json["processes"]]
    return setup_base, process_codes


def add_families_and_setups(
    session: Session,
    scenario_id: int,
    *,
    gass_source_id: int,
    seed: int,
) -> dict:
    rng = random.Random(seed)
    setup_base, process_codes = _gass_profiles(session, gass_source_id)

    families = [
        JobFamily(instance_id=scenario_id, source_id=code, name=f"FAM-{code}")
        for code in process_codes
    ]
    session.add_all(families)
    session.flush()

    jobs = session.scalars(
        select(Job).where(Job.instance_id == scenario_id).order_by(Job.id)
    ).all()
    for job in jobs:
        job.job_family_id = rng.choice(families).id

    machines = session.scalars(
        select(Machine).where(Machine.instance_id == scenario_id).order_by(Machine.id)
    ).all()
    # setup_base was built in natural numeric order (M1, M2, ... M15); list()
    # preserves it. A lexicographic sort here would mis-map demo machine i to
    # the wrong GASS class (M10 lands right after M1).
    gass_codes = list(setup_base)

    n_setup_rows = 0
    for mi, m in enumerate(machines):
        v = setup_base[gass_codes[mi % len(gass_codes)]]
        for fa in families:
            for fb in families:
                if fa.id == fb.id:
                    continue
                dur = v + ((_fam_num(fa.source_id) + _fam_num(fb.source_id)) % 3)
                session.add(
                    SetupTime(
                        instance_id=scenario_id,
                        machine_id=m.id,
                        from_family_id=fa.id,
                        to_family_id=fb.id,
                        setup_duration=dur,
                        source="gass-profile",
                    )
                )
                n_setup_rows += 1
            session.add(
                SetupTime(
                    instance_id=scenario_id,
                    machine_id=m.id,
                    from_family_id=None,
                    to_family_id=fa.id,
                    setup_duration=v + (_fam_num(fa.source_id) % 3),
                    source="gass-profile",
                )
            )
            n_setup_rows += 1

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=gass_source_id,
            contribution_type="setup_times",
            transformation_description=(
                "families from GASS process codes; sequence-dependent matrix "
                "normalized as gass SetupTime/30 per machine class"
            ),
            random_seed=seed,
        )
    )
    session.flush()
    return {"families": len(families), "setup_rows": n_setup_rows}
