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


ORG = {"period": "2026-10-30", "organization": "Тест", "departments": [{
    "id": "u-root", "name": "Компания", "order": 1,
    "positions": [{"id": "p-dir", "name": "Директор /Основное/", "rates": 1, "occupied": 1}],
    "departments": [
        {"id": "u-sales", "name": "Департамент продаж", "order": 2, "positions": [], "departments": [
            {"id": "u-s1", "name": "Отдел продаж №1", "order": 1, "departments": [],
             "positions": [{"id": "p-mpp1", "name": "Менеджер проектных продаж /Отдел продаж/", "rates": 3,
                            "occupied": 1}]},
            {"id": "u-s2", "name": "Отдел продаж №2", "order": 2, "departments": [],
             "positions": [{"id": "p-mpp2", "name": "Менеджер проектных продаж /Отдел продаж/", "rates": 2,
                            "occupied": 2}]}]},
        {"id": "u-rnd", "name": "Отдел инженерных разработок", "order": 3, "departments": [],
         "positions": [{"id": "p-eng", "name": "Ведущий инженер-электронщик /Отдел инженерных разработок/",
                        "rates": 1, "occupied": 0}]},
        {"id": "u-hr", "name": "Отдел управления персоналом", "order": 4, "departments": [],
         "positions": [{"id": "p-hr", "name": "Менеджер по персоналу /Отдел персонала/", "rates": 2,
                        "occupied": 1}]}]}]}


def org(conn, now) -> dict:
    """Вымышленная оргструктура: портреты продаж и инженера заведены заранее, чтобы связались по названию.
    Возвращает {external_id подразделения: org_unit.id} и профили."""
    from hr import org_1c
    sales = profile(conn, "sales_project", "Менеджер проектных продаж", dept="sales")
    eng = profile(conn)
    org_1c.apply(conn, json.dumps(ORG, ensure_ascii=False).encode(), "org.json", None, now)
    units = {r["external_id"]: r["id"] for r in db.rows(conn, "SELECT id, external_id FROM org_unit")}
    return {"units": units, "sales": sales, "eng": eng,
            "pos": {r["external_id"]: r["id"] for r in db.rows(conn, "SELECT id, external_id FROM staff_position")}}


def user(conn, login: str, role: str, unit_id: int | None = None) -> int:
    return db.val(conn, """INSERT INTO app_user (login, full_name, role, org_unit_id) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (login) DO UPDATE SET role = EXCLUDED.role, org_unit_id = EXCLUDED.org_unit_id
                           RETURNING id""", (login, f"Проверочный {login}", role, unit_id))


def own_unit(conn, user_id: int, role_code: str, name: str = "Проверочный отдел") -> int:
    """Подразделение руководителя с одной должностью, связанной с портретом role_code."""
    uid = db.val(conn, "INSERT INTO org_unit (external_id, title) VALUES (%s, %s) RETURNING id",
                 (f"u-{user_id}-{role_code}", name))
    key = f"title:{role_code}"
    db.run(conn, """INSERT INTO job_role_map (job_external_id, job_title, role_code) VALUES (%s, %s, %s)
                    ON CONFLICT (job_external_id) DO NOTHING""", (key, role_code, role_code))
    db.run(conn, """INSERT INTO staff_position (external_id, org_unit_id, job_external_id, job_title, rates_total)
                    VALUES (%s, %s, %s, %s, 1)""", (f"p-{user_id}-{role_code}", uid, key, role_code))
    db.run(conn, "UPDATE app_user SET org_unit_id = %s WHERE id = %s", (uid, user_id))
    return uid
