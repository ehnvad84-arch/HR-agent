"""Команды для инженера: python -m hr.cli <команда>."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import auth, crypto, db, hh, release, version
from .clock import SYSTEM
from .config import get_config
from .http import client
from .jobs import JobError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr.cli", description="Агент подбора: служебные команды")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mig = sub.add_parser("migrate", help="накатить схему и миграции")
    mig.add_argument("--pending", action="store_true", help="только показать неприменённые (выход 3 — есть)")
    sub.add_parser("version", help="версия кода и базы, миграции (выход 3 — есть неприменённые)")
    sub.add_parser("jobs-running", help="идущие фоновые задачи (выход 4 — идут; зависшие — предупреждение)")
    rr = sub.add_parser("release-record", help="запись в журнал версий ops.release")
    rr.add_argument("--kind", required=True, choices=release.KINDS)
    rr.add_argument("--to", required=True, dest="to_version")
    rr.add_argument("--from", dest="from_version")
    rr.add_argument("--commit")
    rr.add_argument("--migrations", default="", help="имена через запятую")
    rr.add_argument("--actor", default=None, help="кто обновлял; по умолчанию — пользователь системы")
    rr.add_argument("--failed", action="store_true", help="неудача (по умолчанию — удачно)")
    rr.add_argument("--note")
    rl = sub.add_parser("release-last", help="«откуда куда» последней удачной записи журнала (выход 5 — записей нет)")
    rl.add_argument("--kind", default="update", choices=release.KINDS)
    em = sub.add_parser("env-missing", help="переменные из .env.example, которых нет или пусто в .env (только имена)")
    em.add_argument("--env", type=Path)
    em.add_argument("--example", type=Path)
    jr = sub.add_parser("job-record", help="итог задачи вне портала (копия базы) в истории задач")
    jr.add_argument("job")
    jr.add_argument("--failed", action="store_true")
    jr.add_argument("--error")
    jr.add_argument("--stats", type=json.loads, default=None, help="JSON с цифрами итога")
    sub.add_parser("db-size", help="размер базы в байтах (место под копию)")
    sub.add_parser("gen-key", help="сгенерировать SECRETS_KEY для .env")
    sub.add_parser("hh-auth", help="одноразовая ссылка авторизации hh (действует 10 минут)")
    sub.add_parser("hh-selftest", help="проверить доступ к базе резюме hh")
    uc = sub.add_parser("user-create", help="временная учётная запись рабочего места; пароль спросит без эха")
    uc.add_argument("login")
    uc.add_argument("--name", required=True, help="ФИО для журнала действий")
    uc.add_argument("--role", default="head_recruiter",
                    choices=["recruiter", "head_recruiter", "hiring_manager", "director"])
    sub.add_parser("reembed", help="пересчитать векторы поиска по смыслу текущим способом")
    from .tasks import TASKS
    run_p = sub.add_parser("run", help="запустить фоновую задачу вручную")
    run_p.add_argument("task", choices=sorted(TASKS))
    args = parser.parse_args(argv)

    if args.cmd == "migrate" and args.pending:
        with db.tx() as conn:
            todo = db.pending(conn)
        print("не применены: " + ", ".join(todo) if todo else "неприменённых нет")
        return 3 if todo else 0
    elif args.cmd == "migrate":
        applied = db.migrate()
        print("применено: " + (", ".join(applied) if applied else "ничего, база актуальна"))
        with db.tx() as conn:
            for msg in release.notices(conn, applied):
                print(msg)
    elif args.cmd == "version":
        with db.tx() as conn:
            st = version.status(conn)
        print(f"версия кода: {st['code']} от {version.release_date(st['code']) or 'неизвестно'}")
        print(f"по журналу версий: {st['db'] or 'записей нет'}")
        print(f"миграций применено: {st['applied']}")
        print("не применены: " + ", ".join(st["pending"]) if st["pending"] else "неприменённых нет")
        if st["ahead"]:
            print("база новее кода (так бывает после отката): " + ", ".join(st["ahead"]))
        return 3 if st["pending"] else 0
    elif args.cmd == "jobs-running":
        with db.tx() as conn:
            going, stuck = release.jobs_running(conn)
        for r in stuck:
            print(f"предупреждение: {r['job']} не завершена с {r['started_at']:%d.%m %H:%M} — похоже, зависла",
                  file=sys.stderr)
        for r in going:
            print(f"идёт: {r['job']} с {r['started_at']:%d.%m %H:%M}")
        if not going:
            print("фоновых задач не идёт")
        return 4 if going else 0
    elif args.cmd == "release-record":
        migs = [m.strip() for m in args.migrations.split(",") if m.strip()]
        with db.tx() as conn:
            rid = release.record(conn, args.kind, args.to_version, from_version=args.from_version,
                                 git_commit=args.commit, migrations=migs, actor=args.actor or getpass.getuser(),
                                 ok=not args.failed, note=args.note)
        print(f"журнал версий: запись {rid}")
    elif args.cmd == "release-last":
        with db.tx() as conn:
            r = release.last(conn, args.kind)
        if not r:
            return 5
        print(f"{r['from_version'] or '-'} {r['to_version']}")
    elif args.cmd == "env-missing":
        names = release.env_missing(args.example, args.env)
        print("не заданы в .env: " + ", ".join(names) if names else "все переменные из .env.example заданы")
    elif args.cmd == "job-record":
        with db.tx() as conn:
            release.job_record(conn, args.job, not args.failed, args.error, args.stats)
    elif args.cmd == "db-size":
        with db.tx() as conn:
            print(db.val(conn, "SELECT pg_database_size(current_database())"))
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
    elif args.cmd == "user-create":
        pw = getpass.getpass("Пароль (не короче 8 знаков): ")
        if pw != getpass.getpass("Ещё раз: "):
            print("Пароли не совпали, учётная запись не создана")
            return 1
        try:
            with db.tx() as conn:
                uid = auth.create_user(conn, args.login, args.name, args.role, pw)
        except auth.PasswordError as e:
            print(f"Учётная запись не создана: {e}")
            return 1
        print(f"Учётная запись {args.login} ({args.role}) готова, id {uid}")
    elif args.cmd == "reembed":
        from . import search
        print(f"пересчитано векторов: {search.reembed(db.tx).changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
