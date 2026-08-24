import argparse
import subprocess
from pathlib import Path


def _weight_args(p):
    p.add_argument("--alpha", type=float, default=None)
    p.add_argument("--beta", type=float, default=None)
    p.add_argument("--time-limit", type=int, default=None,
                   dest="time_limit")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--no-normalize", action="store_true",
                   dest="no_normalize")


def _weight_overrides(args) -> dict:
    from coe.config import get_settings

    s = get_settings()
    return {
        "alpha": args.alpha if args.alpha is not None
        else s.solver_alpha_weight,
        "beta": args.beta if args.beta is not None else s.solver_beta_weight,
        "time_limit_seconds": args.time_limit if args.time_limit is not None
        else s.solver_time_limit_seconds,
        "random_seed": args.seed if args.seed is not None
        else s.solver_random_seed,
        "num_search_workers": args.workers if args.workers is not None
        else s.solver_num_search_workers,
        "normalize_objectives": False if args.no_normalize
        else s.solver_normalize_objectives,
    }


def _instance_or_die(session, name):
    from coe.db.models.provenance import Instance

    inst = (session.query(Instance)
            .filter(Instance.name == name).one_or_none())
    if inst is None:
        raise SystemExit(f"unknown instance '{name}'")
    return inst


def _cli_message_id(instance_name: str, machine_name: str) -> str:
    import hashlib

    digest = hashlib.sha256(
        f"{instance_name}|{machine_name}".encode()).hexdigest()
    return f"cli-{digest[:8]}"


def _solve_common(session, inst, payload, *, now=None,
                  failed=()):
    from coe.solver.committer import commit_solution
    from coe.solver.engine import solve
    from coe.solver.invariants import check_solution

    solution = solve(payload)
    if solution["status"] not in ("OPTIMAL", "FEASIBLE"):
        raise SystemExit(
            f"solver returned {solution['status']} — nothing committed "
            "(UNKNOWN: increase --time-limit; INFEASIBLE: constraint/"
            "material conflict)")
    problems = check_solution(payload, solution)
    if problems:
        raise SystemExit("INVARIANT VIOLATIONS:\n"
                         + "\n".join(problems))
    version = commit_solution(session, instance_row=inst,
                              payload=payload, solution=solution,
                              failed_machine_names=failed, now=now)
    print(f"solved {inst.name}: version={version.version_number} "
          f"status={version.solver_status} makespan={version.makespan} "
          f"tardiness={version.total_tardiness} "
          f"duration={version.solve_duration_seconds}s")


def _recovery_floor(seconds: float) -> float:
    """Option A: recovery quality floor — spec §10"""
    return max(seconds, 180)


def _run_solve(args) -> None:
    from coe.db.session import session_scope

    from coe.solver.payload_builder import (
        build_payload,
        resolve_reference_clock,
    )

    w = _weight_overrides(args)
    if args.solve_cmd == "baseline":
        with session_scope() as session:
            inst = _instance_or_die(session, args.instance)
            payload = build_payload(session, instance_row=inst, **w)
            _solve_common(session, inst, payload)
        return

    # recovery
    w["time_limit_seconds"] = _recovery_floor(w["time_limit_seconds"])
    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        now = resolve_reference_clock(session, inst.id, args.at)
    from coe.mqtt.ingest import ingest_telemetry_event

    for m in args.failed_machine:
        created = ingest_telemetry_event({
            "message_id": _cli_message_id(args.instance, m),
            "instance_id": args.instance,
            "resource_kind": "MACHINE",
            "machine_id": m,
            "event_type": "FAILURE",
            "occurred_at": now,
            "severity": "HIGH",
            "reason": "cli-recovery-injection"})
        print(f"injected FAILURE {m} at t={now} "
              f"({'new' if created[1] else 'duplicate-suppressed'})")
    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        payload = build_payload(session, instance_row=inst, **w,
                                schedule_type="RECOVERY", now=now,
                                failed_machine_names=tuple(
                                    args.failed_machine))
        _solve_common(session, inst, payload, now=now,
                      failed=tuple(args.failed_machine))


