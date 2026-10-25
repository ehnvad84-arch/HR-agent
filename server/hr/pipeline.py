"""Конвейер: наблюдение → версия и дифф → события → сшивка → балл → сигналы.

Узлы 1–5 раздела 3.2 архитектуры. Функции работают внутри переданной
транзакции, поэтому прогон по одному резюме применяется целиком или никак.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import embed, llm, scoring
from .db import agent_user_id, j, one, rows, run, setting, val
from .names import name_variants
from .normalize import Norm, label_of, strip_personal

log = logging.getLogger(__name__)

TRACKED = ("title", "city", "employer_current", "employers", "skills", "exp_years", "avg_tenure_months",
           "salary_expect", "relocation_ready", "open_for_search", "updated_at_src", "education")


# ------------------------------------------------------------------ профили

def active_profiles(conn) -> list[dict]:
    """Действующие утверждённые версии: по одной на код. Черновики в скоринге не участвуют (раздел 9 ТЗ)."""
    return rows(conn, """
        SELECT DISTINCT ON (rp.code) rp.*, d.code AS dept_code, d.title AS dept_title
        FROM role_profile rp JOIN department d ON d.id = rp.department_id
        WHERE rp.is_active AND rp.approved_at IS NOT NULL
        ORDER BY rp.code, rp.approved_at DESC""")


# ------------------------------------------------------------------ наблюдение

def _obs_fields(n: Norm) -> dict:
    return {
        "url": n.url, "title": n.title, "city": n.city, "employer_current": n.employer_current,
        "employers": n.employers, "skills": n.skills, "exp_years": n.exp_years,
        "avg_tenure_months": n.avg_tenure_months, "salary_expect": n.salary_expect,
        "relocation_ready": n.relocation_ready, "open_for_search": n.open_for_search,
        "updated_at_src": n.updated_at_src,
    }


def _diff(old: dict, new: Norm) -> dict:
    out = {}
    newd = _obs_fields(new)
    newd["education"] = new.education
    for k in TRACKED:
        a = old.get(k)
        b = newd.get(k)
        if isinstance(a, list) or isinstance(b, list):
            a_set, b_set = set(a or []), set(b or [])
            if a_set != b_set:
                out[k] = {"added": sorted(b_set - a_set), "removed": sorted(a_set - b_set)}
            continue
        if isinstance(a, datetime) and isinstance(b, datetime):
            if a.replace(tzinfo=None) != b.replace(tzinfo=None):
                out[k] = {"from": a.isoformat(), "to": b.isoformat()}
            continue
        if k == "exp_years" and a is not None and b is not None and abs(float(a) - float(b)) < 0.05:
            continue
        if k == "education" and k not in old:
            continue
        if a != b and not (a in (None, "") and b in (None, "")):
            out[k] = {"from": a if not isinstance(a, datetime) else a.isoformat(),
                      "to": b if not isinstance(b, datetime) else b.isoformat()}
    return out


def _all_keywords(conn) -> set[str]:
    kws = set()
    for p in active_profiles(conn):
        kws.update(k.lower() for k in p["keywords"] or [])
    return kws


def events_from_diff(conn, diff: dict) -> list[tuple[str, dict]]:
    ev: list[tuple[str, dict]] = []
    if "employer_current" in diff and diff["employer_current"].get("to"):
        ev.append(("employer_changed", diff["employer_current"]))
    if "salary_expect" in diff:
        ev.append(("salary_changed", diff["salary_expect"]))
    if "open_for_search" in diff and diff["open_for_search"].get("to") is True:
        ev.append(("opened_for_search", {}))
    if "skills" in diff and diff["skills"]["added"]:
        ours = _all_keywords(conn)
        hit = [s for s in diff["skills"]["added"] if s.lower() in ours or any(k in s.lower() for k in ours if len(k) > 3)]
        if hit:
            ev.append(("skill_added", {"skills": hit}))
    if "city" in diff or ("relocation_ready" in diff and diff["relocation_ready"].get("to") is True):
        ev.append(("geo_changed", {k: diff[k] for k in ("city", "relocation_ready") if k in diff}))
    if not ev and diff:
        ev.append(("resume_updated", {"fields": sorted(diff)}))
    return ev


def upsert_observation(conn, source: dict, n: Norm, raw: dict) -> dict:
    """Возвращает {"id", "new", "changed", "diff"}; при неизменном резюме обновляется только last_seen."""
    raw = strip_personal(raw)
    h = n.content_hash()
    retention_months = int(setting(conn, "retention_observation_months", 12))
    keep_until = date.today() + timedelta(days=30 * retention_months)
    old = one(conn, "SELECT * FROM observation WHERE source_id = %s AND external_id = %s",
              (source["id"], n.external_id))
    f = _obs_fields(n)
    if old is None:
        oid = val(conn, """
            INSERT INTO observation (source_id, external_id, url, title, city, employer_current, employers, skills,
                exp_years, avg_tenure_months, salary_expect, relocation_ready, open_for_search, updated_at_src,
                raw, content_hash, retention_until)
            VALUES (%(sid)s, %(ext)s, %(url)s, %(title)s, %(city)s, %(employer_current)s, %(employers)s, %(skills)s,
                %(exp_years)s, %(avg_tenure_months)s, %(salary_expect)s, %(relocation_ready)s, %(open_for_search)s,
                %(updated_at_src)s, %(raw)s, %(hash)s, %(keep)s)
            RETURNING id""", {**f, "sid": source["id"], "ext": n.external_id, "raw": j(raw), "hash": h, "keep": keep_until})
        run(conn, "INSERT INTO obs_version (observation_id, content_hash, raw) VALUES (%s, %s, %s)", (oid, h, j(raw)))
        return {"id": oid, "new": True, "changed": False, "diff": {}, "person_id": None, "was_gone": False}
    if old["content_hash"] == h:
        run(conn, "UPDATE observation SET last_seen = now(), is_gone = false, retention_until = %s WHERE id = %s",
            (keep_until, old["id"]))
        return {"id": old["id"], "new": False, "changed": False, "diff": {}, "person_id": old["person_id"],
                "was_gone": old["is_gone"]}
    old_full = dict(old)
    prev_raw = one(conn, "SELECT raw FROM obs_version WHERE observation_id = %s ORDER BY captured_at DESC LIMIT 1", (old["id"],))
    old_full["education"] = None
    if prev_raw:
        from .normalize import normalize as _norm
        try:
            code = val(conn, "SELECT code FROM source WHERE id = %s", (source["id"],))
            old_full["education"] = _norm(code, {**prev_raw["raw"], "id": n.external_id}).education
        except Exception:  # старая версия могла прийти в другом формате — сравниваем без образования
            pass
    diff = _diff(old_full, n)
    run(conn, """
        UPDATE observation SET url = %(url)s, title = %(title)s, city = %(city)s, employer_current = %(employer_current)s,
            employers = %(employers)s, skills = %(skills)s, exp_years = %(exp_years)s,
            avg_tenure_months = %(avg_tenure_months)s, salary_expect = %(salary_expect)s,
            relocation_ready = %(relocation_ready)s, open_for_search = %(open_for_search)s,
            updated_at_src = %(updated_at_src)s, raw = %(raw)s, content_hash = %(hash)s,
            last_seen = now(), is_gone = false, retention_until = %(keep)s
        WHERE id = %(id)s""", {**f, "raw": j(raw), "hash": h, "keep": keep_until, "id": old["id"]})
    run(conn, "INSERT INTO obs_version (observation_id, content_hash, raw, diff) VALUES (%s, %s, %s, %s)",
        (old["id"], h, j(raw), j(diff)))
    return {"id": old["id"], "new": False, "changed": bool(diff), "diff": diff, "person_id": old["person_id"],
            "was_gone": old["is_gone"]}


# ------------------------------------------------------------------ человек

def person_data(conn, person_id: int) -> scoring.Person:
    """Сводит все наблюдения человека: свежее резюме главное, навыки и места работы объединяются."""
    obs = rows(conn, """SELECT * FROM observation WHERE person_id = %s AND NOT is_gone
                        ORDER BY coalesce(updated_at_src, last_seen) DESC""", (person_id,))
    if not obs:
        obs = rows(conn, "SELECT * FROM observation WHERE person_id = %s ORDER BY last_seen DESC", (person_id,))
    p = scoring.Person()
    if not obs:
        return p
    main = obs[0]
    p.title, p.city = main["title"], main["city"]
    p.salary_expect, p.relocation_ready = main["salary_expect"], main["relocation_ready"]
    p.exp_years = max((float(o["exp_years"]) for o in obs if o["exp_years"] is not None), default=None)
    p.avg_tenure_months = main["avg_tenure_months"]
    seen_e, seen_s = [], []
    for o in obs:
        seen_e += [e for e in o["employers"] if e not in seen_e]
        seen_s += [s for s in o["skills"] if s.lower() not in {x.lower() for x in seen_s}]
        p.positions += [o["title"]] if o["title"] and o["title"] not in p.positions else []
    p.employers, p.skills = seen_e, seen_s
    texts = []
    for o in obs:
        r = o["raw"] or {}
        for k in ("skills", "about", "description", "experience"):
            v = r.get(k)
            if isinstance(v, str):
                texts.append(v)
            elif isinstance(v, list):
                texts += [str(x.get("description") or x.get("position") or "") if isinstance(x, dict) else str(x) for x in v]
    p.text = " ".join(texts)[:20000]
    gh = one(conn, """SELECT confidence FROM social_profile WHERE person_id = %s AND kind IN ('github', 'gitlab')
                      AND confidence <> 'rejected' ORDER BY confidence LIMIT 1""", (person_id,))
    if gh:
        p.open_code = 1.0 if gh["confidence"] == "confirmed" else 0.5
    return p


def norm_person(n: Norm) -> scoring.Person:
    return scoring.Person(title=n.title, city=n.city, employers=n.employers, positions=n.positions,
                          skills=n.skills, exp_years=n.exp_years, avg_tenure_months=n.avg_tenure_months,
                          salary_expect=n.salary_expect, relocation_ready=n.relocation_ready,
                          text=" ".join(filter(None, [n.about, n.education])))


def stitch_candidates(conn, n: Norm, exclude_obs: int) -> list[tuple[int, float, dict]]:
    """Похожие люди с других площадок: работодатели, должность, город, ник."""
    cands = rows(conn, """
        SELECT DISTINCT p.id, p.nicknames, o.title, o.city, o.employers, o.exp_years
        FROM person p JOIN observation o ON o.person_id = p.id
        WHERE o.id <> %s AND p.watch <> 'dropped'
          AND (o.employers && %s::text[] OR similarity(p.label, %s) > 0.45
               OR (cardinality(%s::text[]) > 0 AND p.nicknames && %s::text[]))
        LIMIT 50""", (exclude_obs, n.employers or ["-"], label_of(n), n.nicknames, n.nicknames))
    out = []
    for c in cands:
        ev: dict[str, Any] = {}
        if n.nicknames and set(map(str.lower, n.nicknames)) & set(map(str.lower, c["nicknames"] or [])):
            out.append((c["id"], 1.0, {"nick": True}))
            continue
        ea, eb = {e.lower() for e in n.employers}, {e.lower() for e in c["employers"] or []}
        emp = len(ea & eb) / len(ea | eb) if ea and eb else 0.0
        title = scoring.trigram_sim(n.title, c["title"])
        city = 1.0 if n.city and c["city"] and n.city.lower() == c["city"].lower() else 0.0
        exp = 1.0 if (n.exp_years is not None and c["exp_years"] is not None
                      and abs(float(n.exp_years) - float(c["exp_years"])) <= 1) else 0.0
        sim = round(0.5 * emp + 0.25 * title + 0.15 * city + 0.1 * exp, 3)
        ev = {"employers": sorted(ea & eb), "title_sim": round(title, 2), "city": bool(city), "exp_close": bool(exp)}
        out.append((c["id"], sim, ev))
    return sorted(out, key=lambda x: -x[1])


def create_person(conn, n: Norm) -> int:
    variants = name_variants(n.name) if n.name else []
    return val(conn, """INSERT INTO person (label, city, name_variants, nicknames, last_event_at)
                        VALUES (%s, %s, %s, %s, now()) RETURNING id""",
               (label_of(n), n.city, variants, [x.lower() for x in n.nicknames]))


def add_event(conn, obs_id: int, person_id: int | None, kind: str, detail: dict) -> int:
    eid = val(conn, "INSERT INTO obs_event (observation_id, person_id, kind, detail) VALUES (%s, %s, %s, %s) RETURNING id",
              (obs_id, person_id, kind, j(detail)))
    if person_id:
        run(conn, "UPDATE person SET last_event_at = now() WHERE id = %s", (person_id,))
    return eid


# ------------------------------------------------------------------ оценка

def explain(conn, profile_row: dict, person: scoring.Person, s: scoring.Score, use_llm: bool) -> tuple[str, str]:
    """Возвращает (текст разбора, имя модели). Разбор хранится в match.explanation."""
    if use_llm:
        try:
            out = llm.ask(conn, "explain_score", {
                "profile": {"title": profile_row["title"], "must_have": profile_row["must_have"],
                            "keywords": profile_row["keywords"], "salary_max": profile_row["salary_max"]},
                "resume": {"title": person.title, "city": person.city, "exp_years": person.exp_years,
                           "employers": person.employers[:6], "skills": person.skills[:40],
                           "salary_expect": person.salary_expect},
                "score": s.score, "features": s.features}, max_tokens=700)
            text = "\n".join(filter(None, [out.get("why"), out.get("gap"),
                                           "Спросить: " + "; ".join(out.get("questions") or [])]))
            if text.strip():
                return text, llm.model_name()
        except llm.LLMUnavailable as e:
            log.warning("объяснение по шаблону: %s", e)
    t = scoring.template_explanation(person, scoring.Profile.from_row(profile_row), s)
    return "\n".join([t["why"], t["gap"], "Спросить: " + "; ".join(t["questions"])]), "template"


def rescore_person(conn, person_id: int, profiles: list[dict] | None = None, explain_from: int | None = None) -> list[dict]:
    """Пересчитывает match по всем действующим профилям. Модель объясняет только тех, кто выше порога."""
    profiles = profiles if profiles is not None else active_profiles(conn)
    if not profiles:
        return []
    person = person_data(conn, person_id)
    threshold = explain_from if explain_from is not None else int(setting(conn, "watch_threshold", 60))
    out = []
    for pr in profiles:
        s = scoring.score(person, scoring.Profile.from_row(pr))
        prev = one(conn, "SELECT score, profile_version, explanation FROM match WHERE person_id = %s AND role_profile_id = %s",
                   (person_id, pr["id"]))
        same = prev and prev["score"] == s.score and prev["profile_version"] == pr["version"] and prev["explanation"]
        if same:
            text, model = prev["explanation"], None
        else:
            text, model = explain(conn, pr, person, s, use_llm=s.score >= threshold)
        run(conn, """
            INSERT INTO match (person_id, role_profile_id, profile_version, score, features, explanation, model, scored_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (person_id, role_profile_id) DO UPDATE SET profile_version = EXCLUDED.profile_version,
                score = EXCLUDED.score, features = EXCLUDED.features, explanation = EXCLUDED.explanation,
                model = coalesce(EXCLUDED.model, match.model), scored_at = now()""",
            (person_id, pr["id"], pr["version"], s.score, j(s.features), text, model))
        out.append({"profile": pr, "score": s.score, "prev": prev["score"] if prev else None, "s": s})
    update_embedding(conn, person_id, person)
    return out


def update_embedding(conn, person_id: int, person: scoring.Person) -> None:
    text = " ".join(filter(None, [person.title, person.city, " ".join(person.positions), " ".join(person.skills),
                                  " ".join(person.employers), person.text[:4000]]))
    try:
        vec = embed.embed(text)
    except Exception as e:  # поиск по смыслу не должен ронять приём резюме
        log.warning("эмбеддинг не посчитан для %s: %s", person_id, e)
        return
    run(conn, """INSERT INTO person_embedding (person_id, embedding, updated_at) VALUES (%s, %s::vector, now())
                 ON CONFLICT (person_id) DO UPDATE SET embedding = EXCLUDED.embedding, updated_at = now()""",
        (person_id, embed.literal(vec)))


def best_match(conn, person_id: int) -> dict | None:
    return one(conn, """
        SELECT m.*, rp.title AS role_title, rp.code AS role_code, d.code AS dept_code
        FROM match m JOIN role_profile rp ON rp.id = m.role_profile_id JOIN department d ON d.id = rp.department_id
        WHERE m.person_id = %s AND rp.is_active AND rp.approved_at IS NOT NULL
        ORDER BY m.score DESC LIMIT 1""", (person_id,))


# ------------------------------------------------------------------ сигналы

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

DONOR_DEPTS = {"sales", "rnd_hw", "rnd_sw"}


def raise_alert(conn, person_id: int | None, role_profile_id: int | None, reason: str, headline: str,
                payload: dict, vacancy_id: int | None = None) -> int:
    """Одна открытая карточка на человека и профиль: повторный сигнал обновляет её, а не плодит дубли."""
    existing = one(conn, """SELECT id FROM alert WHERE state = 'new' AND person_id IS NOT DISTINCT FROM %s
                            AND role_profile_id IS NOT DISTINCT FROM %s AND vacancy_id IS NOT DISTINCT FROM %s""",
                   (person_id, role_profile_id, vacancy_id))
    if existing:
        run(conn, "UPDATE alert SET reason = %s, headline = %s, payload = %s, created_at = now() WHERE id = %s",
            (reason, headline, j(payload), existing["id"]))
        return existing["id"]
    return val(conn, """INSERT INTO alert (person_id, role_profile_id, vacancy_id, reason, headline, payload)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
               (person_id, role_profile_id, vacancy_id, reason, headline, j(payload)))


