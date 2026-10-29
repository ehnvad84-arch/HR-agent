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

from . import alerts, embed, scoring
from .db import ConnFactory, j, one, rows, run, setting, val
from .jobs import JobStats
from .names import name_variants
from .normalize import Norm, label_of, normalize

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


def person_text(p: scoring.Person) -> str:
    return " ".join(filter(None, [p.title, p.city, " ".join(p.positions), " ".join(p.skills),
                                  " ".join(p.employers), p.text[:4000]]))


def write_embedding(conn: psycopg.Connection, person_id: int, vec: list[float], model: str, now: datetime) -> None:
    run(conn, """INSERT INTO person_embedding (person_id, embedding, embed_model, updated_at)
                 VALUES (%s, %s::vector, %s, %s)
                 ON CONFLICT (person_id) DO UPDATE SET embedding = EXCLUDED.embedding,
                     embed_model = EXCLUDED.embed_model, updated_at = EXCLUDED.updated_at""",
        (person_id, embed.literal(vec), model, now))


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


# ------------------------------------------------------------------ сшивка

def stitch_candidates(conn: psycopg.Connection, n: Norm, exclude_obs: int) -> list[tuple[int, float, dict]]:
    """Похожие люди с других наблюдений: ник — сразу 1,0; иначе взвешенное сходство (S14, S15)."""
    nicks = [x.lower() for x in n.nicknames]
    cands = rows(conn, """
        SELECT p.id, p.nicknames, o.title, o.city, o.employers, o.exp_years
        FROM person p JOIN observation o ON o.person_id = p.id
        WHERE o.id <> %(obs)s AND p.watch <> 'dropped'
          AND (o.employers && %(emp)s::text[] OR similarity(p.label, %(label)s) > 0.45
               OR p.nicknames && %(nicks)s::text[])
        LIMIT 100""", {"obs": exclude_obs, "emp": n.employers, "label": label_of(n), "nicks": nicks})
    best: dict[int, tuple[float, dict]] = {}
    for c in cands:
        if nicks and set(nicks) & set(c["nicknames"]):
            sim, ev = 1.0, {"nick": sorted(set(nicks) & set(c["nicknames"]))}
        else:
            ea, eb = {e.lower() for e in n.employers}, {e.lower() for e in c["employers"]}
            emp = len(ea & eb) / len(ea | eb) if ea and eb else 0.0
            title = scoring.trigram_sim(n.title, c["title"])
            city = 1.0 if n.city and c["city"] and n.city.lower() == c["city"].lower() else 0.0
            exp = 1.0 if (n.exp_years is not None and c["exp_years"] is not None
                          and abs(n.exp_years - float(c["exp_years"])) <= 1) else 0.0
            sim = round(0.5 * emp + 0.25 * title + 0.15 * city + 0.1 * exp, 3)
            ev = {"employers": sorted(ea & eb), "title_sim": round(title, 2), "city": bool(city),
                  "exp_close": bool(exp)}
        if sim > best.get(c["id"], (-1.0, {}))[0]:
            best[c["id"]] = (sim, ev)
    return sorted(((pid, s, e) for pid, (s, e) in best.items()), key=lambda x: -x[1])


def attach(conn: psycopg.Connection, person_id: int, obs_id: int, n: Norm) -> None:
    run(conn, "SELECT id FROM person WHERE id = %s FOR UPDATE", (person_id,))
    run(conn, "UPDATE observation SET person_id = %s WHERE id = %s", (person_id, obs_id))
    run(conn, """UPDATE person SET
                   name_variants = ARRAY(SELECT DISTINCT unnest(name_variants || %s::text[])),
                   nicknames = ARRAY(SELECT DISTINCT unnest(nicknames || %s::text[]))
                 WHERE id = %s""", (name_variants(n.name), [x.lower() for x in n.nicknames], person_id))


def ask_merge(conn: psycopg.Connection, new_id: int, other: int, sim: float, ev: dict) -> None:
    """Спорная сшивка не склеивается молча: вопрос рекрутеру в очередь действий."""
    a, b = sorted((new_id, other))
    mid = val(conn, """INSERT INTO person_merge_candidate (person_a, person_b, similarity, evidence)
                       VALUES (%s, %s, %s, %s) RETURNING id""", (a, b, sim, j(ev)))
    run(conn, "INSERT INTO action_queue (kind, person_id, payload) VALUES ('merge_persons', %s, %s)",
        (new_id, j({"merge_candidate_id": mid, "person_a": a, "person_b": b, "similarity": sim, "evidence": ev})))


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


def passes_threshold(conn: psycopg.Connection, n: Norm, profiles: list[dict], partial: bool = False) -> int | None:
    """Лучший балл резюме, если он не ниже watch_threshold; иначе None (S12, S13).
    partial — выдача поиска без навыков: решаем, запрашивать ли полное резюме."""
    prm = params(conn)
    best = max((scoring.score(norm_person(n), scoring.Profile.from_row(pr), prm, partial=partial).score
                for pr in profiles), default=0)
    return best if best >= int(setting(conn, "watch_threshold", 60)) else None