def _run_restore(args) -> None:
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.session import session_scope

    from coe.solver.payload_builder import resolve_reference_clock

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        mrow = (session.query(Machine)
                .filter(Machine.instance_id == inst.id,
                        Machine.name == args.machine).one_or_none())
        if mrow is None:
            raise SystemExit(f"unknown machine '{args.machine}'")
        now = resolve_reference_clock(session, inst.id, args.at)
        opens = (session.query(MachineDowntimeWindow)
                 .filter(MachineDowntimeWindow.instance_id == inst.id,
                         MachineDowntimeWindow.machine_id == mrow.id,
                         MachineDowntimeWindow.downtime_until.is_(None))
                 .all())
        if not opens:
            raise SystemExit(f"no open outage window for {args.machine}")
        for w in opens:
            w.downtime_until = max(w.downtime_from + 1, now)
        mrow.status = "ACTIVE"
        print(f"restored {args.machine} at t={now}")


def _run_show(args) -> None:
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    from coe.solver.identifier import op_id
    from coe.solver.payload_builder import _load_active_snapshot

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        ver, entries = _load_active_snapshot(session, inst.id)
        if ver is None:
            raise SystemExit("no active schedule")
        print(f"version={ver.version_number} type={ver.schedule_type} "
              f"status={ver.solver_status} makespan={ver.makespan} "
              f"tardiness={ver.total_tardiness}")
        mnames = dict(session.query(Machine.id, Machine.name)
                      .filter(Machine.instance_id == inst.id).all())
        wnames = dict(session.query(Worker.id, Worker.name)
                      .filter(Worker.instance_id == inst.id).all())
        ops = (session.query(Operation, Job.name)
               .join(Job, Job.id == Operation.job_id)
               .filter(Operation.instance_id == inst.id).all())
        opnames = {o.id: op_id(jname, o.sequence_number) for o, jname in ops}
        for e in sorted(entries.values(),
                        key=lambda x: (mnames[x.machine_id],
                                       x.start_time)):
            print(f"  {mnames[e.machine_id]:<6} "
                  f"W={wnames.get(e.worker_id, '-'):<6} "
                  f"{opnames[e.operation_id]:<10} "
                  f"[{e.start_time},{e.end_time}) "
                  f"proc={e.processing_time} setup={e.setup_time} "
                  f"{'FROZEN' if e.is_frozen else e.status}")