def evaluate_alerts(conn, person_id: int, events: list[tuple[str, dict]], scores: list[dict]) -> list[int]:
    """Формула «обратите внимание», раздел 3.3 архитектуры. Пороги — из settings."""
    p = one(conn, "SELECT * FROM person WHERE id = %s", (person_id,))
    if not p or p["candidate_id"]:
        return []
    if p["postponed_until"] and p["postponed_until"] > date.today() and p["watch"] != "watched":
        return []
    th_new = int(setting(conn, "alert_score_new", 75))
    th_pool = int(setting(conn, "alert_score_pool_event", 65))
    grew_by = int(setting(conn, "alert_score_grew_by", 10))
    grew_after = int(setting(conn, "score_grew_after_months", 6))
    watch_th = int(setting(conn, "watch_threshold", 60))
    if not scores:
        return []
    top = max(scores, key=lambda x: x["score"])
    pr, sc = top["profile"], top["score"]
    kinds = [k for k, _ in events]
    what = "; ".join(EVENT_TEXT[k].lower() for k in kinds if k != "new") or "резюме изменилось"
    created: list[int] = []

    if p["watch"] == "dropped":
        last = one(conn, """SELECT score_at, role_profile_id, created_at FROM decision_log
                            WHERE person_id = %s AND kind = 'reject' ORDER BY created_at DESC LIMIT 1""", (person_id,))
        if last and last["score_at"] is not None and last["created_at"] < datetime.now(timezone.utc) - timedelta(days=30 * grew_after):
            same = next((x for x in scores if x["profile"]["id"] == last["role_profile_id"]), top)
            if same["score"] - last["score_at"] >= grew_by:
                created.append(raise_alert(conn, person_id, same["profile"]["id"], "score_grew",
                    f"Был отказ, балл вырос с {last['score_at']} до {same['score']}: {what}",
                    {"score": same["score"], "was": last["score_at"], "events": kinds}))
        return created

    if "new" in kinds and sc >= th_new:
        created.append(raise_alert(conn, person_id, pr["id"], "new_high_score",
            f"Новый в поле зрения, {sc} по профилю «{pr['title']}»", {"score": sc, "events": kinds}))
    elif "new" not in kinds and events and (sc >= th_pool or p["watch"] == "watched"):
        created.append(raise_alert(conn, person_id, pr["id"], "event_on_pool",
            f"{what[:1].upper()}{what[1:]}", {"score": sc, "events": kinds, "watched": p["watch"] == "watched"}))
    if not created and events and sc >= watch_th and pr.get("dept_code") in DONOR_DEPTS:
        donor = top["s"].features.get("donor_company")
        if donor and donor["points"] == donor["max"] and donor["max"] > 0:
            created.append(raise_alert(conn, person_id, pr["id"], "donor_company",
                f"Опыт в компании-доноре: {donor['detail']}", {"score": sc, "events": kinds}))
    return created


