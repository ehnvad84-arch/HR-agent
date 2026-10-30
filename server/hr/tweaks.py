"""Личное обучение агента (manager-cabinet S14–S16): поправки к баллу видны и действуют только для автора.

Поправка: plus — ключевое слово плюсом, minus — слово не в счёт, stop — стоп-слово, weight — свой вес признака.
«Мой балл» — тот же код скоринга с наложенными поправками (Profile.apply_criteria), кэш ops.user_match:
считается по должностям скоупа автора и только для людей с общим баллом ≥ my_score_min_base. Общий балл
не меняется. Предложения агента ложатся state = 'proposed' и действуют только после кнопки автора (A1).
Одинаковые поправки у shared_tweak_users и больше авторов уходят в недельную сводку (S16, A2).
"""
from __future__ import annotations

import re
from datetime import datetime

import psycopg

from . import auth, pipeline, scope, scoring
from .db import ConnFactory, j, rows, run, setting, val
from .jobs import JobStats
from .scoring import FEATURE_LABELS

KINDS = ("plus", "minus", "stop", "weight")
_URL = re.compile(r"https?://|www\.", re.I)


class Invalid(ValueError):
    pass


class NotFound(LookupError):
    pass


def clean(kind: str, value: object) -> dict:
    """Белый список: вид, текст 2–60 знаков без ссылок, признак из FEATURE_LABELS, вес 0–100."""
    if kind not in KINDS or not isinstance(value, dict):
        raise Invalid("вид поправки")
    if kind == "weight":
        f, w = value.get("feature"), value.get("weight")
        if f not in FEATURE_LABELS or not isinstance(w, int) or isinstance(w, bool) or not 0 <= w <= 100:
            raise Invalid("вес: известный признак и целое от 0 до 100")
        return {"feature": f, "weight": w}
    text = re.sub(r"\s+", " ", str(value.get("text") or "")).strip()
    if not 2 <= len(text) <= 60 or _URL.search(text) or "<" in text:
        raise Invalid("слово или фраза — от 2 до 60 знаков, без ссылок")
    return {"text": text}


def key(kind: str, value: dict) -> str:
    """Одинаковые поправки разных людей — по виду и нормализованному тексту (S16)."""
    if kind == "weight":
        return f"weight:{value['feature']}:{value['weight']}"
    return f"{kind}:{value['text'].lower().replace('ё', 'е')}"


