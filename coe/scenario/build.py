from sqlalchemy.orm import Session

from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.session import session_scope
from coe.scenario.add_failures import add_maintenance_windows
from coe.scenario.add_job_attributes import add_job_attributes
from coe.scenario.add_setup_times import add_families_and_setups
from coe.scenario.add_workers import add_workers
from coe.scenario.topology_sampler import sample_topology


class ScenarioError(RuntimeError):
    pass


def _require_source(session: Session, prefix: str, label: str) -> Instance:
    inst = (
        session.query(Instance)
        .filter(Instance.name.like(f"{prefix}%"))
        .order_by(Instance.id.asc())
        .first()
    )
    if inst is None:
        raise ScenarioError(
            f"{label} source instance not found; import it first "
            f"(expected an instance named '{prefix}*')"
        )
    return inst


def build_scenario(name: str = "factory_demo_01", seed: int = 42) -> int:
    """Composite build is atomic (spec §11): any failure rolls back everything."""
    with session_scope() as session:
        existing = (
            session.query(Instance).filter(Instance.name == name).one_or_none()
        )
        if existing is not None:
            raise ScenarioError(
                f"scenario '{name}' already exists; run 'db reset' to rebuild"
            )

        scenario = Instance(
            name=name,
            source_name="synthetic-composite",
            source_version="phase1",
            source_license="synthetic",
        )
        session.add(scenario)
        session.flush()

        mk01 = _require_source(session, "mk01", "MK01")
        nouri = _require_source(session, "nouri-", "Hutter/Nouri")
        gass = _require_source(session, "gass", "GASS")

        counts = sample_topology(
            session,
            scenario.id,
            source_instance_id=mk01.id,
            n_jobs=30,
            n_machines=8,
            seed=seed,
        )
        session.add(
            ScenarioSource(
                scenario_id=scenario.id,
                source_instance_id=mk01.id,
                contribution_type="topology",
                transformation_description=(
                    "sampled 30x8 topology from MK01-derived profiles "
                    f"(ops/job, flexibility histogram, duration pool): {counts}"
                ),
                random_seed=seed,
            )
        )

        # Later tasks insert their transformations here, in this order:
        add_job_attributes(session, scenario.id, source_instance_id=mk01.id, seed=seed + 1)
        add_workers(session, scenario.id, nouri_source_id=nouri.id, seed=seed + 2)
        add_families_and_setups(session, scenario.id, gass_source_id=gass.id, seed=seed + 3)
        add_maintenance_windows(session, scenario.id, seed=seed + 4)
        #   add_materials(...)           (Task 14)
        #   normalize_times(...)         (Task 14)

        session.flush()
        return scenario.id
