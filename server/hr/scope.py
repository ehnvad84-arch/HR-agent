"""Граница видимости по оргструктуре (manager-cabinet, S5, S17, S18): одна точка на сервере.

Отдел персонала (право base.all) видит всё. Руководитель — своё подразделение и всё вложенное: вакансии
поддерева, людей с баллом по должностям поддерева, свои передачи. Директор — корень, то есть вся компания,
но без прав отдела персонала. Скоуп считается на каждый запрос: смена подразделения действует сразу (S18).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from .db import one, rows, val


@dataclass(frozen=True)
class Scope:
    all: bool = False
    units: frozenset[int] = field(default_factory=frozenset)
    codes: frozenset[str] = field(default_factory=frozenset)      # role_profile.code должностей поддерева
    vacancies: frozenset[int] = field(default_factory=frozenset)
    user_id: int | None = None
    reason: str | None = None                                      # no_unit — подразделение не указано (S17)

    def public(self) -> dict:
        return {"all": self.all, "units": sorted(self.units), "reason": self.reason}

    def code_ok(self, code: str | None) -> bool:
        return self.all or (code is not None and code in self.codes)

    def vacancy_ok(self, vacancy_id: int) -> bool:
        return self.all or vacancy_id in self.vacancies


ALL = Scope(all=True)

_SUBTREE = """
    WITH RECURSIVE t AS (SELECT id FROM org_unit WHERE id = ANY(%s) AND is_active
                         UNION SELECT u.id FROM org_unit u JOIN t ON u.parent_id = t.id WHERE u.is_active)
    SELECT id FROM t"""


def for_user(conn: psycopg.Connection, user) -> Scope:
    if "base.all" in user.permissions:
        return ALL
    if user.role == "director":
        roots = [r["id"] for r in rows(conn, "SELECT id FROM org_unit WHERE parent_id IS NULL AND is_active")]
    else:
        unit = val(conn, "SELECT org_unit_id FROM app_user WHERE id = %s", (user.id,))
        roots = [unit] if unit else []
    if not roots:
        return Scope(user_id=user.id, reason="no_unit")
    units = frozenset(r["id"] for r in rows(conn, _SUBTREE, (roots,)))
    codes = frozenset(r["role_code"] for r in rows(conn, """
        SELECT DISTINCT m.role_code FROM staff_position sp JOIN job_role_map m ON m.job_external_id = sp.job_external_id
        WHERE sp.is_active AND m.role_code IS NOT NULL AND sp.org_unit_id = ANY(%s)""", (list(units),)))
    vacancies = frozenset(r["id"] for r in rows(conn, """
        SELECT v.id FROM vacancy v LEFT JOIN staff_position sp ON sp.id = v.staff_position_id
        WHERE sp.org_unit_id = ANY(%(u)s) OR v.hiring_manager_id = %(me)s
           OR v.hiring_manager_id IN (SELECT id FROM app_user WHERE org_unit_id = ANY(%(u)s))""",
        {"u": list(units), "me": user.id}))
    return Scope(units=units, codes=codes, vacancies=vacancies, user_id=user.id)


def person_ok(conn: psycopg.Connection, scope: Scope, person_id: int) -> bool:
    """Человек виден: балл по действующей версии профиля из скоупа, воронка вакансии скоупа или своя передача."""
    if scope.all:
        return True
    return bool(one(conn, """
        SELECT 1 WHERE EXISTS (
            SELECT 1 FROM match m JOIN role_profile rp ON rp.id = m.role_profile_id
            WHERE m.person_id = %(p)s AND rp.code = ANY(%(codes)s) AND rp.approved_at IS NOT NULL AND rp.is_active)
         OR EXISTS (SELECT 1 FROM ops.candidate_vacancy cv JOIN candidate c ON c.id = cv.candidate_id
                    WHERE c.person_id = %(p)s AND cv.vacancy_id = ANY(%(vac)s))
         OR EXISTS (SELECT 1 FROM ops.handoff h WHERE h.person_id = %(p)s AND h.from_user = %(me)s)""",
        {"p": person_id, "codes": list(scope.codes), "vac": list(scope.vacancies), "me": scope.user_id}))
