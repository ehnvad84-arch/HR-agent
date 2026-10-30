"""Пробный сбор с настоящего hh по двум открытым вакансиям, до 50 человек на каждую. Запускает человек.

    cd server && set -a; source .env; set +a; python scripts/trial_hh.py [--yes]

Заводит и утверждает (с подтверждения запускающего) профили «Ведущий инженер-электронщик»
и «Менеджер проектных продаж», ставит пределы прогона, замеряет остаток пакета API hh до и после
и печатает итог. Контакты не открываются: это отдельная платная операция.
Имён и контактов не печатает: только ярлык карточки (должность, город) и балл.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hr import db, hh, tasks  # noqa: E402
from hr.clock import SYSTEM  # noqa: E402
from hr.config import get_config  # noqa: E402
from hr.http import client  # noqa: E402
from hr.jobs import JobError  # noqa: E402

PROFILES = [
    {"dept": "rnd_hw", "dept_title": "Инженерные разработки", "code": "engineer_hw_lead",
     "title": "Ведущий инженер-электронщик", "version": "0.1-проба",
     "keywords": ["Altium", "схемотехника", "трассировка", "ЭМС"], "donors": [],
     "salary_min": None, "salary_max": None},
    {"dept": "sales", "dept_title": "Продажи", "code": "sales_project",
     "title": "Менеджер проектных продаж", "version": "0.1-проба",
     "keywords": ["44-ФЗ", "CRM", "проектные продажи"], "donors": [],
     "salary_min": None, "salary_max": None},
]
# вилка пока не задана: признак «ожидания против вилки» у всех нейтральный, 0.5
# одна страница поиска (50 резюме) на вакансию; каждый запрос полного резюме — тоже страница бюджета
LIMITS = {"crawl_max_pages": "110", "crawl_per_profile_pages": "1", "hh_full_resume_per_run": "100"}
NOTE = "пробный сбор scripts/trial_hh.py, утвердил запускающий"


def balance(http) -> int | None:
    res = hh.selftest(db.tx, http, SYSTEM.now())
    if not res["search_access"]:
        raise JobError("поиск по базе резюме через API закрыт — сбор не запускаю")
    left = [s["balance"] for s in res["services"] if s["type"] == "API_LIMITED" and s["balance"] is not None]
    return left[0] if left else None


def prepare() -> None:
    with db.tx() as conn:
        for p in PROFILES:
            _profile(conn, p)
        _limits(conn)


def _profile(conn, p: dict) -> None:
    d = db.val(conn, "SELECT id FROM department WHERE code = %s", (p["dept"],)) or db.val(
        conn, "INSERT INTO department (code, title) VALUES (%s, %s) RETURNING id", (p["dept"], p["dept_title"]))
    if not db.val(conn, "SELECT id FROM role_profile WHERE code = %s AND version = %s", (p["code"], p["version"])):
        # approved_by = 0: учётных записей из 1С ещё нет; кто утвердил — в agent_change.note
        db.run(conn, """INSERT INTO role_profile (department_id, code, title, version, salary_min, salary_max,
                            keywords, donor_companies, approved_by, approved_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, now())""",
               (d, p["code"], p["title"], p["version"], p["salary_min"], p["salary_max"], p["keywords"],
                p["donors"]))
        db.run(conn, "INSERT INTO agent_change (kind, after, note) VALUES ('profile', %s, %s)",
               (db.j({k: p[k] for k in ("code", "title", "version", "keywords")}), NOTE))


def _limits(conn) -> None:
    before = {r["key"]: r["value"] for r in db.rows(conn, "SELECT key, value FROM settings WHERE key = ANY(%s)",
                                                    (list(LIMITS),))}
    if before != LIMITS:
        for k, v in LIMITS.items():
            db.run(conn, "UPDATE settings SET value = %s WHERE key = %s", (v, k))
        db.run(conn, "INSERT INTO agent_change (kind, before, after, note) VALUES ('source', %s, %s, %s)",
               (db.j(before), db.j(LIMITS), NOTE))


def main() -> int:
    for p in PROFILES:
        print(f"Профиль: «{p['title']}», Краснодар, ключевые слова: {', '.join(p['keywords'])}; вилка не задана.")
    print(f"Пределы: до 50 резюме на вакансию, не больше {LIMITS['hh_full_resume_per_run']} полных резюме "
          f"и {LIMITS['crawl_max_pages']} запросов к hh. Контакты не открываются.")
    if "--yes" in sys.argv[1:]:
        print("Профили утверждены ключом --yes (поручение человека).")
    elif input("Утвердить профили и запустить сбор? Напишите «да»: ").strip().lower() != "да":
        print("Отменено.")
        return 1
    calls: Counter = Counter()

    def count(req):
        path = req.url.path
        calls["поиск" if path == "/resumes" else "полное резюме" if path.startswith("/resumes/") else "служебный"] += 1

    http = client(get_config().hh_user_agent, event_hooks={"request": [count]})
    try:
        left_before = balance(http)
        prepare()
        calls.clear()
        results = [(name, tasks.run(name, **({"http": http} if name == "collect_hh" else {})))
                   for name in ("collect_hh", "score", "alerts")]
        run_calls = dict(calls)
        left_after = balance(http)
    except JobError as e:
        print(f"Остановлено: {e}")
        return 1
    for name, res in results:
        note = f"; {res.stats.note}" if res.stats.note else ""
        err = f" — {res.error}" if res.error else ""
        print(f"{name}: {'готово' if res.ok else 'ошибка'}{err}. Просмотрено {res.stats.seen}, новых {res.stats.new}, "
              f"карточек {res.stats.persons}, сигналов {res.stats.alerts}{note}")
    with db.tx() as conn:
        print("\nКарточки (лучший балл):")
        for r in db.rows(conn, """SELECT p.label, max(m.score) AS score FROM person p JOIN match m ON m.person_id = p.id
                                  GROUP BY p.id, p.label ORDER BY score DESC LIMIT 10"""):
            print(f"  {r['score']:>3}  {r['label']}")
        below = db.val(conn, "SELECT count(*) FROM observation WHERE person_id IS NULL")
        print(f"  ниже порога, без карточки: {below}")
        n = db.one(conn, """SELECT count(*) AS total, count(*) FILTER (WHERE raw ? 'photo' AND raw->'photo' <> 'null')
                                AS photo FROM observation o JOIN source s ON s.id = o.source_id WHERE s.code = 'hh'""")
        print(f"  всего резюме с hh в базе: {n['total']}, из них с фото: {n['photo']}")
        print("\nОбратите внимание:")
        for r in db.rows(conn, "SELECT reason, headline FROM alert WHERE state = 'new' ORDER BY id DESC LIMIT 5"):
            print(f"  [{r['reason']}] {r['headline']}")
        m = db.one(conn, "SELECT explanation FROM match WHERE explanation IS NOT NULL ORDER BY score DESC LIMIT 1")
        if m:
            print("\nРазбор лучшего балла:\n  " + m["explanation"].replace("\n", "\n  "))
    print(f"\nЗапросов сбора к hh: {', '.join(f'{k} {v}' for k, v in run_calls.items()) or 'нет'}.")
    if left_before is not None and left_after is not None:
        print(f"Пакет API: было {left_before}, стало {left_after}, списано {left_before - left_after}. "
              "Между замерами есть ещё 4 служебных запроса проверки, из них 1 поиск.")
    return 0 if all(res.ok for _, res in results) else 1


if __name__ == "__main__":
    sys.exit(main())