def card_for_observation(conn: psycopg.Connection, res: ProcessResult, source_code: str, n: Norm,
                         now: datetime, profiles: list[dict]) -> bool:
    """Наблюдение без карточки: порог → сшивка → своя карточка или привязка. False — ниже порога."""
    best = passes_threshold(conn, n, profiles) if profiles else None
    if best is None:
        return False
    cands = stitch_candidates(conn, n, res.observation_id)
    auto = float(setting(conn, "merge_auto_similarity", 0.92))
    ask = float(setting(conn, "merge_ask_similarity", 0.6))
    if cands and cands[0][1] >= auto:
        res.person_id = cands[0][0]
        attach(conn, res.person_id, res.observation_id, n)
        res.events = [("resume_updated", {"new_source": source_code, "similarity": cands[0][1]})]
        return True
    res.person_id = create_person(conn, n, now)
    res.person_created = True
    run(conn, "UPDATE observation SET person_id = %s WHERE id = %s", (res.person_id, res.observation_id))
    res.events = [("new", {"source": source_code, "score": best})]
    if cands and cands[0][1] >= ask:
        ask_merge(conn, res.person_id, *cands[0])
        res.merge_question = True
    return True


def _finish(conn: psycopg.Connection, res: ProcessResult, now: datetime, profiles: list[dict]) -> ProcessResult:
    for kind, detail in res.events:
        add_event(conn, res.observation_id, res.person_id, kind, detail, now)
    res.scores = rescore_person(conn, res.person_id, profiles)
    if embed.method() == embed.HASH:
        # вектор в коде считается сразу; у провайдера — задачей score, вне транзакции
        write_embedding(conn, res.person_id, embed.hash_vector(person_text(person_data(conn, res.person_id))),
                        embed.HASH, now)
    res.alerts = alerts.evaluate(conn, res.person_id, res.events, res.scores, now)
    run(conn, "UPDATE obs_event SET processed = true WHERE observation_id = %s AND NOT processed",
        (res.observation_id,))
    return res


def process(conn: psycopg.Connection, source: dict, n: Norm, raw: dict, now: datetime) -> ProcessResult:
    """Приём одного резюме: от записи до оценок. Вызывается внутри транзакции резюме."""
    obs = upsert_observation(conn, source, n, raw, now)
    res = ProcessResult(obs.id, obs.new, obs.changed, obs.person_id)
    if not (obs.new or obs.changed or obs.was_gone):
        return res
    profiles = current_profiles(conn)
    if obs.person_id is None:
        if not card_for_observation(conn, res, source["code"], n, now, profiles):
            return res              # «увидел, но мимо»: наблюдение лежит до следующего изменения
    else:
        keywords = {k for pr in profiles for k in pr["keywords"] or []}
        res.events = events_from_diff(obs.diff, keywords) if obs.changed else [("resume_updated", {"reappeared": True})]
        run(conn, "UPDATE person SET label = %s, city = coalesce(%s, city) WHERE id = %s",
            (label_of(n), n.city, res.person_id))
    return _finish(conn, res, now, profiles)


# ------------------------------------------------------------------ переоценка по новой версии

class ProfileNotApproved(ValueError):
    pass


BATCH = 200


def rescore_profile(conn_factory: ConnFactory, role_profile_id: int, now: datetime | None = None) -> JobStats:
    """Переоценка по утверждённой версии: все люди, включая «не наш» и кандидатов, и наблюдения
    без карточки в пределах срока хранения (S20). Пачками, каждая в своей транзакции."""
    now = now or datetime.now(UTC)
    stats = JobStats()
    with conn_factory() as conn:
        pr = one(conn, """SELECT rp.*, d.code AS dept_code FROM role_profile rp
                          JOIN department d ON d.id = rp.department_id WHERE rp.id = %s""", (role_profile_id,))
        if not pr or pr["approved_by"] is None or pr["approved_at"] is None:
            raise ProfileNotApproved(f"версия профиля {role_profile_id} не утверждена")
    last = 0
    while True:
        with conn_factory() as conn:
            ids = [r["id"] for r in rows(conn, "SELECT id FROM person WHERE id > %s ORDER BY id LIMIT %s",
                                         (last, BATCH))]
            for pid in ids:
                rescore_person(conn, pid, [pr])
        if not ids:
            break
        stats.seen += len(ids)
        last = ids[-1]
    last = 0
    while True:
        with conn_factory() as conn:
            obs = rows(conn, """SELECT o.id, o.raw, s.code FROM observation o JOIN source s ON s.id = o.source_id
                                WHERE o.person_id IS NULL AND o.id > %s AND NOT o.is_gone
                                  AND (o.retention_until IS NULL OR o.retention_until >= %s::date)
                                ORDER BY o.id LIMIT %s""", (last, now, BATCH))
            profiles = current_profiles(conn)
            for o in obs:
                res = ProcessResult(o["id"], False, False)
                if card_for_observation(conn, res, o["code"], normalize(o["code"], o["raw"]), now, profiles):
                    _finish(conn, res, now, profiles)
                    stats.persons += int(res.person_created)
        if not obs:
            break
        stats.skipped += len(obs)
        last = obs[-1]["id"]
    return stats
