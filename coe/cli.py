import argparse
from pathlib import Path


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

    mq = sub.add_parser("mqtt")
    mq_sub = mq.add_subparsers(dest="mqtt_cmd", required=True)
    tf = mq_sub.add_parser("test-failure")
    tf.add_argument("--instance", default="factory_demo_01")
    tf.add_argument("--machine", default="M3")
    tf.add_argument("--at", type=int, default=512)

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


if __name__ == "__main__":
    main()
