"""Отправка в Телеграм-бот человека (manager-cabinet S21): только недельная сводка замечаний сотрудников.

Контактов и имён кандидатов здесь не бывает: текст собирается из замаскированных замечаний (never.md).
Сообщение длиннее 4000 знаков режется на части по строкам.
"""
from __future__ import annotations

import httpx

from .config import get_config
from .jobs import JobError, JobNotConfigured

LIMIT = 4000


def configured() -> bool:
    c = get_config()
    return bool(c.feedback_tg_token and c.feedback_tg_chat)


def chunks(text: str) -> list[str]:
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > LIMIT:
            out.append(cur + line[:LIMIT - len(cur)])
            line, cur = line[LIMIT - len(cur):], ""
        if len(cur) + len(line) > LIMIT:
            out.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out


def send(text: str, http: httpx.Client | None = None) -> int:
    c = get_config()
    if not configured():
        raise JobNotConfigured("Телеграм не настроен: FEEDBACK_TG_TOKEN и FEEDBACK_TG_CHAT в .env")
    own = http is None
    http = http or httpx.Client(timeout=30.0)
    try:
        parts = chunks(text)
        for p in parts:
            try:
                r = http.post(f"{c.telegram_api}/bot{c.feedback_tg_token}/sendMessage",
                              json={"chat_id": c.feedback_tg_chat, "text": p, "disable_web_page_preview": True})
            except httpx.HTTPError as e:   # в тексте исключения адрес с токеном — наружу только класс
                raise JobError(f"Телеграм не ответил: {type(e).__name__}") from None
            if r.status_code >= 400:
                raise JobError(f"Телеграм ответил ошибкой {r.status_code}")
        return len(parts)
    finally:
        if own:
            http.close()
