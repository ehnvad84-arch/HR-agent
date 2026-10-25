"""Команды для инженера: python -m hr.cli <команда>."""
from __future__ import annotations

import argparse
import sys

from . import crypto, db, hh
from .clock import SYSTEM
from .config import get_config
from .http import client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr.cli", description="Агент подбора: служебные команды")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="накатить схему и миграции")
    sub.add_parser("gen-key", help="сгенерировать SECRETS_KEY для .env")
    sub.add_parser("hh-auth", help="одноразовая ссылка авторизации hh (действует 10 минут)")
    sub.add_parser("hh-selftest", help="проверить доступ к базе резюме hh")
    sub.add_parser("reembed", help="пересчитать векторы поиска по смыслу текущим способом")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        applied = db.migrate()
        print("применено: " + (", ".join(applied) if applied else "ничего, база актуальна"))
    elif args.cmd == "gen-key":
        print(crypto.new_key())
    elif args.cmd == "hh-auth":
        with db.tx() as conn:
            print("Откройте под менеджером работодателя:\n" + hh.auth_link(conn, SYSTEM.now()))
    elif args.cmd == "hh-selftest":
        with db.tx() as conn:
            res = hh.selftest(conn, client(get_config().hh_user_agent), SYSTEM.now())
        print(f"hh отвечает: по запросу «инженер» в Краснодаре видно резюме: {res['found']}")
    elif args.cmd == "reembed":
        from . import search
        print(f"пересчитано векторов: {search.reembed(db.tx).changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
