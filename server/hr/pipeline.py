"""Конвейер: наблюдение → версия и дифф → события → порог → сшивка → балл → сигналы.

Узлы 1–5 раздела 3.2 архитектуры. Функции работают внутри переданной транзакции:
одно резюме применяется целиком или никак. Модель здесь не вызывается (решение
спеки «модель вне транзакции»): объяснения досчитывает explain после фиксации.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from . import scoring
from .db import j, one, rows, run, setting, val
from .names import name_variants
from .normalize import Norm, label_of

# Поля наблюдения, которые сравниваются между версиями
TRACKED = ("title", "city", "employer_current", "employers", "skills", "exp_years", "avg_tenure_months",
           "salary_expect", "relocation_ready", "open_for_search", "updated_at_src")


@dataclass
class ObsResult:
    id: int
    new: bool
    changed: bool
    person_id: int | None
    was_gone: bool = False
    diff: dict = field(default_factory=dict)


def _fields(n: Norm) -> dict:
    return {k: getattr(n, k) for k in TRACKED} | {"url": n.url}


def _aware(d: datetime) -> datetime:
    """Дата без пояса из файла выгрузки считается UTC, как её сохранит timestamptz."""
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _same(a, b) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        return _aware(a) == _aware(b)
    if a is not None and b is not None and isinstance(b, float):
        return abs(float(a) - b) < 0.05
    if a in (None, "", []) and b in (None, "", []):
        return True
    return a == b


def _plain(v):
    """Значение для jsonb: даты строкой, numeric из базы — числом."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def diff_of(old: dict, n: Norm) -> dict:
    out: dict = {}
    for k in TRACKED:
        a, b = old.get(k), getattr(n, k)
        if k in ("employers", "skills"):
            sa, sb = set(a or []), set(b or [])
            if sa != sb:
                out[k] = {"added": sorted(sb - sa), "removed": sorted(sa - sb)}
        elif not _same(a, b):
            out[k] = {"from": _plain(a), "to": _plain(b)}
    return out


def events_from_diff(diff: dict, keywords: set[str]) -> list[tuple[str, dict]]:
    """Дифф версии → события obs_event (S10). resume_updated — только если других нет."""
    ev: list[tuple[str, dict]] = []
    if diff.get("employer_current", {}).get("to"):
        ev.append(("employer_changed", diff["employer_current"]))
    if "salary_expect" in diff:
        ev.append(("salary_changed", diff["salary_expect"]))
    if diff.get("open_for_search", {}).get("to") is True:
        ev.append(("opened_for_search", {}))
    added = diff.get("skills", {}).get("added", [])
    kw = {k.lower() for k in keywords}
    hit = [s for s in added if s.lower() in kw or any(len(k) > 3 and k in s.lower() for k in kw)]
    if hit:
        ev.append(("skill_added", {"skills": hit}))
    if "city" in diff or diff.get("relocation_ready", {}).get("to") is True:
        ev.append(("geo_changed", {k: diff[k] for k in ("city", "relocation_ready") if k in diff}))
    if not ev and diff:
        ev.append(("resume_updated", {"fields": sorted(diff)}))
    return ev