def add(conn: psycopg.Connection, user_id: int, kind: str, value: object, now: datetime, *,
        source: str = "manual", state: str = "active", feedback_id: int | None = None) -> int:
    v = clean(kind, value)
    k = key(kind, v)
    for t in rows(conn, """SELECT id, kind, value FROM ops.user_tweak
                           WHERE user_id = %s AND state <> 'off'""", (user_id,)):
        if key(t["kind"], t["value"]) == k or (kind == "weight" and t["kind"] == "weight"
                                               and t["value"]["feature"] == v["feature"]):
            run(conn, "UPDATE ops.user_tweak SET state = 'off', decided_at = %s WHERE id = %s", (now, t["id"]))
    return val(conn, """INSERT INTO ops.user_tweak (user_id, kind, value, state, source, feedback_id, created_at,
                                                    decided_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
               (user_id, kind, j(v), state, source, feedback_id, now, now if state == "active" else None))


def set_state(conn: psycopg.Connection, user_id: int, tweak_id: int, state: str, now: datetime) -> None:
    if state not in ("active", "off"):
        raise Invalid("состояние")
    if not run(conn, """UPDATE ops.user_tweak SET state = %s, decided_at = %s
                        WHERE id = %s AND user_id = %s AND state <> 'off'""", (state, now, tweak_id, user_id)):
        raise NotFound


def listing(conn: psycopg.Connection, user_id: int) -> dict:
    items = rows(conn, """SELECT id, kind, value, state, source, role_code, created_at FROM ops.user_tweak
                          WHERE user_id = %s AND state <> 'off' ORDER BY state DESC, created_at DESC""", (user_id,))
    proposed = {r["k"] for r in rows(conn, "SELECT target->'tweak'->>'key' AS k FROM digest_proposal "
                                           "WHERE target ? 'tweak'")}
    for t in items:
        t["in_digest"] = key(t["kind"], t["value"]) in proposed
    return {"items": items, "weights": scoring.default_weights(None)}


def criteria(conn: psycopg.Connection, user_id: int) -> dict:
    """Поправки автора в форме Profile.apply_criteria."""
    c: dict = {"keywords": [], "keywords_minus": [], "stop_factors": [], "weights": {}}
    for t in rows(conn, "SELECT kind, value FROM ops.user_tweak WHERE user_id = %s AND state = 'active'",
                  (user_id,)):
        v = t["value"]
        if t["kind"] == "plus":
            c["keywords"].append(v["text"])
        elif t["kind"] == "minus":
            c["keywords_minus"].append(v["text"])
        elif t["kind"] == "stop":
            c["stop_factors"].append(v["text"])
        else:
            c["weights"][v["feature"]] = v["weight"]
    return c


def _has_tweaks(c: dict) -> bool:
    return any(c[k] for k in c)


def _user(conn, user_id: int) -> auth.User | None:
    u = rows(conn, "SELECT login, full_name, role::text AS role FROM app_user WHERE id = %s AND is_active",
             (user_id,))
    if not u:
        return None
    return auth.User(user_id, u[0]["login"], u[0]["full_name"], u[0]["role"], auth.permissions(conn, u[0]["role"]), "")


def rescore_user(conn: psycopg.Connection, user_id: int, person_ids: list[int] | None = None) -> int:
    """«Мой балл» автора: по должностям его скоупа, для людей с общим баллом ≥ my_score_min_base."""
    u = _user(conn, user_id)
    c = criteria(conn, user_id)
    if person_ids is None:
        run(conn, "DELETE FROM ops.user_match WHERE user_id = %s", (user_id,))
    if not u or not _has_tweaks(c):
        if person_ids is not None:
            run(conn, "DELETE FROM ops.user_match WHERE user_id = %s AND person_id = ANY(%s)", (user_id, person_ids))
        return 0
    sc = scope.for_user(conn, u)
    floor = int(setting(conn, "my_score_min_base", 30))
    prm = pipeline.params(conn)
    n = 0
    for pr in pipeline.current_profiles(conn):
        if not sc.code_ok(pr["code"]):
            continue
        prof = scoring.Profile.from_row(pr, c)
        people = rows(conn, """SELECT person_id FROM match WHERE role_profile_id = %s AND score >= %s
                               AND (%s::bigint[] IS NULL OR person_id = ANY(%s))""",
                      (pr["id"], floor, person_ids, person_ids))
        for p in people:
            s = scoring.score(pipeline.person_data(conn, p["person_id"]), prof, prm)
            run(conn, """INSERT INTO ops.user_match (user_id, person_id, role_profile_id, score, features)
                         VALUES (%s, %s, %s, %s, %s)
                         ON CONFLICT (user_id, person_id, role_profile_id) DO UPDATE
                         SET score = EXCLUDED.score, features = EXCLUDED.features, scored_at = now()""",
                (user_id, p["person_id"], pr["id"], s.score, j(s.features)))
            n += 1
    return n


def rescore_person_all(conn: psycopg.Connection, person_id: int) -> None:
    """После пересчёта общего балла человека — «мой балл» у всех, у кого есть поправки."""
    for r in rows(conn, "SELECT DISTINCT user_id FROM ops.user_tweak WHERE state = 'active'"):
        rescore_user(conn, r["user_id"], [person_id])


def user_rescore(conn_factory: ConnFactory, *, user_id: int | None = None, **_) -> JobStats:
    """Задача user_rescore: после сохранения поправок (в фоне) или для всех авторов поправок."""
    stats = JobStats()
    with conn_factory() as conn:
        users = [user_id] if user_id else [r["user_id"] for r in rows(
            conn, "SELECT DISTINCT user_id FROM ops.user_tweak WHERE state = 'active'")]
    for uid in users:
        with conn_factory() as conn:
            stats.changed += rescore_user(conn, uid)
        stats.seen += 1
    return stats


def attach_my(conn: psycopg.Connection, user_id: int, items: list[dict]) -> None:
    """Поле my_score в сигналах и списках: по (человек, профиль); нет поправок — null."""
    pairs = [(r["person_id"], r.get("role_profile_id")) for r in items if r.get("role_profile_id")]
    got = {(r["person_id"], r["role_profile_id"]): r["score"] for r in rows(conn, """
        SELECT person_id, role_profile_id, score FROM ops.user_match
        WHERE user_id = %s AND person_id = ANY(%s)""", (user_id, [p for p, _ in pairs]))} if pairs else {}
    for r in items:
        r["my_score"] = got.get((r["person_id"], r.get("role_profile_id")))


# ------------------------------------------------------------------ предложения агента

_QUOTED = r"[«\"„]([^»\"“]{2,60})[»\"“]"
_MINUS = re.compile(r"(?:без|не нужн\w*|не надо|не важн\w*|не считай|убер\w*|минус)\D{0,30}?" + _QUOTED, re.I)
_PLUS = re.compile(r"(?:нуж\w*|важ\w*|добав\w*|учитыва\w*|плюс)\D{0,30}?" + _QUOTED, re.I)
_STOP = re.compile(r"(?:стоп|отсека\w*|исключ\w*)\D{0,30}?" + _QUOTED, re.I)


def suggest(conn: psycopg.Connection, user_id: int, feedback_id: int, body: str, now: datetime) -> list[int]:
    """Предложения по тексту замечания: слово в кавычках рядом с «без / нужен / стоп». Только proposed (A1)."""
    out = []
    for rx, kind in ((_STOP, "stop"), (_MINUS, "minus"), (_PLUS, "plus")):
        for m in rx.finditer(body):
            try:
                out.append(add(conn, user_id, kind, {"text": m.group(1)}, now, source="agent", state="proposed",
                               feedback_id=feedback_id))
            except Invalid:
                continue
        if out:
            break
    return out


def shared(conn: psycopg.Connection) -> list[dict]:
    """S16: одинаковые активные поправки у shared_tweak_users и больше авторов, ещё не вынесенные в сводку."""
    need = int(setting(conn, "shared_tweak_users", 3))
    groups: dict[str, dict] = {}
    for t in rows(conn, "SELECT user_id, kind, value, role_code FROM ops.user_tweak WHERE state = 'active'"):
        k = key(t["kind"], t["value"])
        g = groups.setdefault(k, {"key": k, "kind": t["kind"], "value": t["value"], "role_code": t["role_code"],
                                  "users": set()})
        g["users"].add(t["user_id"])
    done = {r["k"] for r in rows(conn, "SELECT target->'tweak'->>'key' AS k FROM digest_proposal "
                                      "WHERE target ? 'tweak'")}
    return [g for g in groups.values() if len(g["users"]) >= need and g["key"] not in done]