def _run_rollback(args) -> None:
    from coe.db.session import session_scope

    from coe.solver.committer import RollbackFloor, rollback_active

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        try:
            rolled, active = rollback_active(session, inst)
        except RollbackFloor as exc:
            raise SystemExit(str(exc))
        print(f"rolled back {rolled} -> active {active}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coe", description="COE factory recovery system")
    sub = parser.add_subparsers(dest="group", required=True)

    imp = sub.add_parser("import", help="import a raw source dataset")
    sources = imp.add_subparsers(dest="source", required=True)
    mk01 = sources.add_parser("mk01")
    mk01.add_argument("--path", default="data/raw/mk01/mk01.txt")

    hutter = sources.add_parser("hutter")
    hutter.add_argument(
        "--path", default=None, help="single instance file, e.g. .../SFJW/SFJW-01.txt"
    )
    hutter.add_argument(
        "--dir",
        default=None,
        help="import every *.txt under this dir as its own instance",
    )

    gass = sources.add_parser("gass")
    gass.add_argument("--dir", default="data/raw/gass")

    sc = sub.add_parser("scenario")
    sc_sub = sc.add_subparsers(dest="scenario_cmd", required=True)
    sb = sc_sub.add_parser("build")
    sb.add_argument("--name", default="factory_demo_01")
    sb.add_argument("--seed", type=int, default=None)

    db = sub.add_parser("db")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("reset")   # destructive dev-only
    db_sub.add_parser("migrate")

    sv = sub.add_parser("solve")
    sv_sub = sv.add_subparsers(dest="solve_cmd", required=True)
    sb = sv_sub.add_parser("baseline")
    sb.add_argument("--instance", required=True)
    _weight_args(sb)
    sr = sv_sub.add_parser("recovery")
    sr.add_argument("--instance", required=True)
    sr.add_argument("--failed-machine", nargs="+", required=True,
                    dest="failed_machine")
    sr.add_argument("--at", type=int, default=None)
    _weight_args(sr)

    mc = sub.add_parser("machine")
    mc_sub = mc.add_subparsers(dest="machine_cmd", required=True)
    mr = mc_sub.add_parser("restore")
    mr.add_argument("--instance", required=True)
    mr.add_argument("--machine", required=True)
    mr.add_argument("--at", type=int, default=None)

    sch = sub.add_parser("schedule")
    sch_sub = sch.add_subparsers(dest="schedule_cmd", required=True)
    shw = sch_sub.add_parser("show")
    shw.add_argument("--instance", required=True)
    rback = sch_sub.add_parser("rollback")
    rback.add_argument("--instance", required=True)

    mq = sub.add_parser("mqtt")
    mq_sub = mq.add_subparsers(dest="mqtt_cmd", required=True)
    tf = mq_sub.add_parser("test-failure")
    tf.add_argument("--instance", default="factory_demo_01")
    tf.add_argument("--machine", default="M3")
    tf.add_argument("--at", type=int, default=512)

    ta = mq_sub.add_parser("test-absence")
    ta.add_argument("--instance", default="factory_demo_01")
    ta.add_argument("--worker", default="W3")
    ta.add_argument("--at", type=int, default=480)
    ta.add_argument("--duration", type=int, default=None)

    ts = mq_sub.add_parser("test-shortage")
    ts.add_argument("--instance", default="factory_demo_01")
    ts.add_argument("--sku", default="MAT-001")
    ts.add_argument("--at", type=int, default=300)

    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if args.group == "import":
        if args.source == "hutter":
            from coe.parsers.nouri import import_nouri

            if args.dir:
                from coe.parsers.common import SourceParseError

                for f in sorted(Path(args.dir).glob("*.txt")):
                    try:
                        print(f"{f} -> instance id={import_nouri(f)}")
                    except SourceParseError as exc:
                        print(f"{f} -> SKIPPED: {exc}")
            elif args.path:
                print(f"instance id={import_nouri(Path(args.path))}")
            else:
                raise SystemExit("hutter requires --path or --dir")

        if args.source == "mk01":
            from coe.parsers.mk01 import import_mk01

            instance_id = import_mk01(Path(args.path))
            print(f"instance id={instance_id}")

        if args.source == "gass":
            from coe.parsers.gass import import_gass

            print(f"instance id={import_gass(Path(args.dir))}")

    elif args.group == "scenario":
        if args.scenario_cmd == "build":
            from coe.config import get_settings
            from coe.scenario.build import build_scenario

            seed = args.seed if args.seed is not None else get_settings().default_seed
            print(f"scenario id={build_scenario(args.name, seed)}")

    elif args.group == "db":
        if args.db_cmd == "reset":
            from coe.db.admin import reset_database

            reset_database()
            print("database reset")

        if args.db_cmd == "migrate":
            subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
            print("migrations applied")

    elif args.group == "solve":
        _run_solve(args)

    elif args.group == "machine":
        if args.machine_cmd == "restore":
            _run_restore(args)

    elif args.group == "schedule":
        if args.schedule_cmd == "show":
            _run_show(args)
        if args.schedule_cmd == "rollback":
            _run_rollback(args)

    elif args.group == "mqtt":
        if args.mqtt_cmd == "test-failure":
            import time

            from coe.db.session import make_engine
            from coe.mqtt.edge_stub import publish_failure
            from coe.mqtt.subscriber import run_subscriber
            from sqlalchemy import text

            handle = run_subscriber()
            mid = publish_failure(args.instance, args.machine, occurred_at=args.at)
            deadline = time.time() + 5
            engine = make_engine()
            found = False
            while time.time() < deadline and not found:
                with engine.begin() as c:
                    n = c.execute(
                        text(
                            "SELECT count(*) FROM telemetry_events te "
                            "JOIN instances i ON i.id = te.instance_id "
                            "WHERE i.name = :inst AND te.message_id = :mid"
                        ),
                        {"inst": args.instance, "mid": mid},
                    ).scalar_one()
                found = n == 1
                if not found:
                    time.sleep(0.25)
            handle.stop()
            if found:
                print(f"OK: telemetry id stored once for message {mid}")
                raise SystemExit(0)
            raise SystemExit("FAIL: event did not reach telemetry_events within 5s")

        if args.mqtt_cmd == "test-absence":
            import time

            from coe.db.session import make_engine
            from coe.mqtt.edge_stub import publish_resource_event
            from coe.mqtt.subscriber import run_subscriber
            from sqlalchemy import text

            handle = run_subscriber()
            try:
                mid = publish_resource_event(
                    instance_name=args.instance, resource_kind="WORKER",
                    resource_id=args.worker, event_type="WORKER_ABSENT",
                    occurred_at=args.at, severity="MEDIUM",
                    reason="cli_proof", duration=args.duration,
                )
                deadline = time.time() + 5
                engine = make_engine()
                found = False
                while time.time() < deadline and not found:
                    with engine.begin() as c:
                        n = c.execute(text(
                            "SELECT count(*) FROM telemetry_events te "
                            "JOIN instances i ON i.id = te.instance_id "
                            "WHERE i.name = :inst AND te.message_id = :mid"),
                            {"inst": args.instance, "mid": mid}).scalar_one()
                        win = c.execute(text(
                            "SELECT count(*) FROM worker_absence_windows w "
                            "JOIN instances i ON i.id = w.instance_id "
                            "JOIN workers wk ON wk.instance_id = i.id "
                            "AND wk.name = :w WHERE i.name = :inst "
                            "AND w.absence_from <= :at AND "
                            "(w.absence_until IS NULL OR w.absence_until > :at)"),
                            {"inst": args.instance, "w": args.worker,
                             "at": args.at}).scalar_one()
                    found = n == 1 and win >= 1
                    if not found:
                        time.sleep(0.25)
                if found:
                    print(f"OK: WORKER_ABSENT stored once with absence window ({mid})")
                    raise SystemExit(0)
                raise SystemExit("FAIL: absence not fully ingested within 5s")
            finally:
                handle.stop()

        if args.mqtt_cmd == "test-shortage":
            import time

            from coe.db.session import make_engine
            from coe.mqtt.edge_stub import publish_resource_event
            from coe.mqtt.subscriber import run_subscriber
            from sqlalchemy import text

            handle = run_subscriber()
            try:
                mid = publish_resource_event(
                    instance_name=args.instance, resource_kind="MATERIAL",
                    resource_id=args.sku, event_type="MATERIAL_SHORTAGE",
                    occurred_at=args.at, severity="LOW", reason="cli_proof",
                )
                deadline = time.time() + 5
                engine = make_engine()
                found = False
                while time.time() < deadline and not found:
                    with engine.begin() as c:
                        n = c.execute(text(
                            "SELECT count(*) FROM telemetry_events te "
                            "JOIN instances i ON i.id = te.instance_id "
                            "WHERE i.name = :inst AND te.message_id = :mid "
                            "AND te.resource_kind = 'MATERIAL'"),
                            {"inst": args.instance, "mid": mid}).scalar_one()
                    found = n == 1
                    if not found:
                        time.sleep(0.25)
                if found:
                    print(f"OK: MATERIAL_SHORTAGE stored once ({mid})")
                    raise SystemExit(0)
                raise SystemExit("FAIL: shortage not ingested within 5s")
            finally:
                handle.stop()


if __name__ == "__main__":
    main()
