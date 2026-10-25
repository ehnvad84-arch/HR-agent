"""Тестовые данные: подразделения, профили, резюме. Люди и компании вымышлены."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from hr import db

FIX = Path(__file__).parent / "fixtures"

ENGINEER_WEIGHTS = {"exp_years": 20, "keywords": 25, "title_match": 15, "donor_company": 10, "tenure": 10,
                    "geo": 10, "salary": 10}


def department(conn, code: str = "rnd_hw", title: str = "Инженерные разработки") -> int:
    return db.val(conn, """INSERT INTO department (code, title) VALUES (%s, %s)
                           ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id""", (code, title))


def profile(conn, code: str = "engineer_hw_lead", title: str = "Ведущий инженер-электронщик", *,
            version: str = "1.0", approved: bool = True, dept: str = "rnd_hw",
            approved_at: datetime | None = None, **kw) -> dict:
    dept_id = department(conn, dept, {"rnd_hw": "Инженерные разработки", "sales": "Продажи",
                                      "qc": "ОТК", "rnd_sw": "Разработка ПО"}.get(dept, dept))
    fields = dict(geo="krasnodar", salary_min=150000, salary_max=200000,
                  keywords=["Altium", "схемотехника", "трассировка", "ЭМС"], donor_companies=["Болид"],
                  weights=ENGINEER_WEIGHTS, must_have=[], nice_to_have=[], stop_factors=[])
    fields.update(kw)
    return db.one(conn, """
        INSERT INTO role_profile (department_id, code, title, version, geo, salary_min, salary_max, must_have,
            nice_to_have, stop_factors, weights, keywords, donor_companies, approved_by, approved_at)
        VALUES (%(d)s, %(code)s, %(title)s, %(version)s, %(geo)s, %(salary_min)s, %(salary_max)s, %(must_have)s,
            %(nice_to_have)s, %(stop_factors)s, %(weights)s, %(keywords)s, %(donor_companies)s, %(by)s, %(at)s)
        RETURNING *""", {**fields, "d": dept_id, "code": code, "title": title, "version": version,
                          "must_have": db.j(fields["must_have"]), "nice_to_have": db.j(fields["nice_to_have"]),
                          "stop_factors": db.j(fields["stop_factors"]), "weights": db.j(fields["weights"]),
                          "by": 1 if approved else None,
                          "at": (approved_at or datetime.now(UTC)) if approved else None})


def hh_item(**changes) -> dict:
    it = json.loads((FIX / "hh" / "search_item.json").read_text(encoding="utf-8"))
    it = copy.deepcopy(it)
    it["skill_set"] = ["Altium Designer", "схемотехника", "трассировка", "ЭМС"]
    it.update(changes)
    return it


def weak_item(**changes) -> dict:
    """Резюме, которое не проходит порог инженерного профиля."""
    return hh_item(id="weak-1", title="Продавец-консультант", skill_set=["касса", "выкладка товара"],
                   experience=[{"company": "Магазин у дома", "position": "Продавец", "start": "2025-01-01",
                                "end": None}], salary={"amount": 400000}, area={"id": "1", "name": "Москва"},
                   total_experience={"months": 10}, **changes)


def source(conn, code: str = "hh") -> dict:
    return db.one(conn, "SELECT * FROM source WHERE code = %s", (code,))
