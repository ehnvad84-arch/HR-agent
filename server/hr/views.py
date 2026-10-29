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


# ------------------------------------------------------------------ досье

def _experience(raw: dict) -> list[dict]:
    """Места работы из ответа площадки — только поля резюме, без контактов (белый список)."""
    out = []
    for e in raw.get("experience") or []:
        if isinstance(e, dict):
            out.append({"company": e.get("company") or (e.get("employer") or {}).get("name"),
                        "position": e.get("position"), "start": e.get("start"), "end": e.get("end"),
                        "description": e.get("description")})
    return out


def dossier(conn: psycopg.Connection, person_id: int, full: bool = True) -> dict | None:
    """S6: карточка, баллы с разбором, резюме по источникам; full — ещё версии, следы в сети, решения."""
    card = cards(conn, [person_id]).get(person_id)
    if not card:
        return None
    matches = rows(conn, """
        SELECT m.role_profile_id, rp.title AS role_title, rp.code AS role_code, m.profile_version, m.score,
               m.features, m.explanation, m.model, m.scored_at
        FROM match m JOIN role_profile rp ON rp.id = m.role_profile_id
        WHERE m.person_id = %s ORDER BY m.score DESC""", (person_id,))
    obs = rows(conn, """
        SELECT o.id, s.code AS source, s.title AS source_title, o.url, o.title, o.city, o.employer_current,
               o.employers, o.skills, o.exp_years, o.avg_tenure_months, o.salary_expect, o.relocation_ready,
               o.open_for_search, o.updated_at_src, o.first_seen, o.last_seen, o.is_gone,
               o.raw->'education' AS education_raw, o.raw->>'skills' AS about, o.raw
        FROM observation o JOIN source s ON s.id = o.source_id
        WHERE o.person_id = %s ORDER BY o.last_seen DESC""", (person_id,))
    for o in obs:
        raw = o.pop("raw") or {}
        o["experience"] = _experience(raw)
        edu = o.pop("education_raw")
        o["education"] = ((edu.get("level") or {}).get("name") if isinstance(edu, dict) else None)
    events = rows(conn, """SELECT kind::text AS kind, detail, happened_at FROM obs_event
                           WHERE person_id = %s ORDER BY happened_at DESC LIMIT 100""", (person_id,))
    cand = None
    if card["candidate_id"]:
        cand = rows(conn, """
            SELECT c.id, c.status::text AS status, c.created_at, c.last_contact_at,
                   EXISTS (SELECT 1 FROM contact k WHERE k.candidate_id = c.id) AS contact_opened
            FROM candidate c WHERE c.id = %s""", (card["candidate_id"],))[0]
    out = {"person": card, "matches": matches, "observations": obs, "events": events, "candidate": cand}
    if full:
        out["versions"] = rows(conn, """
            SELECT v.observation_id, v.captured_at, v.diff FROM obs_version v
            JOIN observation o ON o.id = v.observation_id
            WHERE o.person_id = %s ORDER BY v.captured_at DESC LIMIT 50""", (person_id,))
        out["social"] = rows(conn, """
            SELECT id, kind::text AS kind, url, handle, confidence::text AS confidence, matched_by, summary,
                   topics, last_activity, confirmed_at
            FROM social_profile WHERE person_id = %s ORDER BY confidence, id""", (person_id,))
        out["decisions"] = rows(conn, """
            SELECT d.kind, d.comment, d.created_at, u.full_name AS author, d.role_profile_id
            FROM decision_log d JOIN app_user u ON u.id = d.author_id
            WHERE d.person_id = %s ORDER BY d.created_at DESC""", (person_id,))
    return out
