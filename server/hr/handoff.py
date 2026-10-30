"""Передача кандидата руководителем в отдел персонала (manager-cabinet S9–S11).

Руководитель выбирает открытую вакансию своего поддерева и пишет, чем человек понравился. Передача видна
ответственному HR вакансии (vacancy.recruiter_id на момент чтения) и руководителю отдела персонала.
«Взять» — decisions.take с этой вакансией; если человек уже кандидат — только привязка к вакансии.
«Отклонить» — причина видна руководителю; в decision_log не пишется, выдачу не меняет.
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from . import decisions, learning, scope
from .db import one, rows, run, setting, val
from .funnel import CLOSED, STAGES
from .views import _card, cards

STAGE_TITLE = dict(STAGES) | {"hired": "Вышел на работу", "rejected_by_us": "Отказ", "rejected_by_candidate":
                              "Отказался сам", "refused_contact": "Не захотел общаться", "no_show": "Не пришёл"}


class Invalid(ValueError):
    pass


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class Forbidden(PermissionError):
    pass


def comment_min(conn) -> int:
    return int(setting(conn, "handoff_comment_min", 50))


def options(conn: psycopg.Connection, sc: scope.Scope, person_id: int) -> dict:
    """Окно «Передать»: открытые вакансии поддерева, сверху — где у человека есть балл."""
    if not scope.person_ok(conn, sc, person_id):
        raise NotFound
    vac = rows(conn, """
        SELECT v.id, v.title, u.full_name AS recruiter,
               (SELECT max(m.score) FROM match m JOIN role_profile x ON x.id = m.role_profile_id
                WHERE m.person_id = %(p)s AND x.code = rp.code) AS score,
               EXISTS (SELECT 1 FROM ops.handoff h WHERE h.person_id = %(p)s AND h.vacancy_id = v.id) AS already
        FROM vacancy v JOIN role_profile rp ON rp.id = v.role_profile_id LEFT JOIN app_user u ON u.id = v.recruiter_id
        WHERE v.state = 'open' AND (%(all)s OR v.id = ANY(%(vac)s))
        ORDER BY score DESC NULLS LAST, v.opened_at DESC""",
               {"p": person_id, "all": sc.all, "vac": list(sc.vacancies)})
    return {"comment_min": comment_min(conn), "vacancies": vac}


def send(conn: psycopg.Connection, sc: scope.Scope, user_id: int, person_id: int, vacancy_id: int, comment: str,
         now: datetime) -> dict:
    if not scope.person_ok(conn, sc, person_id) or not sc.vacancy_ok(vacancy_id):
        raise NotFound
    v = one(conn, """SELECT v.state, u.full_name AS recruiter FROM vacancy v LEFT JOIN app_user u
                     ON u.id = v.recruiter_id WHERE v.id = %s""", (vacancy_id,))
    if not v:
        raise NotFound
    if v["state"] != "open":
        raise Conflict("вакансия не открыта")
    minimum = comment_min(conn)
    if len((comment or "").strip()) < minimum:
        raise Invalid(f"напишите, чем понравился: не короче {minimum} знаков")
    if val(conn, "SELECT 1 FROM ops.handoff WHERE person_id = %s AND vacancy_id = %s", (person_id, vacancy_id)):
        raise Conflict("уже передан в эту вакансию")
    hid = val(conn, """INSERT INTO ops.handoff (person_id, vacancy_id, from_user, comment, created_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
              (person_id, vacancy_id, user_id, comment.strip(), now))
    return {"id": hid, "state": "sent", "recruiter": v["recruiter"]}


