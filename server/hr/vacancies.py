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
               (current_date - v.opened_at) AS open_days,
               (SELECT count(*) FROM ops.candidate_vacancy cv JOIN candidate c ON c.id = cv.candidate_id
                WHERE cv.vacancy_id = v.id AND c.status NOT IN ('hired', 'rejected_by_us', 'rejected_by_candidate',
                                                                'refused_contact', 'no_show')) AS in_work
        FROM vacancy v JOIN role_profile rp ON rp.id = v.role_profile_id
        WHERE %s::bigint[] IS NULL OR v.id = ANY(%s)
        ORDER BY v.opened_at DESC, v.id DESC""", (list(only) if only is not None else None,) * 2)
    return {"open": [v for v in items if v["state"] in ("open", "on_hold")],
            "closed": [v for v in items if v["state"] in ("closed", "cancelled")]}


def create(conn: psycopg.Connection, role_profile_id: int, title: str, user_id: int, headcount: int = 1,
           target_date=None) -> int:
    if not val(conn, "SELECT 1 FROM role_profile WHERE id = %s AND approved_at IS NOT NULL AND is_active",
               (role_profile_id,)):
        raise Invalid("профиль не утверждён")
    if not title.strip() or headcount < 1:
        raise Invalid("название и число ставок")
    return val(conn, """INSERT INTO vacancy (role_profile_id, title, headcount, target_date, recruiter_id, origin)
                        VALUES (%s, %s, %s, %s, %s, 'recruiter') RETURNING id""",
               (role_profile_id, title.strip(), headcount, target_date, user_id))


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