def upsert_observation(conn: psycopg.Connection, source: dict, n: Norm, raw: dict, now: datetime) -> ObsResult:
    """Записывает резюме. Неизменное — только last_seen; изменившееся — новая версия с диффом."""
    keep = (now + timedelta(days=30 * int(setting(conn, "retention_observation_months", 12)))).date()
    h = n.content_hash()
    old = one(conn, "SELECT * FROM observation WHERE source_id = %s AND external_id = %s FOR UPDATE",
              (source["id"], n.external_id))
    f = _fields(n) | {"raw": j(raw), "hash": h, "keep": keep, "now": now}
    if old is None:
        oid = val(conn, """
            INSERT INTO observation (source_id, external_id, url, title, city, employer_current, employers, skills,
                exp_years, avg_tenure_months, salary_expect, relocation_ready, open_for_search, updated_at_src,
                raw, content_hash, retention_until, first_seen, last_seen)
            VALUES (%(sid)s, %(ext)s, %(url)s, %(title)s, %(city)s, %(employer_current)s, %(employers)s, %(skills)s,
                %(exp_years)s, %(avg_tenure_months)s, %(salary_expect)s, %(relocation_ready)s, %(open_for_search)s,
                %(updated_at_src)s, %(raw)s, %(hash)s, %(keep)s, %(now)s, %(now)s)
            RETURNING id""", f | {"sid": source["id"], "ext": n.external_id})
        run(conn, "INSERT INTO obs_version (observation_id, content_hash, raw, captured_at) VALUES (%s, %s, %s, %s)",
            (oid, h, j(raw), now))
        return ObsResult(oid, True, False, None)
    if old["content_hash"] == h:
        run(conn, "UPDATE observation SET last_seen = %s, is_gone = false, retention_until = %s WHERE id = %s",
            (now, keep, old["id"]))
        return ObsResult(old["id"], False, False, old["person_id"], old["is_gone"])
    diff = diff_of(old, n)
    run(conn, """
        UPDATE observation SET url = %(url)s, title = %(title)s, city = %(city)s,
            employer_current = %(employer_current)s, employers = %(employers)s, skills = %(skills)s,
            exp_years = %(exp_years)s, avg_tenure_months = %(avg_tenure_months)s, salary_expect = %(salary_expect)s,
            relocation_ready = %(relocation_ready)s, open_for_search = %(open_for_search)s,
            updated_at_src = %(updated_at_src)s, raw = %(raw)s, content_hash = %(hash)s,
            last_seen = %(now)s, is_gone = false, retention_until = %(keep)s
        WHERE id = %(id)s""", f | {"id": old["id"]})
    run(conn, """INSERT INTO obs_version (observation_id, content_hash, raw, diff, captured_at)
                 VALUES (%s, %s, %s, %s, %s)""", (old["id"], h, j(raw), j(diff), now))
    return ObsResult(old["id"], False, bool(diff), old["person_id"], old["is_gone"], diff)


# ------------------------------------------------------------------ профили и человек

def current_profiles(conn: psycopg.Connection) -> list[dict]:
    """Действующая версия по каждому коду: активна, утверждена, утверждена последней (S17)."""
    return rows(conn, """
        SELECT DISTINCT ON (rp.code) rp.*, d.code AS dept_code
        FROM role_profile rp JOIN department d ON d.id = rp.department_id
        WHERE rp.is_active AND rp.approved_by IS NOT NULL AND rp.approved_at IS NOT NULL
        ORDER BY rp.code, rp.approved_at DESC, rp.id DESC""")


def params(conn: psycopg.Connection) -> scoring.Params:
    return scoring.Params.load(lambda k, d: setting(conn, k, d))


def norm_person(n: Norm) -> scoring.Person:
    return scoring.Person(title=n.title, city=n.city, employers=n.employers, positions=n.positions,
                          skills=n.skills, exp_years=n.exp_years, avg_tenure_months=n.avg_tenure_months,
                          salary_expect=n.salary_expect, relocation_ready=n.relocation_ready,
                          text=" ".join(filter(None, [n.about, n.education])))


def person_data(conn: psycopg.Connection, person_id: int) -> scoring.Person:
    """Все наблюдения человека: свежее резюме главное, навыки и места работы объединяются."""
    obs = rows(conn, """SELECT * FROM observation WHERE person_id = %s
                        ORDER BY is_gone, coalesce(updated_at_src, last_seen) DESC""", (person_id,))
    p = scoring.Person()
    if not obs:
        return p
    main = obs[0]
    p.title, p.city, p.salary_expect = main["title"], main["city"], main["salary_expect"]
    p.relocation_ready, p.avg_tenure_months = main["relocation_ready"], main["avg_tenure_months"]
    p.exp_years = max((float(o["exp_years"]) for o in obs if o["exp_years"] is not None), default=None)
    for o in obs:
        p.employers += [e for e in o["employers"] if e not in p.employers]
        p.skills += [s for s in o["skills"] if s.lower() not in {x.lower() for x in p.skills}]
        if o["title"] and o["title"] not in p.positions:
            p.positions.append(o["title"])
        about = (o["raw"] or {}).get("skills")
        if isinstance(about, str):
            p.text += " " + about
    p.open_code = bool(one(conn, """SELECT 1 FROM social_profile WHERE person_id = %s
                                    AND kind IN ('github', 'gitlab') AND confidence = 'confirmed'""", (person_id,)))
    return p


