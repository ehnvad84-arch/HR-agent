"""Воронка вакансии (S24, S36, S37; funnel-report S10): доска по этапам набора, резерв, дата встречи.

Кандидат связан с вакансией через ops.candidate_vacancy и отметки этапов ops.candidate_stage (hr/stages.py);
статус кандидата пишется в status_history. STAGES — прежние этапы-статусы: подписи статусов и откат на v1.2.0.
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from . import stages
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
    """Статус кандидата. С вакансией — сначала вход в её воронку (funnel-report S10: «Отобранные резюме»); этапы
    дальше отмечает stages.mark, статус здесь не двигает воронку."""
    if to_status not in ALL:
        raise BadStage(to_status)
    if not val(conn, "SELECT 1 FROM candidate WHERE id = %s FOR UPDATE", (candidate_id,)):
        raise NotFound
    if vacancy_id is not None:
        try:
            stages.enter(conn, candidate_id, vacancy_id, user_id, now)
        except stages.NotFound:
            raise NotFound from None
    c = one(conn, "SELECT status::text AS status FROM candidate WHERE id = %s", (candidate_id,))
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


def has_open_stage(conn: psycopg.Connection, candidate_id: int) -> bool:
    """Кандидат стоит на этапе какой-нибудь вакансии без исхода."""
    return bool(val(conn, """SELECT 1 FROM ops.candidate_stage WHERE candidate_id = %s AND outcome IS NULL
                             AND cancelled_at IS NULL LIMIT 1""", (candidate_id,)))


def _agent_picks(conn, vacancy_id: int, now: datetime) -> list[dict]:
    """S10: отобранные агентом по должности вакансии (новые сигналы), ещё не взятые в работу. В воронку и в отчёт
    они не входят — только «Взять в работу» рекрутера ставит человека на первый этап."""
    picks = rows(conn, """
        SELECT DISTINCT ON (a.person_id) a.person_id, a.id AS alert_id, a.reason::text AS reason, a.headline,
               a.created_at, m.score
        FROM alert a JOIN person p ON p.id = a.person_id AND p.candidate_id IS NULL
        LEFT JOIN role_profile rp ON rp.id = a.role_profile_id
        LEFT JOIN match m ON m.person_id = a.person_id AND m.role_profile_id = a.role_profile_id
        WHERE a.state = 'new'
          AND (a.vacancy_id = %(v)s OR rp.code = (SELECT x.code FROM vacancy vv JOIN role_profile x
                                                   ON x.id = vv.role_profile_id WHERE vv.id = %(v)s))
        ORDER BY a.person_id, a.created_at DESC""", {"v": vacancy_id})
    info = cards(conn, [p["person_id"] for p in picks])
    for p in picks:
        card = info.get(p["person_id"]) or {}
        p.update(name=card.get("name"), label=card.get("label"), photo=bool(card.get("photo")),
                 days=(now - p["created_at"]).days)
    return sorted(picks, key=lambda p: (p["score"] is None, -(p["score"] or 0), p["person_id"]))


def funnel(conn: psycopg.Connection, vacancy_id: int, now: datetime) -> dict:
    """Доска вакансии (funnel-report): колонки — этапы действующего набора по slot, люди — открытые отметки;
    у человека — последняя отметка пары (кто и когда: «Отменить последнюю отметку»), заметки — комментарии
    отметок. Ниже — «Отобраны агентом» (в отчёт не входят). Чтение ничего не пишет: у вакансии без набора
    (заведена v1.2.0 и закрыта до выпуска) показываются 8 стандартных этапов."""
    v = one(conn, "SELECT id, title, state, opened_at, headcount FROM vacancy WHERE id = %s", (vacancy_id,))
    if not v:
        raise NotFound
    st = stages.current_set(conn, vacancy_id)
    items = st["items"] if st else stages.standard_items(conn)
    row_title = {r["code"]: r["title"] for r in rows(conn, "SELECT code, title FROM ops.funnel_row")}
    titles = {i["slot"]: i["label"] or i["title"] for i in items}
    people = rows(conn, """
        SELECT cs.candidate_id, c.person_id, c.status::text AS status, cs.slot, cs.entered_at AS since, cs.carried
        FROM ops.candidate_stage cs JOIN candidate c ON c.id = cs.candidate_id
        WHERE cs.vacancy_id = %s AND cs.outcome IS NULL AND cs.cancelled_at IS NULL
        ORDER BY cs.entered_at, cs.id""", (vacancy_id,))
    last = {m.pop("candidate_id"): m for m in rows(conn, """
        SELECT DISTINCT ON (cs.candidate_id) cs.candidate_id, cs.decided_by AS by, u.full_name AS by_name,
               cs.decided_at AS at, cs.outcome, cs.slot
        FROM ops.candidate_stage cs LEFT JOIN app_user u ON u.id = cs.decided_by
        WHERE cs.vacancy_id = %s AND cs.cancelled_at IS NULL AND cs.outcome IS NOT NULL
        ORDER BY cs.candidate_id, cs.decided_at DESC, cs.id DESC""", (vacancy_id,))}
    notes: dict[int, list] = {}
    for n in rows(conn, """SELECT cs.candidate_id, cs.comment, cs.decided_at AS changed_at, cs.outcome, cs.slot,
                                  s.title AS stage, u.full_name AS author
                           FROM ops.candidate_stage cs JOIN ops.stage s ON s.id = cs.stage_id
                           LEFT JOIN app_user u ON u.id = cs.decided_by
                           WHERE cs.vacancy_id = %s AND cs.cancelled_at IS NULL
                             AND coalesce(btrim(cs.comment), '') <> ''
                           ORDER BY cs.decided_at DESC, cs.id DESC""", (vacancy_id,)):
        n["stage"] = titles.get(n["slot"]) or n["stage"]
        notes.setdefault(n.pop("candidate_id"), []).append(n)
    info = cards(conn, [p["person_id"] for p in people])
    columns = []
    for item in items:
        here = [p for p in people if p["slot"] == item["slot"]]
        for p in here:
            p["days"] = (now - p["since"]).days if p["since"] else 0
            card = info.get(p["person_id"]) or {}
            p.update(name=card.get("name"), label=card.get("label"), photo=bool(card.get("photo")),
                     notes=notes.get(p["candidate_id"], [])[:5], last_mark=last.get(p["candidate_id"]))
        avg = round(sum(p["days"] for p in here) / len(here)) if here else None
        columns.append({"slot": item["slot"], "pos": item["pos"], "stage_id": item["stage_id"],
                        "title": titles[item["slot"]], "stage": item["title"], "report_row": item["report_row"],
                        "row_title": row_title[item["report_row"]], "people": here, "avg_days": avg})
    closed = val(conn, """SELECT count(DISTINCT candidate_id) FROM ops.candidate_stage o
                          WHERE o.vacancy_id = %s AND o.cancelled_at IS NULL
                            AND NOT EXISTS (SELECT 1 FROM ops.candidate_stage x
                                            WHERE x.candidate_id = o.candidate_id AND x.vacancy_id = o.vacancy_id
                                              AND x.outcome IS NULL AND x.cancelled_at IS NULL)""", (vacancy_id,))
    return {"vacancy": v, "set": {"id": st["id"], "version": st["version"]} if st else None, "stages": columns,
            "agent": _agent_picks(conn, vacancy_id, now), "closed": closed}
