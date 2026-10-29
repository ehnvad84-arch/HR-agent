"""Очередь действий с кандидатом (S18, S37; пункты 9, 10): агент готовит, человек одобряет.

Письмо — черновик для копирования (Решения): система его не отправляет. «Одобрить» сохраняет
итоговый текст (с правкой человека) в истории и переводит кандидата на этап «Написали».
В модель уходят только поля вакансии и резюме, без имени и контактов; имя подставляет код (W1).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx

from . import assist, explain, funnel, llm
from .config import get_config
from .db import ConnFactory, j, one, run, setting, val
from .pipeline import person_data

log = logging.getLogger(__name__)
KINDS = {"first_message", "follow_up", "invite_interview", "send_test_task", "reject_notice"}
NAME = "{имя}"
_CONTACTS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|(?:\+7|8)[\s(-]*\d{3}", re.I)

TEMPLATES = {
    "first_message": ("Здравствуйте, {имя}! Пишем из отдела персонала «Рувер»: мы разрабатываем и производим "
                      "IP-камеры в Краснодаре. Нам интересен ваш опыт: {опыт}. Сейчас ищем человека на позицию "
                      "«{вакансия}». Удобно ли коротко созвониться на этой неделе?"),
    "follow_up": ("Здравствуйте, {имя}! На прошлой неделе мы писали вам о позиции «{вакансия}» в «Рувер». "
                  "Если тема актуальна, будем рады короткому разговору в удобное время."),
    "invite_interview": ("Здравствуйте, {имя}! Спасибо за разговор. Приглашаем вас на собеседование по позиции "
                         "«{вакансия}». Подскажите, какое время вам удобно."),
    "send_test_task": ("Здравствуйте, {имя}! Предлагаем следующий шаг по позиции «{вакансия}» — небольшое тестовое "
                       "задание. Пришлём его, как только подтвердите, что готовы."),
    "reject_notice": ("Здравствуйте, {имя}! Спасибо за время и интерес к «Рувер». Сейчас по позиции «{вакансия}» мы "
                      "выбрали другого кандидата. С вашего разрешения сохраним контакт на будущее."),
}


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


def _first_name(conn, person_id: int | None) -> str | None:
    if not person_id:
        return None
    return val(conn, """SELECT nullif(btrim(o.raw->>'first_name'), '') FROM observation o
                        WHERE o.person_id = %s ORDER BY o.last_seen DESC LIMIT 1""", (person_id,))


def personalize(text: str, first_name: str | None) -> str:
    if first_name:
        return text.replace(NAME, first_name)
    return text.replace(", " + NAME, "").replace(NAME, "")


def validate(reply: object) -> str | None:
    if not isinstance(reply, dict) or set(reply) != {"text"} or not isinstance(reply["text"], str):
        return None
    text = reply["text"].strip()
    if not text or len(text) > explain.MAX_LEN or explain._BAD.search(text) or _CONTACTS.search(text):
        return None
    return text


def _template(kind: str, data: dict) -> str:
    exp = ", ".join((data["resume"].get("skills") or [])[:3]) or (data["resume"].get("title") or "по профилю")
    return TEMPLATES[kind].replace("{опыт}", exp).replace("{вакансия}", data["vacancy"]["title"])


def draft(conn_factory: ConnFactory, candidate_id: int, kind: str, user_id: int, now: datetime,
          vacancy_id: int | None = None, http: httpx.Client | None = None) -> dict:
    """Черновик письма в action_queue (proposed): {action_id, text, by}."""
    if kind not in KINDS:
        raise ValueError(kind)
    system, version = llm.prompt("draft_letter")
    with conn_factory() as conn:
        c = one(conn, "SELECT id, person_id FROM candidate WHERE id = %s", (candidate_id,))
        if not c:
            raise NotFound
        basis = val(conn, "SELECT basis::text FROM consent WHERE candidate_id = %s ORDER BY created_at DESC LIMIT 1",
                    (candidate_id,))
        if basis in (None, "not_obtained"):
            raise Conflict("нет основания обработки: переписка заблокирована")
        vacancy_id = vacancy_id or val(conn, """SELECT vacancy_id FROM ops.candidate_vacancy WHERE candidate_id = %s
                                                ORDER BY added_at DESC LIMIT 1""", (candidate_id,))
        v = one(conn, """SELECT v.title, rp.title AS role_title FROM vacancy v
                         JOIN role_profile rp ON rp.id = v.role_profile_id WHERE v.id = %s""", (vacancy_id,)) or \
            {"title": "по вашему профилю", "role_title": None}
        p = person_data(conn, c["person_id"]) if c["person_id"] else None
        data = {"kind": kind, "vacancy": v,
                "resume": {"title": p.title, "city": p.city, "exp_years": p.exp_years,
                           "employers": p.employers[:4], "skills": p.skills[:20]} if p else {}}
        first = _first_name(conn, c["person_id"])
        key = assist.cache_key("draft", version, data)
        hit = one(conn, "SELECT reply FROM ops.llm_cache WHERE key = %s", (key,)) if get_config().llm_enabled else None
        pause = float(setting(conn, "llm_pause_minutes", 60))
        capped = get_config().llm_enabled and not hit and assist._over_hourly_cap(conn)
    text, by = (hit["reply"]["text"], "model") if hit else (None, "template")
    if text is None and get_config().llm_enabled and not capped:
        try:
            reply = llm.complete([{"role": "system", "content": system},
                                  {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)}],
                                 http=http, pause_minutes=pause)
            text = validate(reply)
            if text:
                by = "model"
                with conn_factory() as conn:
                    run(conn, """INSERT INTO ops.llm_cache (key, purpose, person_id, reply)
                                 VALUES (%s, 'draft', %s, %s) ON CONFLICT (key) DO NOTHING""",
                        (key, c["person_id"], j({"text": text})))
        except llm.LLMUnavailable as e:
            log.info("черновик по шаблону: %s", e)
    text = personalize(text or _template(kind, data), first)
    with conn_factory() as conn:
        aid = val(conn, """INSERT INTO action_queue (kind, person_id, candidate_id, vacancy_id, payload, proposed_at)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                  (kind, c["person_id"], candidate_id, vacancy_id, j({"text": text, "by": by, "by_user": user_id}),
                   now))
    return {"action_id": aid, "text": text, "by": by}


