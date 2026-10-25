"""Команды для инженера: python -m hr.cli <команда>."""
from __future__ import annotations

import argparse
import sys

from . import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr.cli", description="Агент подбора: служебные команды")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="накатить схему и миграции")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        applied = db.migrate()
        print("применено: " + (", ".join(applied) if applied else "ничего, база актуальна"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
