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
           lo.exp_years, lo.salary_expect, lo.employer_current, lo.relocation_ready, lo.open_for_search,
           lo.updated_at_src, lo.skills AS top_skills,
           (SELECT max(m.score) FROM match m WHERE m.person_id = p.id AND m.role_profile_id IN (
                SELECT DISTINCT ON (x.code) x.id FROM role_profile x
                WHERE x.is_active AND x.approved_at IS NOT NULL ORDER BY x.code, x.approved_at DESC, x.id DESC))
               AS best_score
    FROM person p LEFT JOIN candidate c ON c.id = p.candidate_id
    LEFT JOIN LATERAL (   -- свежее резюме человека: короткая сводка для карточки
        SELECT o.exp_years, o.salary_expect, o.employer_current, o.relocation_ready, o.open_for_search,
               o.updated_at_src, o.skills[1:5] AS skills
        FROM observation o WHERE o.person_id = p.id
        ORDER BY o.is_gone, coalesce(o.updated_at_src, o.last_seen) DESC LIMIT 1) lo ON true
    WHERE p.id = ANY(%s)"""


def cards(conn: psycopg.Connection, person_ids: list[int]) -> dict[int, dict]:
    ids = sorted({i for i in person_ids if i})
    return {r["person_id"]: r for r in rows(conn, _CARD, (ids,))} if ids else {}


SUMMARY = ("exp_years", "salary_expect", "employer_current", "relocation_ready", "open_for_search", "updated_at_src")


def _card(c: dict | None) -> dict:
    """Карточка человека для списков: имя или ярлык, фото и короткая сводка резюме — без контактов."""
    c = c or {}
    summary = {k: c.get(k) for k in SUMMARY}
    summary["exp_years"] = float(summary["exp_years"]) if summary["exp_years"] is not None else None
    summary["skills"] = list(c.get("top_skills") or [])
    return {"name": c.get("name"), "label": c.get("label"), "photo": bool(c.get("photo")), "city": c.get("city"),
            "summary": summary}


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


_MANAGER_VACANCIES = """
    SELECT v.id, v.title, v.headcount, v.target_date, rp.code AS role_code, u.full_name AS recruiter,
           (SELECT count(*) FROM match m JOIN role_profile x ON x.id = m.role_profile_id
            WHERE x.code = rp.code AND x.id IN (CURRENT) AND m.score >= %s) AS matching
    FROM vacancy v JOIN role_profile rp ON rp.id = v.role_profile_id
    LEFT JOIN app_user u ON u.id = v.recruiter_id
    WHERE v.state = 'open' AND v.id = ANY(%s) ORDER BY v.opened_at DESC"""


def manager_today(conn: psycopg.Connection, sc, now: datetime) -> dict:
    """manager-cabinet S6: новые и изменившиеся люди по должностям поддерева, открытые вакансии поддерева
    со счётом подходящих, его передачи. Без действий, зависших и встреч отдела персонала."""
    limit = int(setting(conn, "daily_cards_limit", 12))
    signals = rows(conn, """
        SELECT a.id AS alert_id, a.person_id, a.reason::text AS reason, a.headline, a.created_at,
               rp.id AS role_profile_id, rp.title AS role_title, m.score
        FROM alert a
        JOIN role_profile rp ON rp.id = a.role_profile_id
        LEFT JOIN match m ON m.person_id = a.person_id AND m.role_profile_id = a.role_profile_id
        WHERE a.state = 'new' AND rp.code = ANY(%s)
        ORDER BY m.score DESC NULLS LAST, a.created_at DESC
        LIMIT %s""", (list(sc.codes), limit))
    floor = int(setting(conn, "watch_threshold", 60))
    vacancies = rows(conn, _MANAGER_VACANCIES.replace("(CURRENT)", f"({CURRENT})"), (floor, list(sc.vacancies)))
    people = cards(conn, [r["person_id"] for r in signals])
    for r in signals:
        r.update(_card(people.get(r["person_id"])))
    return {"signals": signals, "vacancies": vacancies, "handoffs": []}


def for_manager(d: dict, sc) -> dict:
    """Досье руководителю (M2): только баллы его должностей, без ссылок на резюме площадки, следов в сети,
    решений отдела персонала и версий резюме; в свободном тексте («О себе», описания мест работы) телефоны,
    почта и ссылки скрыты — через них выходят на человека в обход отдела персонала."""
    from .learning import strip_contacts
    d["matches"] = [m for m in d["matches"] if sc.code_ok(m["role_code"])]
    for o in d["observations"]:
        o.pop("url", None)
        if o.get("about"):
            o["about"] = strip_contacts(o["about"])
        for e in o.get("experience") or []:
            if e.get("description"):
                e["description"] = strip_contacts(e["description"])
    for k in ("social", "decisions", "versions"):
        d.pop(k, None)
    return d


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


# ------------------------------------------------------------------ база

# действующие версии профилей — как pipeline.current_profiles: оценки прежних версий остаются историей
CURRENT = """SELECT DISTINCT ON (x.code) x.id FROM role_profile x
             WHERE x.is_active AND x.approved_by IS NOT NULL AND x.approved_at IS NOT NULL
             ORDER BY x.code, x.approved_at DESC, x.id DESC"""

SORTS = {"name": "name", "city": "city", "role": "role_title", "dept": "dept", "score": "score", "where": "where_",
         "source": "sources", "socn": "socn", "upd": "updated",
         "my": "my_score"}

_BASE = """
WITH best AS (
    SELECT DISTINCT ON (m.person_id) m.person_id, m.score, rp.id AS role_profile_id, rp.code AS role_code,
           rp.title AS role_title,
           d.code AS dept_code, d.title AS dept
    FROM match m JOIN role_profile rp ON rp.id = m.role_profile_id JOIN department d ON d.id = rp.department_id
    WHERE (%(code)s::text IS NULL OR rp.code = %(code)s) AND rp.id IN (CURRENT)
      AND (%(codes)s::text[] IS NULL OR rp.code = ANY(%(codes)s))
    ORDER BY m.person_id, m.score DESC
), people AS (
    SELECT p.id AS person_id, p.label, p.city, p.last_event_at AS updated, p.candidate_id,
           coalesce(c.fio, (SELECT nullif(btrim(concat_ws(' ', o.raw->>'last_name', o.raw->>'first_name')), '')
                            FROM observation o WHERE o.person_id = p.id ORDER BY o.last_seen DESC LIMIT 1)) AS name,
           CASE WHEN p.candidate_id IS NOT NULL THEN 'candidate' ELSE p.watch::text END AS where_,
           (SELECT coalesce(array_agg(DISTINCT s.code ORDER BY s.code), '{{}}') FROM observation o
            JOIN source s ON s.id = o.source_id WHERE o.person_id = p.id) AS sources,
           (SELECT count(*) FROM social_profile sp WHERE sp.person_id = p.id AND sp.confidence <> 'rejected') AS socn,
           EXISTS (SELECT 1 FROM ops.person_photo ph JOIN observation o ON o.id = ph.observation_id
                   WHERE o.person_id = p.id AND ph.state = 'ok') AS photo,
           b.score, b.role_profile_id, b.role_code, b.role_title, b.dept_code, b.dept,
           (SELECT um.score FROM ops.user_match um WHERE um.user_id = %(me)s AND um.person_id = p.id
              AND um.role_profile_id = b.role_profile_id) AS my_score
    FROM person p LEFT JOIN candidate c ON c.id = p.candidate_id
    {join} best b ON b.person_id = p.id
    WHERE (%(code)s::text IS NULL OR (p.watch <> 'dropped' AND NOT EXISTS (
            SELECT 1 FROM decision_log dl JOIN role_profile x ON x.id = dl.role_profile_id
            WHERE dl.person_id = p.id AND dl.kind = 'reject' AND x.code = %(code)s)))
)
SELECT *, count(*) OVER () AS total FROM people
WHERE (%(dept)s::text IS NULL OR dept_code = %(dept)s OR dept = %(dept)s)
  AND (%(city)s::text IS NULL OR city ILIKE %(city)s)
  AND (%(role)s::text IS NULL OR role_code = %(role)s OR role_title = %(role)s)
  AND (%(score_min)s::int IS NULL OR score >= %(score_min)s)
  AND (%(where)s::text IS NULL OR where_ = %(where)s)
  AND (%(source)s::text IS NULL OR %(source)s = ANY(sources))