def _proposed(conn, action_id: int) -> dict:
    a = one(conn, "SELECT *, kind::text AS kind_s, state::text AS state_s FROM action_queue WHERE id = %s FOR UPDATE",
            (action_id,))
    if not a:
        raise NotFound
    if a["state_s"] != "proposed":
        raise Conflict("действие уже решено")
    return a


def approve(conn, action_id: int, user_id: int, now: datetime, text: str | None = None) -> dict:
    """Письмо: итоговый текст в историю, статус «Написали» (first_message) или отметка контакта."""
    a = _proposed(conn, action_id)
    final = (text or "").strip() or a["payload"].get("text", "")
    payload = {**a["payload"], "final_text": final, "edited": final != a["payload"].get("text")}
    run(conn, """UPDATE action_queue SET state = 'executed', decided_by = %s, decided_at = %s, executed_at = %s,
                     payload = %s WHERE id = %s""", (user_id, now, now, j(payload), action_id))
    if a["candidate_id"]:
        if a["kind_s"] == "first_message":
            funnel.move(conn, a["candidate_id"], "first_message_sent", user_id, now, vacancy_id=a["vacancy_id"],
                        comment="письмо одобрено и скопировано")
        else:
            run(conn, "UPDATE candidate SET last_contact_at = %s WHERE id = %s", (now, a["candidate_id"]))
    return {"state": "executed", "result": {"text": final}}


def decline(conn, action_id: int, user_id: int, now: datetime, reason: str) -> dict:
    if not reason.strip():
        raise ValueError("причина")
    _proposed(conn, action_id)
    run(conn, """UPDATE action_queue SET state = 'declined', decided_by = %s, decided_at = %s, decline_reason = %s
                 WHERE id = %s""", (user_id, now, reason.strip(), action_id))
    return {"state": "declined"}
