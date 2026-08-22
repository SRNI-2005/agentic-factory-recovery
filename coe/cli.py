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


if __name__ == "__main__":
    main()
