"""Сквозной прогон фичи core на вымышленных данных, без сети и ключей.

    DATABASE_URL=postgresql://…/ruware_hr_demo python scripts/demo_core.py

Пересоздаёт схемы в базе *_demo, заводит три утверждённых профиля, принимает
выгрузку scripts/demo/habr_demo.csv, считает оценки и сигналы и печатает итог.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hr import db, search, tasks  # noqa: E402

PROFILES = [
    ("rnd_hw", "Инженерные разработки", "engineer_hw_lead", "Ведущий инженер-электронщик",
     ["Altium", "схемотехника", "трассировка", "ЭМС"], ["Болид"], 150000, 200000),
    ("qc", "ОТК", "otk_engineer", "Инженер ОТК", ["AQL", "входной контроль", "8D"], [], 80000, 110000),
    ("sales", "Продажи", "sales_project", "Менеджер проектных продаж", ["44-ФЗ", "CRM", "проектные продажи"],
     ["Дистрибьютор СБ"], 120000, 160000),
]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not urlparse(dsn).path.endswith("_demo"):
        print("DATABASE_URL должен указывать на базу *_demo: скрипт пересоздаёт её схемы")
        return 2
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS ops CASCADE")
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")
    print("схема:", ", ".join(db.migrate(dsn)))
    with db.tx() as conn:
        for dept, dtitle, code, title, kws, donors, lo, hi in PROFILES:
            d = db.val(conn, "INSERT INTO department (code, title) VALUES (%s, %s) RETURNING id", (dept, dtitle))
            db.run(conn, """INSERT INTO role_profile (department_id, code, title, version, salary_min, salary_max,
                                keywords, donor_companies, approved_by, approved_at)
                            VALUES (%s, %s, %s, '1.0', %s, %s, %s, %s, 0, now())""",
                   (d, code, title, lo, hi, kws, donors))
    folder = Path(tempfile.mkdtemp(prefix="hr-demo-"))
    shutil.copy(Path(__file__).parent / "demo" / "habr_demo.csv", folder / "habr_demo.csv")
    os.environ["IMPORT_DIR"] = str(folder)
    from hr.config import get_config
    get_config.cache_clear()
    for name in ("import_files", "score", "alerts"):
        res = tasks.run(name)
        note = f"; {res.stats.note}" if res.stats.note else ""
        print(f"{name}: {'готово' if res.ok else 'ошибка'} — просмотрено {res.stats.seen}, новых {res.stats.new}, "
              f"карточек {res.stats.persons}, сигналов {res.stats.alerts}{note}")
    with db.tx() as conn:
        print("\nКарточки наблюдения (лучший балл):")
        for r in db.rows(conn, """
                SELECT p.label, max(m.score) AS score FROM person p JOIN match m ON m.person_id = p.id
                GROUP BY p.id, p.label ORDER BY score DESC"""):
            print(f"  {r['score']:>3}  {r['label']}")
        below = db.val(conn, "SELECT count(*) FROM observation WHERE person_id IS NULL")
        print(f"  ниже порога, без карточки: {below}")
        print("\nОбратите внимание:")
        for r in db.rows(conn, "SELECT reason, headline FROM alert WHERE state = 'new' ORDER BY id"):
            print(f"  [{r['reason']}] {r['headline']}")
        print("\nПоиск по смыслу «схемотехник Altium»:")
        for r in search.similar(conn, "схемотехник Altium", limit=3):
            print(f"  {r['similarity']:.2f}  {r['label']}")
        m = db.one(conn, "SELECT explanation FROM match ORDER BY score DESC LIMIT 1")
        print("\nРазбор лучшего балла:\n  " + m["explanation"].replace("\n", "\n  "))
    shutil.rmtree(folder, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
