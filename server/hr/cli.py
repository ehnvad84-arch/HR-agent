"""Команды для инженера: python -m hr.cli <команда>."""
from __future__ import annotations

import argparse
import sys

from . import crypto, db, hh
from .clock import SYSTEM
from .config import get_config
from .http import client
from .jobs import JobError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr.cli", description="Агент подбора: служебные команды")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="накатить схему и миграции")
    sub.add_parser("gen-key", help="сгенерировать SECRETS_KEY для .env")
    sub.add_parser("hh-auth", help="одноразовая ссылка авторизации hh (действует 10 минут)")
    sub.add_parser("hh-selftest", help="проверить доступ к базе резюме hh")
    sub.add_parser("reembed", help="пересчитать векторы поиска по смыслу текущим способом")
    from .tasks import TASKS
    run_p = sub.add_parser("run", help="запустить фоновую задачу вручную")
    run_p.add_argument("task", choices=sorted(TASKS))
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
        try:
            res = hh.selftest(db.tx, client(get_config().hh_user_agent), SYSTEM.now())
        except JobError as e:
            print(f"hh-selftest: {e}")
            return 1
        print("hh отвечает, токен действует.")
        if not res["search_access"]:
            print("Поиск по базе резюме через API: ЗАКРЫТ. Нужна услуга доступа к базе резюме с разделом "
                  "«API и автоматизация процессов» (https://hh.ru/article/27029) — вопрос к менеджеру hh.")
        else:
            print("Поиск по базе резюме через API: открыт.")
        for s in res["services"] or [{"type": None}]:
            if s["type"] is None:
                print("Платных услуг API нет.")
            else:
                bal = f", остаток {s['balance']}" if s["balance"] is not None else ""
                print(f"Услуга API: {s['name']} ({s['type']}{bal}, до {s['expires_at']}).")
        api_left = res["views_left"] if res["views_left"] is not None else "не предусмотрено"
        print(f"Просмотров резюме на сегодня осталось: через API {api_left}, на сайте {res['views_left_site']}.")
        if res["found"] is not None:
            print(f"По запросу «инженер» в Краснодаре видно резюме: {res['found']}.")
        return 0 if res["search_access"] else 1
    elif args.cmd == "run":
        from . import tasks
        res = tasks.run(args.task)
        print(f"{args.task}: {'готово' if res.ok else 'ошибка'}" + (f" — {res.error}" if res.error else ""))
        print(f"  просмотрено {res.stats.seen}, новых {res.stats.new}, изменилось {res.stats.changed}, "
              f"карточек {res.stats.persons}, сигналов {res.stats.alerts}, страниц {res.stats.pages}"
              + (f"; {res.stats.note}" if res.stats.note else ""))
        return 0 if res.ok else 1
    elif args.cmd == "reembed":
        from . import search
        print(f"пересчитано векторов: {search.reembed(db.tx).changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
