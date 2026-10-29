"""Чтение для экранов рабочего места (AD-1: запись — в других модулях пакета).

Карточка человека: имя — из кандидата или из ответа hh, если hh его отдал (уточнение AD-3,
решение человека 2026-10-29), иначе ярлык «должность, город». Контактов в карточке нет.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from .db import rows, setting

# Граница «сегодня и завтра» — по московскому времени: рабочее место в одной таймзоне
TZ = "Europe/Moscow"

_CARD = """
    SELECT p.id AS person_id, p.label, p.city, p.watch::text AS watch, p.candidate_id,
           coalesce(c.fio, (SELECT nullif(btrim(concat_ws(' ', o.raw->>'last_name', o.raw->>'first_name')), '')
                            FROM observation o WHERE o.person_id = p.id
                            ORDER BY o.last_seen DESC LIMIT 1)) AS name,
           EXISTS (SELECT 1 FROM ops.person_photo ph JOIN observation o ON o.id = ph.observation_id
                   WHERE o.person_id = p.id AND ph.state = 'ok') AS photo,
           (SELECT max(m.score) FROM match m WHERE m.person_id = p.id) AS best_score
    FROM person p LEFT JOIN candidate c ON c.id = p.candidate_id
    WHERE p.id = ANY(%s)"""


def cards(conn: psycopg.Connection, person_ids: list[int]) -> dict[int, dict]:
    ids = sorted({i for i in person_ids if i})
    return {r["person_id"]: r for r in rows(conn, _CARD, (ids,))} if ids else {}


def _card(c: dict | None) -> dict:
    c = c or {}
    return {"name": c.get("name"), "label": c.get("label"), "photo": bool(c.get("photo")), "city": c.get("city")}


def today(conn: psycopg.Connection, now: datetime) -> dict:
    """S4, S5, S36, S37: сигналы, подтверждения, зависшие, встречи на сегодня и завтра."""
    limit = int(setting(conn, "daily_cards_limit", 12))
    signals = rows(conn, """
        SELECT a.id AS alert_id, a.person_id, a.reason::text AS reason, a.headline, a.created_at,
               rp.id AS role_profile_id, rp.title AS role_title, m.score
        FROM alert a
        LEFT JOIN role_profile rp ON rp.id = a.role_profile_id
        LEFT JOIN match m ON m.person_id = a.person_id AND m.role_profile_id = a.role_profile_id
        WHERE a.state = 'new'
        ORDER BY m.score DESC NULLS LAST, a.created_at DESC
        LIMIT %s""", (limit,))
    actions = rows(conn, """
        SELECT q.id AS action_id, q.kind::text AS kind, q.person_id, q.candidate_id, q.vacancy_id, q.proposed_at,
               v.title AS vacancy_title
        FROM action_queue q LEFT JOIN vacancy v ON v.id = q.vacancy_id
        WHERE q.state = 'proposed' ORDER BY q.proposed_at""")
    stalled = rows(conn, """
        SELECT s.id AS candidate_id, c.person_id, s.status::text AS status, s.days_silent, s.last_contact_at
        FROM v_stalled s JOIN candidate c ON c.id = s.id ORDER BY s.days_silent DESC""")
    day0 = rows(conn, "SELECT date_trunc('day', %s AT TIME ZONE %s) AS d", (now, TZ))[0]["d"]
    meetings = rows(conn, """
        SELECT i.id AS interview_id, i.candidate_id, c.person_id, i.kind, i.scheduled_at, v.title AS vacancy_title
        FROM interview i JOIN candidate c ON c.id = i.candidate_id LEFT JOIN vacancy v ON v.id = i.vacancy_id
        WHERE i.scheduled_at >= (%s::timestamp AT TIME ZONE %s) AND i.scheduled_at < (%s::timestamp AT TIME ZONE %s)
          AND i.result IS NULL
        ORDER BY i.scheduled_at""", (day0, TZ, day0 + timedelta(days=2), TZ))
    people = cards(conn, [r["person_id"] for r in signals + actions + stalled + meetings])
    for r in signals + actions + stalled + meetings:
        r.update(_card(people.get(r["person_id"])))
    return {"signals": signals, "actions": actions, "stalled": stalled, "meetings": meetings}
