"""Помощь модели по действию человека: разбор балла при открытии досье (S7, S8).

Модель зовётся только по запросу, ответ кэшируется в ops.llm_cache по ключу входа
(назначение, модель, версия промпта, данные): повторное открытие модель не зовёт.
Ответ проверяется (explain.validate: ровно нужные поля, без ссылок и разметки); не прошло,
модель недоступна или исчерпан лимит в час — разбор по шаблону, без ошибки.
"""
from __future__ import annotations

import hashlib
import json
import logging

import httpx

from . import explain, llm
from .config import get_config
from .db import ConnFactory, j, one, run, setting, val

log = logging.getLogger(__name__)


def cache_key(purpose: str, version: str, data: dict) -> str:
    raw = json.dumps([purpose, llm.model_name(), version, data], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _over_hourly_cap(conn) -> bool:
    cap = int(setting(conn, "llm_calls_per_hour", 60))
    return val(conn, "SELECT count(*) FROM ops.llm_cache WHERE created_at > now() - interval '1 hour'") >= cap


def _text(ok: dict) -> str:
    return "\n".join([ok["why"], ok["gap"], "Спросить: " + "; ".join(ok["questions"])])


class NotFound(LookupError):
    pass


def explain_pair(conn_factory: ConnFactory, person_id: int, role_profile_id: int,
                 http: httpx.Client | None = None) -> dict:
    """{text, by: model|template}. Никогда не падает из-за модели (S8)."""
    system, version = llm.prompt("explain_score")
    with conn_factory() as conn:
        m = one(conn, """SELECT m.id, m.person_id, m.score, m.profile_version, m.features, m.explanation, m.model,
                                rp.title, rp.must_have, rp.keywords, rp.salary_max
                         FROM match m JOIN role_profile rp ON rp.id = m.role_profile_id
                         WHERE m.person_id = %s AND m.role_profile_id = %s""", (person_id, role_profile_id))
        if not m:
            raise NotFound
        template = {"text": m["explanation"] or "", "by": "template"}
        if not get_config().llm_enabled:
            return template
        data = explain.payload(conn, m)          # только структурные поля, без имени и контактов
        key = cache_key("explain", version, data)
        hit = one(conn, "SELECT reply FROM ops.llm_cache WHERE key = %s", (key,))
        if hit:
            return {"text": hit["reply"]["text"], "by": "model"}
        if _over_hourly_cap(conn):
            return template
        pause = float(setting(conn, "llm_pause_minutes", 60))
    try:   # вне транзакции: модель отвечает секунды
        reply = llm.complete([{"role": "system", "content": system},
                              {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)}],
                             http=http, pause_minutes=pause)
    except llm.LLMUnavailable as e:
        log.info("разбор по шаблону: %s", e)
        return template
    ok = explain.validate(reply, m["score"])
    if ok is None:
        return template
    text = _text(ok)
    with conn_factory() as conn:
        run(conn, """INSERT INTO ops.llm_cache (key, purpose, person_id, reply) VALUES (%s, 'explain', %s, %s)
                     ON CONFLICT (key) DO NOTHING""", (key, person_id, j({"text": text})))
        cur = one(conn, "SELECT score, profile_version FROM match WHERE id = %s FOR UPDATE", (m["id"],))
        if cur and cur["score"] == m["score"] and cur["profile_version"] == m["profile_version"]:
            run(conn, "UPDATE match SET explanation = %s, model = %s WHERE id = %s",
                (text, f"{llm.model_name()}/explain_score@{version}", m["id"]))
    return {"text": text, "by": "model"}
