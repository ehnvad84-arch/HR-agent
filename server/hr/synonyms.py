"""Синонимы ключей профиля (S1–S4): модель предлагает, рекрутер правит и сохраняет.

save и rollback действуют сразу, без новой версии профиля (таблица role_synonym по коду должности),
каждое изменение — agent_change kind 'synonyms' с полными снимками до и после. Откат — только последней
записи должности. Утверждение версии без ключа удаляет его синонимы (prune). Пересчёт — rescore, в фоне.

suggest ничего не пишет, кроме кэша ответа: вход модели — только название и ключи действующей
версии профиля (данных людей нет), ответ проходит белый список (clean_term, известные ключи,
не больше 8 на ключ). Повтор с теми же ключами берётся из ops.llm_cache — модель и предел не тратятся.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx
import psycopg

from . import llm, scoring
from .assist import cache_key
from .db import ConnFactory, j, one, rows, run, val

log = logging.getLogger(__name__)

MAX_SUGGESTED = 8
MAX_SAVED = 10
_URL = re.compile(r"https?://|www\.", re.I)
_BANNED = set('<>"()!')


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    """422 validation: причина — в тексте."""


class Stale(RuntimeError):
    """409 stale: после открытия экрана синонимы должности кто-то сохранил."""


class NotLatest(RuntimeError):
    """409 not_latest: откатывать можно только последнюю запись о синонимах должности."""


class NoKeywords(ValueError):
    """У действующей версии профиля нет ключей — предлагать не к чему (422 no_keywords)."""


def norm(keyword: str) -> str:
    """Ключ в role_synonym: нижний регистр, «ё» → «е», пробелы схлопнуты (как в балле)."""
    return scoring.norm_keyword(keyword)


def clean_term(term: object) -> str | None:
    """Синоним по правилам сохранения: 2–60 знаков, без ссылок и < > " ( ) !; иначе None."""
    if not isinstance(term, str):
        return None
    t = re.sub(r"\s+", " ", term).strip()
    if not 2 <= len(t) <= 60 or _URL.search(t) or _BANNED & set(t):
        return None
    return t


def current(conn, profile_id: int) -> dict:
    """Действующая (последняя утверждённая) версия профиля того же кода."""
    p = one(conn, """SELECT x.id, x.code, x.title, x.keywords FROM role_profile rp
                     JOIN role_profile x ON x.code = rp.code AND x.is_active AND x.approved_at IS NOT NULL
                     WHERE rp.id = %s ORDER BY x.approved_at DESC, x.id DESC LIMIT 1""", (profile_id,))
    if p:
        return p
    if not one(conn, "SELECT 1 FROM role_profile WHERE id = %s", (profile_id,)):
        raise NotFound
    raise NoKeywords


def _validate(reply: object, keywords: list[str]) -> dict[str, list[str]]:
    """Белый список ответа: только ключи профиля, термины по clean_term, без ключей и повторов, ≤ 8."""
    if not isinstance(reply, dict) or not isinstance(reply.get("synonyms"), dict):
        return {}
    by_norm = {norm(k): k for k in keywords}
    out: dict[str, list[str]] = {}
    for k, terms in reply["synonyms"].items():
        key = by_norm.get(norm(k)) if isinstance(k, str) else None
        if key is None or not isinstance(terms, list):
            continue
        seen = set(by_norm) | {norm(t) for t in out.get(key, [])}
        got = out.setdefault(key, [])
        for t in terms:
            t = clean_term(t)
            if t and norm(t) not in seen and len(got) < MAX_SUGGESTED:
                seen.add(norm(t))
                got.append(t)
    return {k: v for k, v in out.items() if v}


