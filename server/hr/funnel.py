"""Воронка вакансии (S24, S36, S37): переводы по этапам, резерв, закрытие, дата встречи.

Кандидат связан с вакансией через ops.candidate_vacancy; каждый перевод — строка status_history.
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from .db import one, rows, run, val
from .views import cards

# Этапы воронки в порядке прототипа; остальные статусы — итог, этапом не показываются
STAGES = [("picked_by_agent", "Отобран агентом"), ("first_message_sent", "Написали"), ("replied", "Ответил"),
          ("phone_interview", "Телефонное интервью"), ("manager_interview", "Встреча с руководителем"),
          ("test_task", "Тестовое задание"), ("security_check", "Проверка СБ"), ("offer", "Оффер"),
          ("reserve", "Резерв")]
CLOSED = {"hired", "rejected_by_us", "rejected_by_candidate", "refused_contact", "no_show"}
CONTACT = {"first_message_sent", "replied", "phone_interview", "manager_interview", "test_task", "offer"}
INTERVIEW_KIND = {"phone_interview": "phone", "manager_interview": "manager", "test_task": "tech_case"}
ALL = {s for s, _ in STAGES} | CLOSED | {"new"}


class BadStage(ValueError):
    pass


class NotFound(LookupError):
    pass


def move(conn: psycopg.Connection, candidate_id: int, to_status: str, user_id: int, now: datetime, *,
         vacancy_id: int | None = None, comment: str | None = None, scheduled_at: datetime | None = None) -> str:
    if to_status not in ALL:
        raise BadStage(to_status)
    c = one(conn, "SELECT id, status::text AS status FROM candidate WHERE id = %s FOR UPDATE", (candidate_id,))
    if not c:
        raise NotFound
    if vacancy_id is not None:
        if not val(conn, "SELECT 1 FROM vacancy WHERE id = %s", (vacancy_id,)):
            raise NotFound
        run(conn, """INSERT INTO ops.candidate_vacancy (candidate_id, vacancy_id, added_by, added_at)
                     VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""", (candidate_id, vacancy_id, user_id, now))
    run(conn, """UPDATE candidate SET status = %s,
                     last_contact_at = CASE WHEN %s THEN %s ELSE last_contact_at END,
                     first_contact_at = CASE WHEN %s AND first_contact_at IS NULL THEN %s ELSE first_contact_at END,
                     reject_reason = CASE WHEN %s THEN %s ELSE reject_reason END
                 WHERE id = %s""",
        (to_status, to_status in CONTACT, now, to_status in CONTACT, now,
         to_status.startswith("rejected") or to_status == "refused_contact", comment, candidate_id))
    run(conn, """INSERT INTO status_history (candidate_id, from_status, to_status, changed_by, changed_at, comment)
                 VALUES (%s, %s, %s, %s, %s, %s)""", (candidate_id, c["status"], to_status, user_id, now, comment))
    if scheduled_at:
        run(conn, "INSERT INTO interview (candidate_id, vacancy_id, kind, scheduled_at) VALUES (%s, %s, %s, %s)",
            (candidate_id, vacancy_id, INTERVIEW_KIND.get(to_status, "recruiter"), scheduled_at))
    return to_status


def funnel(conn: psycopg.Connection, vacancy_id: int, now: datetime) -> dict:
    v = one(conn, "SELECT id, title, state, opened_at, headcount FROM vacancy WHERE id = %s", (vacancy_id,))
    if not v:
        raise NotFound
    people = rows(conn, """
        SELECT c.id AS candidate_id, c.person_id, c.status::text AS status,
               (SELECT max(h.changed_at) FROM status_history h WHERE h.candidate_id = c.id) AS since
        FROM ops.candidate_vacancy cv JOIN candidate c ON c.id = cv.candidate_id
        WHERE cv.vacancy_id = %s ORDER BY since""", (vacancy_id,))
    info = cards(conn, [p["person_id"] for p in people])
    stages = []
    for code, title in STAGES:
        here = [p for p in people if p["status"] == code]
        for p in here:
            p["days"] = (now - p["since"]).days if p["since"] else 0
            card = info.get(p["person_id"]) or {}
            p.update(name=card.get("name"), label=card.get("label"), photo=bool(card.get("photo")))
        avg = round(sum(p["days"] for p in here) / len(here)) if here else None
        stages.append({"status": code, "title": title, "people": here, "avg_days": avg})
    closed = [p for p in people if p["status"] in CLOSED]
    return {"vacancy": v, "stages": stages, "closed": len(closed)}