# ------------------------------------------------------------------ один шаг конвейера

def process(conn, source: dict, n: Norm, raw: dict) -> dict:
    """Приём одного резюме целиком: от записи до сигнала."""
    res = upsert_observation(conn, source, n, raw)
    obs_id, person_id = res["id"], res["person_id"]
    stats = {"observation": obs_id, "new_obs": res["new"], "changed": res["changed"], "person": person_id,
             "person_created": False, "alerts": 0, "merge_question": False}
    if not res["new"] and not res["changed"] and not res["was_gone"]:
        return stats
    events = events_from_diff(conn, res["diff"]) if res["changed"] else []
    if res["was_gone"] and not res["changed"]:
        events = [("resume_updated", {"reappeared": True})]

    if person_id is None:
        profiles = active_profiles(conn)
        if not profiles:
            return stats
        quick = scoring.score
        best = max((quick(norm_person(n), scoring.Profile.from_row(pr)).score for pr in profiles), default=0)
        if best < int(setting(conn, "watch_threshold", 60)):
            return stats            # «увидел, но мимо»: карточка не заводится, наблюдение лежит до следующего изменения
        auto = float(setting(conn, "merge_auto_similarity", 0.92))
        ask = float(setting(conn, "merge_ask_similarity", 0.6))
        cands = stitch_candidates(conn, n, obs_id)
        if cands and cands[0][1] >= auto:
            person_id = cands[0][0]
            run(conn, "UPDATE observation SET person_id = %s WHERE id = %s", (person_id, obs_id))
            if n.name:
                run(conn, "UPDATE person SET name_variants = (SELECT array_agg(DISTINCT v) FROM unnest(name_variants || %s::text[]) v) WHERE id = %s",
                    (name_variants(n.name), person_id))
            events = [("resume_updated", {"new_source": source["code"]})]
        else:
            person_id = create_person(conn, n)
            run(conn, "UPDATE observation SET person_id = %s WHERE id = %s", (person_id, obs_id))
            stats["person_created"] = True
            events = [("new", {"source": source["code"], "score": best})]
            if cands and cands[0][1] >= ask:
                other, sim, ev = cands[0]
                a, b = sorted((person_id, other))
                if not one(conn, "SELECT 1 FROM person_merge_candidate WHERE person_a = %s AND person_b = %s", (a, b)):
                    mid = val(conn, """INSERT INTO person_merge_candidate (person_a, person_b, similarity, evidence)
                                       VALUES (%s, %s, %s, %s) RETURNING id""", (a, b, sim, j(ev)))
                    run(conn, """INSERT INTO action_queue (kind, person_id, payload) VALUES ('merge_persons', %s, %s)""",
                        (person_id, j({"merge_candidate_id": mid, "person_a": a, "person_b": b, "similarity": sim,
                                       "evidence": ev})))
                    stats["merge_question"] = True
        stats["person"] = person_id

    for kind, detail in events:
        add_event(conn, obs_id, person_id, kind, detail)
    if n.nicknames:
        run(conn, """UPDATE person SET nicknames = (SELECT array_agg(DISTINCT v) FROM unnest(nicknames || %s::text[]) v)
                     WHERE id = %s""", ([x.lower() for x in n.nicknames], person_id))
    run(conn, "UPDATE person SET label = %s, city = coalesce(%s, city) WHERE id = %s AND candidate_id IS NULL",
        (label_of(n), n.city, person_id))
    scores = rescore_person(conn, person_id)
    stats["alerts"] = len(evaluate_alerts(conn, person_id, events, scores))
    run(conn, "UPDATE obs_event SET processed = true WHERE observation_id = %s AND NOT processed", (obs_id,))
    return stats


