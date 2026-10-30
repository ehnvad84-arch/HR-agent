"""Вакансии, которые заводит рекрутер (S21, S23, S38). Заявки из 1С — фича exchange-1c.

Закрытие — с комментарием не короче decision_comment_min (проверку дублирует СУБД, схема:527);
keep_sourcing не трогается: наблюдение по должности продолжается (пункт 24).
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from .db import j, one, rows, run, setting, val


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


def listing(conn: psycopg.Connection, only: frozenset[int] | None = None) -> dict:
    """only — вакансии скоупа руководителя (manager-cabinet); None — все."""
    items = rows(conn, """
        SELECT v.id, v.title, v.headcount, v.opened_at, v.target_date, v.state, v.origin::text AS origin,
               v.closed_at, v.close_comment, v.norm_days, v.role_profile_id, rp.code AS role_code,
               (current_date - v.opened_at) AS open_days, v.recruiter_id, v.hiring_manager_id,
               (SELECT full_name FROM app_user WHERE id = v.recruiter_id) AS recruiter,
               (SELECT full_name FROM app_user WHERE id = v.hiring_manager_id) AS hiring_manager,
               (SELECT count(*) FROM ops.candidate_vacancy cv JOIN candidate c ON c.id = cv.candidate_id
                WHERE cv.vacancy_id = v.id AND c.status NOT IN ('hired', 'rejected_by_us', 'rejected_by_candidate',
                                                                'refused_contact', 'no_show')) AS in_work
        FROM vacancy v JOIN role_profile rp ON rp.id = v.role_profile_id
        WHERE %s::bigint[] IS NULL OR v.id = ANY(%s)
        ORDER BY v.opened_at DESC, v.id DESC""", (list(only) if only is not None else None,) * 2)
    return {"open": [v for v in items if v["state"] in ("open", "on_hold")],
            "closed": [v for v in items if v["state"] in ("closed", "cancelled")]}


HR_ROLES = ("recruiter", "head_recruiter")
MANAGER_ROLES = ("hiring_manager", "director")


def staff(conn: psycopg.Connection, kind: str) -> list[dict]:
    """Списки формы вакансии: отдел персонала (ответственный) или руководители (заказчик)."""
    roles = list(HR_ROLES if kind == "hr" else MANAGER_ROLES)
    return rows(conn, """SELECT u.id, u.full_name, ou.title AS org_unit FROM app_user u
                         LEFT JOIN org_unit ou ON ou.id = u.org_unit_id
                         WHERE u.is_active AND u.role::text = ANY(%s) ORDER BY u.full_name""", (roles,))


def default_manager(conn: psycopg.Connection, role_profile_id: int) -> dict | None:
    """S12: руководитель подразделения должности — ближайший вверх по оргструктуре; должность в нескольких
    подразделениях — их общий начальник. Не нашли — None, заказчика выбирают вручную."""
    units = [r["org_unit_id"] for r in rows(conn, """
        SELECT DISTINCT sp.org_unit_id FROM staff_position sp
        JOIN job_role_map m ON m.job_external_id = sp.job_external_id
        JOIN role_profile rp ON rp.code = m.role_code
        WHERE rp.id = %s AND sp.is_active""", (role_profile_id,))]
    if not units:
        return None
    chains = [[r["id"] for r in rows(conn, """
        WITH RECURSIVE up AS (SELECT id, parent_id, 0 AS d FROM org_unit WHERE id = %s
                              UNION SELECT o.id, o.parent_id, up.d + 1 FROM org_unit o JOIN up ON o.id = up.parent_id)
        SELECT id FROM up ORDER BY d""", (u,))] for u in units]
    common = [u for u in chains[0] if all(u in c for c in chains[1:])]
    for unit in common:
        m = one(conn, """SELECT id, full_name FROM app_user WHERE org_unit_id = %s AND is_active
                         AND role::text = ANY(%s) ORDER BY role, id LIMIT 1""", (unit, list(MANAGER_ROLES)))
        if m:
            return {"hiring_manager_id": m["id"], "hiring_manager": m["full_name"]}
    return None


def _role_of(conn, user_id: int | None) -> str | None:
    return val(conn, "SELECT role::text FROM app_user WHERE id = %s AND is_active", (user_id,)) if user_id else None


def create(conn: psycopg.Connection, role_profile_id: int, title: str, user_id: int, headcount: int = 1,
           target_date=None, recruiter_id: int | None = None, hiring_manager_id: int | None = None) -> int:
    """S12: ответственный HR (по умолчанию — заводящий) и руководитель-заказчик (по умолчанию — руководитель
    подразделения должности) обязательны."""
    if not val(conn, "SELECT 1 FROM role_profile WHERE id = %s AND approved_at IS NOT NULL AND is_active",
               (role_profile_id,)):
        raise Invalid("профиль не утверждён")
    if not title.strip() or headcount < 1:
        raise Invalid("название и число ставок")
    recruiter_id = recruiter_id or user_id
    if _role_of(conn, recruiter_id) not in HR_ROLES:
        raise Invalid("ответственный — сотрудник отдела персонала")
    if hiring_manager_id is None:
        hiring_manager_id = (default_manager(conn, role_profile_id) or {}).get("hiring_manager_id")
    if _role_of(conn, hiring_manager_id) not in MANAGER_ROLES:
        raise Invalid("руководитель-заказчик: выберите руководителя подразделения")
    return val(conn, """INSERT INTO vacancy (role_profile_id, title, headcount, target_date, recruiter_id,
                                             hiring_manager_id, origin)
                        VALUES (%s, %s, %s, %s, %s, %s, 'recruiter') RETURNING id""",
               (role_profile_id, title.strip(), headcount, target_date, recruiter_id, hiring_manager_id))


def _vacancy(conn, vacancy_id: int) -> dict:
    v = one(conn, "SELECT id, state FROM vacancy WHERE id = %s FOR UPDATE", (vacancy_id,))
    if not v:
        raise NotFound
    return v


def close(conn: psycopg.Connection, vacancy_id: int, comment: str, user_id: int, now: datetime) -> str:
    minimum = int(setting(conn, "decision_comment_min", 200))
    if len(comment.strip()) < minimum:
        raise Invalid(f"комментарий не короче {minimum} знаков")
    if _vacancy(conn, vacancy_id)["state"] == "closed":
        raise Conflict("вакансия уже закрыта")
    run(conn, """UPDATE vacancy SET state = 'closed', close_comment = %s, closed_by = %s, closed_at = %s
                 WHERE id = %s""", (comment.strip(), user_id, now.date(), vacancy_id))
    return "closed"


def reopen(conn: psycopg.Connection, vacancy_id: int, user_id: int, now: datetime) -> str:
    if _vacancy(conn, vacancy_id)["state"] in ("open", "on_hold"):
        raise Conflict("вакансия уже открыта")
    run(conn, "UPDATE vacancy SET state = 'open', closed_at = NULL, closed_by = NULL WHERE id = %s", (vacancy_id,))
    run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail, at)
                 VALUES (%s, 'edit', 'vacancy', %s, %s, %s)""", (user_id, vacancy_id, j({"reopen": True}), now))
    return "open"