_LIST = """
    SELECT h.id, h.person_id, h.vacancy_id, v.title AS vacancy_title, h.comment, h.state, h.created_at,
           h.decided_at, h.reject_reason, h.candidate_id, f.full_name AS from_user, fu.title AS from_unit,
           r.full_name AS recruiter, d.full_name AS decided_by, c.status::text AS stage,
           c.reject_reason AS candidate_reason,
           (SELECT max(m.score) FROM match m JOIN role_profile x ON x.id = m.role_profile_id
             JOIN role_profile vp ON vp.code = x.code WHERE m.person_id = h.person_id AND vp.id = v.role_profile_id)
             AS score
    FROM ops.handoff h JOIN vacancy v ON v.id = h.vacancy_id
    JOIN app_user f ON f.id = h.from_user LEFT JOIN org_unit fu ON fu.id = f.org_unit_id
    LEFT JOIN app_user r ON r.id = v.recruiter_id LEFT JOIN app_user d ON d.id = h.decided_by
    LEFT JOIN candidate c ON c.id = h.candidate_id
    WHERE {where} ORDER BY h.created_at DESC LIMIT 200"""


def _decorate(conn, items: list[dict]) -> list[dict]:
    info = cards(conn, [h["person_id"] for h in items])
    for h in items:
        h.update(_card(info.get(h["person_id"])))
        h["stage_title"] = STAGE_TITLE.get(h["stage"]) if h["stage"] else None
        h["closed"] = h["stage"] in CLOSED if h["stage"] else False
    return items


def mine(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """S10: судьба переданных руководителем. В причине отказа телефоны, почта и ссылки скрыты (M2)."""
    items = _decorate(conn, rows(conn, _LIST.format(where="h.from_user = %s"), (user_id,)))
    for h in items:
        for k in ("reject_reason", "candidate_reason"):
            if h.get(k):
                h[k] = learning.mask(conn, h[k])[0]
    return items


def inbox(conn: psycopg.Connection, user_id: int, see_all: bool, state: str | None = "sent") -> list[dict]:
    """S11: передачи отделу персонала — ответственному HR его, руководителю отдела персонала все."""
    where = "(%(all)s OR v.recruiter_id = %(me)s) AND (%(state)s::text IS NULL OR h.state = %(state)s)"
    return _decorate(conn, rows(conn, _LIST.format(where=where), {"all": see_all, "me": user_id, "state": state}))


def _get(conn, handoff_id: int, user, see_all: bool) -> dict:
    h = one(conn, """SELECT h.*, v.recruiter_id FROM ops.handoff h JOIN vacancy v ON v.id = h.vacancy_id
                     WHERE h.id = %s FOR UPDATE OF h""", (handoff_id,))
    if not h:
        raise NotFound
    if not see_all and h["recruiter_id"] != user:
        raise Forbidden("передача другого ответственного")
    if h["state"] != "sent":
        raise Conflict("передача уже разобрана")
    return h


def take(conn: psycopg.Connection, handoff_id: int, user_id: int, see_all: bool, now: datetime) -> dict:
    h = _get(conn, handoff_id, user_id, see_all)
    cid = val(conn, "SELECT candidate_id FROM person WHERE id = %s", (h["person_id"],))
    if cid:   # уже кандидат — только в воронку этой вакансии
        run(conn, """INSERT INTO ops.candidate_vacancy (candidate_id, vacancy_id, added_by, added_at)
                     VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""", (cid, h["vacancy_id"], user_id, now))
    else:
        try:
            cid = decisions.take(conn, h["person_id"], user_id, now, h["vacancy_id"])
        except decisions.Conflict as e:
            raise Conflict(str(e)) from None
    run(conn, """UPDATE ops.handoff SET state = 'taken', decided_by = %s, decided_at = %s, candidate_id = %s
                 WHERE id = %s""", (user_id, now, cid, handoff_id))
    return {"candidate_id": cid}


def reject(conn: psycopg.Connection, handoff_id: int, user_id: int, see_all: bool, reason: str,
           now: datetime) -> dict:
    h = _get(conn, handoff_id, user_id, see_all)
    minimum = comment_min(conn)
    if len((reason or "").strip()) < minimum:
        raise Invalid(f"причина — не короче {minimum} знаков: её увидит руководитель")
    run(conn, """UPDATE ops.handoff SET state = 'rejected', decided_by = %s, decided_at = %s, reject_reason = %s
                 WHERE id = %s""", (user_id, now, reason.strip(), h["id"]))
    return {"state": "rejected"}