def mark_gone(conn, days: int | None = None) -> int:
    """Резюме, которое площадка перестала показывать: событие disappeared, наблюдение остаётся."""
    days = days or int(setting(conn, "stale_observation_days", 45))
    gone = rows(conn, """UPDATE observation o SET is_gone = true
                         FROM source s WHERE s.id = o.source_id AND s.mode = 'api' AND NOT o.is_gone
                           AND o.last_seen < now() - (%s || ' days')::interval
                         RETURNING o.id, o.person_id""", (str(days),))
    for g in gone:
        if g["person_id"]:
            add_event(conn, g["id"], g["person_id"], "disappeared", {})
    return len(gone)


def rescore_profile(conn, role_profile_id: int) -> int:
    """После утверждения версии профиля — переоценка всей базы по ней."""
    pr = one(conn, """SELECT rp.*, d.code AS dept_code, d.title AS dept_title FROM role_profile rp
                      JOIN department d ON d.id = rp.department_id WHERE rp.id = %s""", (role_profile_id,))
    people = rows(conn, "SELECT id FROM person WHERE watch <> 'dropped' OR candidate_id IS NOT NULL")
    for p in people:
        rescore_person(conn, p["id"], [pr])
    return len(people)


def agent_id(conn) -> int:
    return agent_user_id(conn)
