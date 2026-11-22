"""Синонимы ключей профиля (S1–S4): модель предлагает, рекрутер правит и сохраняет.

suggest ничего не пишет, кроме кэша ответа: вход модели — только название и ключи действующей
версии профиля (данных людей нет), ответ проходит белый список (clean_term, известные ключи,
не больше 8 на ключ). Повтор с теми же ключами берётся из ops.llm_cache — модель и предел не тратятся.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from . import llm
from .assist import cache_key
from .db import ConnFactory, j, one, run

log = logging.getLogger(__name__)

MAX_SUGGESTED = 8
_URL = re.compile(r"https?://|www\.", re.I)
_BANNED = set('<>"()!')


class NotFound(LookupError):
    pass


class NoKeywords(ValueError):
    """У действующей версии профиля нет ключей — предлагать не к чему (422 no_keywords)."""


def norm(keyword: str) -> str:
    """Ключ в role_synonym: нижний регистр, «ё» → «е», пробелы схлопнуты."""
    return re.sub(r"\s+", " ", keyword).strip().lower().replace("ё", "е")


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
