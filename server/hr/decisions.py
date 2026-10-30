"""Решения человека по карточке (S11–S15). Всё — по нажатию человека, агент только готовит (never.md).

«В работу» переводит человека в зону кандидата: основание обработки — резюме, открытое
работодателям на hh (Решения), в decision_log не пишется (CHECK от 200 знаков, схема:850).
«Отложить» и «не наш» — с комментарием не короче decision_comment_min; проверку дублирует СУБД.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from .db import one, run, setting, val
from .views import cards

BASIS_CHANNEL = "hh: резюме открыто работодателям"


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class TooShort(ValueError):
    def __init__(self, minimum: int):
        super().__init__(minimum)
        self.minimum = minimum


def _person(conn: psycopg.Connection, person_id: int) -> dict:
    p = one(conn, "SELECT * FROM person WHERE id = %s FOR UPDATE", (person_id,))
    if not p:
        raise NotFound
    return p


def _open_vacancy(conn: psycopg.Connection, person_id: int) -> int | None:
    """Открытая вакансия по профилю с лучшим баллом человека (по коду: версии профиля не важны)."""
    return val(conn, """
        SELECT v.id FROM match m
        JOIN role_profile rp ON rp.id = m.role_profile_id
        JOIN role_profile vp ON vp.code = rp.code
        JOIN vacancy v ON v.role_profile_id = vp.id AND v.state = 'open'
        WHERE m.person_id = %s ORDER BY m.score DESC, v.opened_at LIMIT 1""", (person_id,))


def take(conn: psycopg.Connection, person_id: int, user_id: int, now: datetime,
         vacancy_id: int | None = None) -> int:
    """S11: кандидат; в воронку открытой вакансии или в резерв без вакансии. Одной транзакцией."""
    p = _person(conn, person_id)
    if p["candidate_id"]:
        raise Conflict("человек уже в работе")
    if vacancy_id is not None and not val(conn, "SELECT 1 FROM vacancy WHERE id = %s AND state = 'open'",
                                          (vacancy_id,)):
        raise Conflict("вакансия не открыта")
    vacancy_id = vacancy_id or _open_vacancy(conn, person_id)
    card = cards(conn, [person_id])[person_id]
    o = one(conn, """SELECT o.title, o.city, o.employer_current, o.relocation_ready, o.source_id, o.url
                     FROM observation o WHERE o.person_id = %s ORDER BY o.last_seen DESC LIMIT 1""",
            (person_id,)) or {}
    status = "picked_by_agent" if vacancy_id else "reserve"
    years = int(setting(conn, "retention_candidate_years", 3))
    cid = val(conn, """
        INSERT INTO candidate (person_id, fio, city, current_position, current_employer, relocation_ready, status,
                               owner_id, source_id, resume_url, retention_until)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (person_id, card["name"] or card["label"], o.get("city") or p["city"], o.get("title"),
         o.get("employer_current"), o.get("relocation_ready"), status, user_id, o.get("source_id"), o.get("url"),
         (now + timedelta(days=365 * years)).date()))
    run(conn, """INSERT INTO consent (candidate_id, basis, obtained_at, channel)
                 VALUES (%s, 'candidate_initiated', %s, %s)""", (cid, now, BASIS_CHANNEL))
    run(conn, """INSERT INTO status_history (candidate_id, from_status, to_status, changed_by, changed_at, comment)
                 VALUES (%s, NULL, %s, %s, %s, %s)""",
        (cid, status, user_id, now, "в работу" + ("" if vacancy_id else ": открытой вакансии нет, в резерве")))
    if vacancy_id:
        run(conn, """INSERT INTO ops.candidate_vacancy (candidate_id, vacancy_id, added_by, added_at)
                     VALUES (%s, %s, %s, %s)""", (cid, vacancy_id, user_id, now))
    run(conn, "UPDATE person SET candidate_id = %s WHERE id = %s", (cid, person_id))
    run(conn, """UPDATE alert SET state = 'taken', decided_by = %s, decided_at = %s
                 WHERE person_id = %s AND state = 'new'""", (user_id, now, person_id))
    return cid


def decide(conn: psycopg.Connection, person_id: int, kind: str, comment: str, user_id: int, now: datetime,
           role_profile_id: int | None = None) -> None:
    """S12: «отложить» или «не наш» с развёрнутым комментарием (S13)."""
    minimum = int(setting(conn, "decision_comment_min", 200))
    if len(comment.strip()) < minimum:
        raise TooShort(minimum)
    p = _person(conn, person_id)
    score = val(conn, """SELECT max(score) FROM match WHERE person_id = %s
                         AND (%s::int IS NULL OR role_profile_id = %s)""",
                (person_id, role_profile_id, role_profile_id))
    run(conn, """INSERT INTO decision_log (person_id, kind, comment, author_id, created_at, role_profile_id, score_at)
                 VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (person_id, kind, comment.strip(), user_id, now, role_profile_id, score))
    if kind == "postpone":
        months = int(setting(conn, "reserve_touch_months", 3))
        run(conn, "UPDATE person SET postponed_until = %s WHERE id = %s",
            ((now + timedelta(days=30 * months + 1)).date(), person_id))
        state = "postponed"
    else:
        if role_profile_id is None:       # «не наш» без профиля — человек уходит из выдачи совсем
            run(conn, "UPDATE person SET watch = 'dropped' WHERE id = %s", (p["id"],))
        state = "dismissed"
    run(conn, """UPDATE alert SET state = %s, decided_by = %s, decided_at = %s
                 WHERE person_id = %s AND state = 'new' AND (%s::int IS NULL OR role_profile_id = %s)""",
        (state, user_id, now, person_id, role_profile_id, role_profile_id))


def set_watch(conn: psycopg.Connection, person_id: int, on: bool, user_id: int, now: datetime) -> str:
    """S14: «вести особо» — порог для человека отменяется (alerts.evaluate смотрит watch)."""
    _person(conn, person_id)
    mode = "watched" if on else "observed"
    run(conn, "UPDATE person SET watch = %s, watch_by = %s, watch_at = %s WHERE id = %s",
        (mode, user_id if on else None, now if on else None, person_id))
    return mode


def confirm_social(conn: psycopg.Connection, social_id: int, user_id: int, now: datetime) -> str:
    """S15: человек подтвердил вероятный профиль в сети."""
    s = one(conn, "SELECT confidence::text AS confidence FROM social_profile WHERE id = %s FOR UPDATE", (social_id,))
    if not s:
        raise NotFound
    if s["confidence"] == "rejected":
        raise Conflict("профиль отклонён")
    run(conn, """UPDATE social_profile SET confidence = 'confirmed', confirmed_by = %s, confirmed_at = %s
                 WHERE id = %s""", (user_id, now, social_id))
    return "confirmed"
