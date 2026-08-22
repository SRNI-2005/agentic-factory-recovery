import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coe", description="COE factory recovery system")
    sub = parser.add_subparsers(dest="group", required=True)

    imp = sub.add_parser("import", help="import a raw source dataset")
    sources = imp.add_subparsers(dest="source", required=True)
    mk01 = sources.add_parser("mk01")
    mk01.add_argument("--path", default="data/raw/mk01/mk01.txt")

    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if args.group == "import":
        if args.source == "mk01":
            from coe.parsers.mk01 import import_mk01

            instance_id = import_mk01(Path(args.path))
            print(f"instance id={instance_id}")


if __name__ == "__main__":
    main()
