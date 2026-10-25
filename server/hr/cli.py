"""Команды для инженера: python -m hr.cli <команда>."""
from __future__ import annotations

import argparse
import sys

from . import crypto, db, hh
from .clock import SYSTEM


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr.cli", description="Агент подбора: служебные команды")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="накатить схему и миграции")
    sub.add_parser("gen-key", help="сгенерировать SECRETS_KEY для .env")
    sub.add_parser("hh-auth", help="одноразовая ссылка авторизации hh (действует 10 минут)")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        applied = db.migrate()
        print("применено: " + (", ".join(applied) if applied else "ничего, база актуальна"))
    elif args.cmd == "gen-key":
        print(crypto.new_key())
    elif args.cmd == "hh-auth":
        with db.tx() as conn:
            print("Откройте под менеджером работодателя:\n" + hh.auth_link(conn, SYSTEM.now()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