def rescore_person(conn: psycopg.Connection, person_id: int, profiles: list[dict] | None = None) -> list[dict]:
    """Пересчёт match по действующим профилям. Разбор — по шаблону; модель досчитает его позже (explain)."""
    profiles = current_profiles(conn) if profiles is None else profiles
    person, prm = person_data(conn, person_id), params(conn)
    out = []
    for pr in profiles:
        s = scoring.score(person, scoring.Profile.from_row(pr), prm)
        prev = one(conn, "SELECT score, profile_version FROM match WHERE person_id = %s AND role_profile_id = %s",
                   (person_id, pr["id"]))
        t = scoring.template_explanation(s)
        text = "\n".join([t["why"], t["gap"], "Спросить: " + "; ".join(t["questions"])])
        unchanged = prev and prev["score"] == s.score and prev["profile_version"] == pr["version"]
        if not unchanged:
            run(conn, """
                INSERT INTO match (person_id, role_profile_id, profile_version, score, features, explanation, model)
                VALUES (%s, %s, %s, %s, %s, %s, 'template')
                ON CONFLICT (person_id, role_profile_id) DO UPDATE SET profile_version = EXCLUDED.profile_version,
                    score = EXCLUDED.score, features = EXCLUDED.features, explanation = EXCLUDED.explanation,
                    model = 'template', scored_at = now()""",
                (person_id, pr["id"], pr["version"], s.score, j(s.features), text))
        out.append({"profile": pr, "score": s.score, "prev": prev["score"] if prev else None, "s": s})
    return out


def add_event(conn: psycopg.Connection, obs_id: int, person_id: int | None, kind: str, detail: dict,
              now: datetime) -> int:
    eid = val(conn, """INSERT INTO obs_event (observation_id, person_id, kind, detail, happened_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""", (obs_id, person_id, kind, j(detail), now))
    if person_id:
        run(conn, "UPDATE person SET last_event_at = %s WHERE id = %s", (now, person_id))
    return eid


def create_person(conn: psycopg.Connection, n: Norm, now: datetime) -> int:
    return val(conn, """INSERT INTO person (label, city, name_variants, nicknames, created_at, last_event_at)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
               (label_of(n), n.city, name_variants(n.name), [x.lower() for x in n.nicknames], now, now))


# ------------------------------------------------------------------ шаг конвейера

@dataclass
class ProcessResult:
    observation_id: int
    new: bool
    changed: bool
    person_id: int | None = None
    person_created: bool = False
    events: list[tuple[str, dict]] = field(default_factory=list)
    merge_question: bool = False
    alerts: int = 0
    scores: list[dict] = field(default_factory=list)


def passes_threshold(conn: psycopg.Connection, n: Norm, profiles: list[dict]) -> int | None:
    """Лучший балл резюме, если он не ниже watch_threshold; иначе None (S12, S13)."""
    prm = params(conn)
    best = max((scoring.score(norm_person(n), scoring.Profile.from_row(pr), prm).score for pr in profiles), default=0)
    return best if best >= int(setting(conn, "watch_threshold", 60)) else None


def process(conn: psycopg.Connection, source: dict, n: Norm, raw: dict, now: datetime) -> ProcessResult:
    """Приём одного резюме: от записи до оценок. Вызывается внутри транзакции резюме."""
    obs = upsert_observation(conn, source, n, raw, now)
    res = ProcessResult(obs.id, obs.new, obs.changed, obs.person_id)
    if not (obs.new or obs.changed or obs.was_gone):
        return res
    profiles = current_profiles(conn)
    if obs.person_id is None:
        best = passes_threshold(conn, n, profiles) if profiles else None
        if best is None:
            return res              # «увидел, но мимо»: наблюдение лежит до следующего изменения
        res.person_id = create_person(conn, n, now)
        res.person_created = True
        run(conn, "UPDATE observation SET person_id = %s WHERE id = %s", (res.person_id, obs.id))
        res.events = [("new", {"source": source["code"], "score": best})]
    else:
        keywords = {k for pr in profiles for k in pr["keywords"] or []}
        res.events = events_from_diff(obs.diff, keywords) if obs.changed else [("resume_updated", {"reappeared": True})]
        run(conn, "UPDATE person SET label = %s, city = coalesce(%s, city) WHERE id = %s",
            (label_of(n), n.city, res.person_id))
    for kind, detail in res.events:
        add_event(conn, obs.id, res.person_id, kind, detail, now)
    res.scores = rescore_person(conn, res.person_id, profiles)
    return res
