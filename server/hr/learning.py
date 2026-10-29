"""Чат обучения агента (S29) и недельная сводка (S30, S31).

Агент ничего не меняет по сообщению: оно попадает в сводку, решает руководитель (W2).
Перед моделью из текста вырезаются телефоны, почта, ссылки и имена людей из базы (W3);
осталось похожее на ФИО — модель не зовётся, ответ по шаблону.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx

from . import actions, assist, llm
from .config import get_config
from .db import ConnFactory, j, rows, run, setting, val

log = logging.getLogger(__name__)
TAGS = {"remark", "recommendation", "criticism", "question", "reject_reason", "postpone_reason"}
MIN_CHARS = 40   # и CHECK в схеме: feedback_message.body не короче 40 знаков

_PHONE = re.compile(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_FIO_LIKE = re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s*[А-ЯЁ]?\.?")   # «Иванов И. И.»

TEMPLATE_REPLY = {
    "question": "Записал вопрос. Отвечу в недельной сводке вместе с остальными, решение — за руководителем.",
}
DEFAULT_REPLY = "Записал. Замечание войдёт в недельную сводку, изменения утверждает руководитель отдела персонала."


class Invalid(ValueError):
    pass


def _known_names(conn) -> set[str]:
    """Фамилии и имена людей из базы: кандидаты и то, что hh отдал в резюме."""
    words = set()
    for r in rows(conn, """SELECT fio AS n FROM candidate
                           UNION SELECT raw->>'last_name' FROM observation WHERE raw ? 'last_name'
                           UNION SELECT raw->>'first_name' FROM observation WHERE raw ? 'first_name'"""):
        for w in (r["n"] or "").split():
            if len(w) >= 3 and not w.endswith("."):
                words.add(w.lower())
    return words


def mask(conn, text: str) -> tuple[str, bool]:
    """(текст без контактов и имён, уверены ли, что имён не осталось)."""
    out = _URL.sub("[ссылка]", _EMAIL.sub("[почта]", _PHONE.sub("[телефон]", text)))
    for name in sorted(_known_names(conn), key=len, reverse=True):
        stem = re.escape(name[:-1] if len(name) > 4 else name)   # «Скворцов» → «Скворцову», «Скворцова»
        out = re.sub(rf"\b{stem}[а-яё]{{0,3}}\b", "[имя]", out, flags=re.I)
    return out, not _FIO_LIKE.search(out)


def post(conn_factory: ConnFactory, user_id: int, body: str, tag: str, now: datetime,
         person_id: int | None = None, role_profile_id: int | None = None, http: httpx.Client | None = None) -> dict:
    body = body.strip()
    if tag not in TAGS:
        raise Invalid("вид сообщения")
    if len(body) < MIN_CHARS:
        raise Invalid(f"сообщение не короче {MIN_CHARS} знаков")
    system, version = llm.prompt("chat_reply")
    with conn_factory() as conn:
        fid = val(conn, """INSERT INTO feedback_message (author_id, tag, body, person_id, role_profile_id, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                  (user_id, tag, body, person_id, role_profile_id, now))
        masked, sure = mask(conn, body)
        use_model = get_config().llm_enabled and sure and not assist._over_hourly_cap(conn)
        pause = float(setting(conn, "llm_pause_minutes", 60))
    reply, by = TEMPLATE_REPLY.get(tag, DEFAULT_REPLY), "template"
    if use_model:
        try:
            got = actions.validate(llm.complete(
                [{"role": "system", "content": system},
                 {"role": "user", "content": json.dumps({"tag": tag, "text": masked}, ensure_ascii=False)}],
                http=http, pause_minutes=pause, max_tokens=200))
            if got:
                reply, by = got, "model"
        except llm.LLMUnavailable as e:
            log.info("ответ в чате по шаблону: %s", e)
    with conn_factory() as conn:
        run(conn, "INSERT INTO ops.feedback_reply (feedback_id, body, by, created_at) VALUES (%s, %s, %s, %s)",
            (fid, reply, by, now))
        if by == "model":
            run(conn, """INSERT INTO ops.llm_cache (key, purpose, reply) VALUES (%s, 'chat', %s)
                         ON CONFLICT (key) DO NOTHING""",
                (assist.cache_key("chat", version, {"id": fid}), j({"text": reply})))
    return {"id": fid, "reply": reply, "by": by}


def feed(conn, limit: int = 100) -> list[dict]:
    return rows(conn, """
        SELECT f.id, f.tag::text AS tag, f.body, f.created_at, f.digest_id, u.full_name AS author,
               r.body AS reply, r.by AS reply_by
        FROM feedback_message f JOIN app_user u ON u.id = f.author_id
        LEFT JOIN ops.feedback_reply r ON r.feedback_id = f.id
        ORDER BY f.created_at DESC, f.id DESC LIMIT %s""", (limit,))