def suggest(conn_factory: ConnFactory, profile_id: int, *, http: httpx.Client | None = None) -> dict[str, list[str]]:
    """{ключ: [термины]}, может быть пусто. LLMUnavailable / LLMDailyLimit — наверх (S4)."""
    system, version = llm.prompt("suggest_synonyms")
    with conn_factory() as conn:
        p = current(conn, profile_id)
        keywords = [k for k in (p["keywords"] or []) if k and k.strip()]
        if not keywords:
            raise NoKeywords
        data = {"title": p["title"], "keywords": keywords}
        key = cache_key(llm.SYNONYMS, version, data)
        hit = one(conn, "SELECT reply FROM ops.llm_cache WHERE key = %s", (key,))
    if hit:
        return hit["reply"]["suggestions"]
    reply = llm.guarded_complete(conn_factory, llm.SYNONYMS,
                                 [{"role": "system", "content": system},
                                  {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                                 http=http, max_tokens=1500)
    got = _validate(reply, keywords)
    if got:   # пустой ответ не кэшируем: повтор по кнопке может дать результат
        with conn_factory() as conn:
            run(conn, """INSERT INTO ops.llm_cache (key, purpose, reply) VALUES (%s, %s, %s)
                         ON CONFLICT (key) DO NOTHING""", (key, llm.SYNONYMS, j({"suggestions": got})))
    else:
        log.info("модель не предложила синонимов, прошедших проверку")
    return got


def load(conn: psycopg.Connection, role_code: str) -> dict[str, list[str]]:
    """Синонимы должности {нормализованный ключ: [термины]}, без пустых."""
    return {r["keyword"]: list(r["synonyms"]) for r in rows(
        conn, """SELECT keyword, synonyms FROM role_synonym WHERE role_code = %s AND cardinality(synonyms) > 0
                 ORDER BY keyword""", (role_code,))}


def last_change_id(conn: psycopg.Connection, role_code: str) -> int | None:
    return val(conn, """SELECT id FROM agent_change WHERE kind = 'synonyms' AND after->>'role_code' = %s
                        ORDER BY id DESC LIMIT 1""", (role_code,))


def _diff(before: dict, after: dict) -> dict[str, dict[str, list[str]]]:
    out = {}
    for kw in sorted(before.keys() | after.keys()):
        b, a = before.get(kw) or [], after.get(kw) or []
        plus, minus = [t for t in a if t not in b], [t for t in b if t not in a]
        if plus or minus:
            out[kw] = {"+": plus, "-": minus}
    return out


def view(conn: psycopg.Connection, profile_id: int, can_edit: bool) -> dict:
    """Карточка «Синонимы ключей»: ключи действующей версии с синонимами и история правок должности."""
    code = val(conn, "SELECT code FROM role_profile WHERE id = %s", (profile_id,))
    if code is None:
        raise NotFound
    try:
        keywords = [k for k in (current(conn, profile_id)["keywords"] or []) if k and k.strip()]
    except NoKeywords:
        keywords = []
    have = load(conn, code)
    hist = rows(conn, """SELECT c.id, c.applied_at, c.before, c.after, u.full_name AS by
                         FROM agent_change c LEFT JOIN app_user u ON u.id = c.applied_by
                         WHERE c.kind = 'synonyms' AND c.after->>'role_code' = %s
                         ORDER BY c.id DESC""", (code,))
    return {"role_code": code, "can_edit": can_edit,
            "items": [{"keyword": k, "synonyms": have.get(norm(k), [])} for k in keywords],
            "history": [{"id": h["id"], "applied_at": h["applied_at"].isoformat(), "by": h["by"],
                         "diff": _diff(h["before"].get("synonyms") or {}, h["after"].get("synonyms") or {}),
                         "rollback_of": h["after"].get("rollback_of"), "can_rollback": can_edit and i == 0}
                        for i, h in enumerate(hist)]}


def _lock(conn: psycopg.Connection, role_code: str) -> None:
    """Правки синонимов одной должности — по очереди: проверка stale и запись в одной блокировке."""
    run(conn, "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"synonyms:{role_code}",))


def _write(conn: psycopg.Connection, role_code: str, before: dict[str, list[str]], after: dict[str, list[str]],
           user_id: int | None, now: datetime, *, profile_id: int, rollback_of: int | None, note: str) -> int:
    for kw in before.keys() - after.keys():
        run(conn, "DELETE FROM role_synonym WHERE role_code = %s AND keyword = %s", (role_code, kw))
    for kw, terms in after.items():
        if before.get(kw) != terms:
            run(conn, """INSERT INTO role_synonym (role_code, keyword, synonyms, updated_by, updated_at)
                         VALUES (%s, %s, %s, %s, %s)
                         ON CONFLICT (role_code, keyword) DO UPDATE
                         SET synonyms = EXCLUDED.synonyms, updated_by = EXCLUDED.updated_by,
                             updated_at = EXCLUDED.updated_at""", (role_code, kw, terms, user_id, now))
    return val(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                        VALUES ('synonyms', %s, %s, %s, %s, %s) RETURNING id""",
               (j({"role_code": role_code, "synonyms": before}),
                j({"role_code": role_code, "synonyms": after, "profile_id": profile_id, "rollback_of": rollback_of}),
                user_id, now, note))


def _clean_items(items: object, keywords: list[str]) -> dict[str, list[str]]:
    """Проверка сохранения: ключ есть в действующей версии, термины по clean_term, не ключ, без повторов, ≤ 10."""
    if not isinstance(items, list):
        raise Invalid("items: список ключей с синонимами")
    keys = {norm(k) for k in keywords}
    out: dict[str, list[str]] = {}
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("keyword"), str) or \
                not isinstance(it.get("synonyms"), list):
            raise Invalid("items: ключ и список синонимов")
        kw = norm(it["keyword"])
        if kw not in keys:
            raise Invalid(f"ключа «{it['keyword']}» нет в действующей версии профиля")
        if kw in out:
            raise Invalid(f"ключ «{it['keyword']}» указан дважды")
        terms: list[str] = []
        for raw in it["synonyms"]:
            t = clean_term(raw)
            if t is None:
                raise Invalid("синоним — от 2 до 60 знаков, без ссылок и знаков < > \" ( ) !")
            if norm(t) in keys:
                raise Invalid(f"«{t}» — уже ключ профиля")
            if norm(t) in {norm(x) for x in terms}:
                raise Invalid(f"«{t}» повторяется")
            terms.append(t)
        if len(terms) > MAX_SAVED:
            raise Invalid(f"не больше {MAX_SAVED} синонимов на ключ")
        out[kw] = terms
    return out


def _profile(conn: psycopg.Connection, profile_id: int) -> dict:
    try:
        return current(conn, profile_id)
    except NoKeywords:
        raise Invalid("у должности нет утверждённой версии с ключами") from None


def save(conn: psycopg.Connection, profile_id: int, items: object, user_id: int, now: datetime, *,
         base_change_id: int | None) -> int:
    """S2: синонимы названных ключей заменяются сразу, без версии профиля; пустой список — убрать.
    Ключи, которых нет в items, не меняются. Пересчёт запускает вызывающий (rescore, в фоне)."""
    p = _profile(conn, profile_id)
    got = _clean_items(items, p["keywords"] or [])
    _lock(conn, p["code"])
    if last_change_id(conn, p["code"]) != base_change_id:
        raise Stale("синонимы уже изменил кто-то другой — обновите страницу")
    before = load(conn, p["code"])
    after = {**before, **got}
    after = {k: v for k, v in sorted(after.items()) if v}
    if after == before:
        raise Invalid("нет изменений")
    return _write(conn, p["code"], before, after, user_id, now, profile_id=p["id"], rollback_of=None,
                  note=f"синонимы ключей профиля «{p['title']}»")


def rollback(conn: psycopg.Connection, profile_id: int, change_id: int, user_id: int, now: datetime) -> int:
    """S3: синонимы — как до правки change_id; только последняя запись должности, иначе NotLatest."""
    code = val(conn, "SELECT code FROM role_profile WHERE id = %s", (profile_id,))
    if code is None:
        raise NotFound
    ch = one(conn, """SELECT id, before FROM agent_change
                      WHERE id = %s AND kind = 'synonyms' AND after->>'role_code' = %s""", (change_id, code))
    if not ch:
        raise NotFound
    _lock(conn, code)
    if last_change_id(conn, code) != change_id:
        raise NotLatest("откатить можно только последнюю правку синонимов")
    before = load(conn, code)
    after = {k: list(v) for k, v in sorted((ch["before"].get("synonyms") or {}).items()) if v}
    cur = one(conn, """SELECT id, title FROM role_profile WHERE code = %s AND is_active AND approved_at IS NOT NULL
                       ORDER BY approved_at DESC, id DESC LIMIT 1""", (code,))
    return _write(conn, code, before, after, user_id, now, profile_id=cur["id"] if cur else profile_id,
                  rollback_of=change_id, note=f"откат правки синонимов № {change_id}")


def prune(conn: psycopg.Connection, role_code: str, keywords: list[str], version: str, profile_id: int,
          user_id: int | None, now: datetime) -> int | None:
    """Утверждена версия без ключа: его синонимы удаляются с записью в журнале. Нечего удалять — None."""
    _lock(conn, role_code)
    before = load(conn, role_code)
    keys = {norm(k) for k in keywords or []}
    gone = sorted(before.keys() - keys)
    if not gone:
        return None
    after = {k: v for k, v in before.items() if k in keys}
    return _write(conn, role_code, before, after, user_id, now, profile_id=profile_id, rollback_of=None,
                  note=f"ключ удалён в версии {version}: {', '.join(gone)}")


def rescore(conn_factory: ConnFactory, profile_id: int) -> None:
    """Фоновый пересчёт после сохранения и отката: действующая версия профиля, затем «мой балл».
    Сначала вектор профиля: в его тексте синонимы (T10)."""
    from . import jobs, pipeline, search, tweaks  # pipeline и tweaks тянут скоринг; синонимы нужны скорингу (T7)

    with conn_factory() as conn:
        pid = current(conn, profile_id)["id"]
    search.refresh_profile(conn_factory, pid)
    jobs.run("rescore_profile", lambda cf, **_: pipeline.rescore_profile(cf, pid), conn_factory,
             locks=(f"profile:{pid}",))
    jobs.run("user_rescore", lambda cf, **_: tweaks.user_rescore(cf), conn_factory)
