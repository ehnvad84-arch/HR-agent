"""Формула «обратите внимание» (архитектура, 3.3; S21–S27). Пороги — из settings."""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from .db import j, one, run, setting, val

EVENT_TEXT = {
    "new": "Попал в поле зрения агента",
    "resume_updated": "Обновил резюме",
    "employer_changed": "Сменил работодателя",
    "salary_changed": "Изменил зарплатные ожидания",
    "opened_for_search": "Открыл резюме для поиска работы",
    "skill_added": "Добавил навыки из нашего списка",
    "geo_changed": "Изменил город или готовность к переезду",
    "disappeared": "Резюме скрыто или удалено",
}


def raise_alert(conn: psycopg.Connection, person_id: int, profile: dict, reason: str, headline: str,
                payload: dict, now: datetime) -> int:
    """Один открытый сигнал на человека и код профиля: повторный повод обновляет его (S27)."""
    existing = one(conn, """SELECT a.id FROM alert a JOIN role_profile rp ON rp.id = a.role_profile_id
                            WHERE a.state = 'new' AND a.person_id = %s AND rp.code = %s
                            ORDER BY a.id LIMIT 1 FOR UPDATE OF a""", (person_id, profile["code"]))
    if existing:
        run(conn, """UPDATE alert SET reason = %s, headline = %s, payload = %s, created_at = %s, role_profile_id = %s
                     WHERE id = %s""", (reason, headline, j(payload), now, profile["id"], existing["id"]))
        return existing["id"]
    return val(conn, """INSERT INTO alert (person_id, role_profile_id, reason, headline, payload, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
               (person_id, profile["id"], reason, headline, j(payload), now))


def _what(kinds: list[str]) -> str:
    text = "; ".join(EVENT_TEXT[k].lower() for k in kinds if k != "new") or "резюме изменилось"
    return text[:1].upper() + text[1:]


def evaluate(conn: psycopg.Connection, person_id: int, events: list[tuple[str, dict]], scores: list[dict],
             now: datetime) -> int:
    """Возвращает число созданных или обновлённых сигналов."""
    p = one(conn, "SELECT * FROM person WHERE id = %s", (person_id,))
    if not p or not scores or not events or p["candidate_id"]:
        return 0
    if p["postponed_until"] and p["postponed_until"] > now.date():
        return 0                       # отложенный — без сигналов, даже при «веду особо» (S26)
    kinds = [k for k, _ in events]
    top = max(scores, key=lambda x: x["score"])
    by_code = {x["profile"]["code"]: x for x in scores}

    if p["watch"] == "dropped":
        last = one(conn, """SELECT d.score_at, rp.code, d.created_at FROM decision_log d
                            JOIN role_profile rp ON rp.id = d.role_profile_id
                            WHERE d.person_id = %s AND d.kind = 'reject' AND d.score_at IS NOT NULL
                            ORDER BY d.created_at DESC LIMIT 1""", (person_id,))
        months = int(setting(conn, "score_grew_after_months", 6))
        if not last or last["created_at"] > now - timedelta(days=30 * months) or last["code"] not in by_code:
            return 0
        cur = by_code[last["code"]]
        if cur["score"] - last["score_at"] < int(setting(conn, "alert_score_grew_by", 10)):
            return 0
        raise_alert(conn, person_id, cur["profile"], "score_grew",
                    f"Был отказ, балл вырос с {last['score_at']} до {cur['score']}: {_what(kinds).lower()}",
                    {"score": cur["score"], "was": last["score_at"], "events": kinds}, now)
        return 1

    if "new" in kinds:
        if top["score"] >= int(setting(conn, "alert_score_new", 75)):
            raise_alert(conn, person_id, top["profile"], "new_high_score",
                        f"Новый в поле зрения: {top['score']} по профилю «{top['profile']['title']}»",
                        {"score": top["score"], "events": kinds}, now)
            return 1
    elif top["score"] >= int(setting(conn, "alert_score_pool_event", 65)) or p["watch"] == "watched":
        raise_alert(conn, person_id, top["profile"], "event_on_pool", _what(kinds),
                    {"score": top["score"], "events": kinds, "watched": p["watch"] == "watched"}, now)
        return 1

    depts = set(setting(conn, "donor_alert_departments", []))
    threshold = int(setting(conn, "watch_threshold", 60))
    for x in sorted(scores, key=lambda x: -x["score"]):
        donor = x["s"].features.get("donor_company")
        if (x["profile"].get("dept_code") in depts and x["score"] >= threshold and donor
                and donor["max"] and donor["points"] == donor["max"]):
            raise_alert(conn, person_id, x["profile"], "donor_company",
                        f"Опыт в компании-доноре: {donor['detail']}", {"score": x["score"], "events": kinds}, now)
            return 1
    return 0