ORDER BY {order} {dir} NULLS LAST, person_id
LIMIT %(limit)s OFFSET %(offset)s"""


def base(conn: psycopg.Connection, *, dept=None, city=None, role=None, score_min=None, where=None, source=None,
         sort="score", direction="desc", page=0, role_code=None, codes=None, me=None) -> dict:
    """S19, S22: таблица людей. Сортировка — только по колонкам из SORTS (имя колонки в SQL не из запроса).
    role_code — подходящие к вакансии: только оценки по этому профилю, без «не наш» по нему и без dropped."""
    if sort not in SORTS or direction not in ("asc", "desc"):
        raise ValueError("сортировка")
    size = int(setting(conn, "base_page_size", 50))
    # codes — должности руководителя (manager-cabinet): только люди с баллом по ним
    sql = _BASE.replace("(CURRENT)", f"({CURRENT})").format(join="JOIN" if role_code or codes is not None
                                                             else "LEFT JOIN",
                                                             order=SORTS[sort], dir=direction)
    items = rows(conn, sql, {"code": role_code, "codes": list(codes) if codes is not None else None,
                             "me": me, "dept": dept, "city": city, "role": role, "score_min": score_min,
                             "where": where, "source": source, "limit": size, "offset": max(page, 0) * size})
    total = items[0]["total"] if items else 0
    for r in items:
        r.pop("total")
        r["where"] = r.pop("where_")
        r["sources"] = list(r["sources"] or [])
    return {"items": items, "total": total, "page": page, "page_size": size}


def schema(conn: psycopg.Connection) -> dict:
    """S34: последний прогон каждой фоновой задачи и объём базы — живые цифры для экрана «Схема»."""
    jobs = rows(conn, """SELECT DISTINCT ON (job) job, started_at, finished_at, ok, stats, error
                         FROM ops.job_run ORDER BY job, started_at DESC, id DESC""")
    counts = rows(conn, """SELECT (SELECT count(*) FROM observation) AS observations,
                                  (SELECT count(*) FROM person) AS persons,
                                  (SELECT count(*) FROM candidate) AS candidates,
                                  (SELECT count(*) FROM role_profile WHERE is_active AND approved_at IS NOT NULL)
                                      AS profiles,
                                  (SELECT count(*) FROM alert WHERE state = 'new') AS signals""")[0]
    return {"jobs": jobs, "counts": counts}
