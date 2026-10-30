"""Чат обучения агента (S29) и недельная сводка (S30, S31).

Агент ничего не меняет по сообщению: оно попадает в сводку, решает руководитель (W2).
Перед моделью из текста вырезаются телефоны, почта, ссылки и имена людей из базы (W3);
осталось похожее на ФИО — модель не зовётся, ответ по шаблону.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

import httpx

from . import actions, assist, embed, llm, profiles
from .clock import SYSTEM
from .config import get_config
from .db import ConnFactory, j, one, rows, run, setting, val
from .jobs import JobError, JobStats
from .scoring import FEATURE_LABELS

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


def strip_contacts(text: str) -> str:
    """Только контакты: телефоны, почта, ссылки (досье руководителю, тексты наружу)."""
    return _URL.sub("[ссылка]", _EMAIL.sub("[почта]", _PHONE.sub("[телефон]", text)))


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
        from . import tweaks  # предложения личных поправок по тексту замечания — только proposed (S15, A1)
        tweaks.suggest(conn, user_id, fid, body, now)
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


def feed(conn, author_id: int | None = None, limit: int = 100) -> list[dict]:
    """S14: каждый видит только свой чат; author_id None — все (недельная сводка, S21)."""
    return rows(conn, """
        SELECT f.id, f.tag::text AS tag, f.body, f.created_at, f.digest_id, u.full_name AS author,
               r.body AS reply, r.by AS reply_by
        FROM feedback_message f JOIN app_user u ON u.id = f.author_id
        LEFT JOIN ops.feedback_reply r ON r.feedback_id = f.id
        WHERE %s::int IS NULL OR f.author_id = %s
        ORDER BY f.created_at DESC, f.id DESC LIMIT %s""", (author_id, author_id, limit))



# ------------------------------------------------------------------ недельная сводка

# Что сводка может поменять сама после «принять»: ключ → допустимые границы (W2)
SETTING_BOUNDS = {"watch_threshold": (40, 90), "daily_cards_limit": (3, 50), "alert_score_new": (50, 95),
                  "alert_score_pool_event": (40, 95), "stalled_after_days": (1, 30), "reserve_touch_months": (1, 12)}
TAG_TITLE = {"remark": "замечание", "recommendation": "рекомендация", "criticism": "критика", "question": "вопрос",
             "reject_reason": "причина отказа", "postpone_reason": "причина «отложить»"}


def safe_target(conn, target: object) -> dict:
    """Только белый список ключей в границах; остальное — пусто: применится как «принято без правки»."""
    if not isinstance(target, dict) or not target:
        return {}
    if set(target) == {"setting", "value"} and target["setting"] in SETTING_BOUNDS:
        lo, hi = SETTING_BOUNDS[target["setting"]]
        v = target["value"]
        return {"setting": target["setting"], "value": v} if isinstance(v, int) and lo <= v <= hi else {}
    if set(target) in ({"tweak", "profile_code"}, {"tweak", "profile_code", "profile_codes"}) \
            and isinstance(target["tweak"], dict):   # S16
        from . import tweaks
        t = target["tweak"]
        try:
            v = tweaks.clean(t.get("kind"), t.get("value"))
        except tweaks.Invalid:
            return {}
        code = target["profile_code"]
        if code is not None and not val(conn, "SELECT 1 FROM role_profile WHERE code = %s AND approved_at IS NOT NULL",
                                        (code,)):
            return {}
        codes = [c for c in (target.get("profile_codes") or []) if isinstance(c, str)
                 and val(conn, "SELECT 1 FROM role_profile WHERE code = %s AND approved_at IS NOT NULL", (c,))]
        return {"tweak": {"kind": t["kind"], "value": v, "key": tweaks.key(t["kind"], v)}, "profile_code": code,
                "profile_codes": codes}
    if set(target) == {"profile_code", "feature", "value"}:
        v = target["value"]
        exists = val(conn, "SELECT 1 FROM role_profile WHERE code = %s AND approved_at IS NOT NULL",
                     (target["profile_code"],))
        if exists and target["feature"] in FEATURE_LABELS and isinstance(v, int) and 0 <= v <= 100:
            return dict(target)
    return {}


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _clusters(texts: list[str], threshold: float) -> list[list[int]]:
    """Похожие сообщения — в одну группу (одиночная связь по векторам embed)."""
    vecs = [embed.vector(t) for t in texts]
    groups: list[list[int]] = []
    for i, v in enumerate(vecs):
        home = next((g for g in groups if any(_cos(v, vecs[k]) >= threshold for k in g)), None)
        (home.append(i) if home is not None else groups.append([i]))
    return groups


def build_digest(conn_factory: ConnFactory, now: datetime, *, http: httpx.Client | None = None, **_):
    """Задача digest (пятница): сообщения без сводки → группы → по предложению на группу (S30, пункт 19)."""
    stats = JobStats()
    system, _ = llm.prompt("digest_proposal")
    with conn_factory() as conn:
        msgs = rows(conn, """SELECT id, tag::text AS tag, body, created_at FROM feedback_message
                             WHERE digest_id IS NULL ORDER BY created_at""")
        from . import tweaks
        common = tweaks.shared(conn)
        if common:   # S16: одинаковые личные поправки у нескольких — предложение сделать их общими
            did0 = val(conn, """INSERT INTO feedback_digest (period_from, period_to, messages) VALUES (%s, %s, 0)
                                ON CONFLICT (period_from, period_to) DO UPDATE SET messages = feedback_digest.messages
                                RETURNING id""", (now.date(), now.date()))
            for g in common:
                what = g["value"].get("text") or f"{FEATURE_LABELS[g['value']['feature']]} = {g['value']['weight']}"
                run(conn, """INSERT INTO digest_proposal (digest_id, title, rationale, question, target)
                             VALUES (%s, %s, %s, %s, %s)""",
                    (did0, f"Сделать общей поправку «{what}» ({g['kind']})"[:300],
                     f"Одна и та же личная поправка у {len(g['users'])} сотрудников.",
                     "Принять — появится версия портрета на утверждение?",
                     j({"tweak": {"kind": g["kind"], "value": g["value"], "key": g["key"]},
                        "profile_code": g["role_code"], "profile_codes": g["codes"]})))
                stats.new += 1
        if not msgs:
            stats.note = "новых сообщений нет"
            return stats
        threshold = float(setting(conn, "digest_similarity", 0.35))
        masked = [mask(conn, m["body"]) for m in msgs]
        did = val(conn, """INSERT INTO feedback_digest (period_from, period_to, messages) VALUES (%s, %s, %s)
                           ON CONFLICT (period_from, period_to) DO UPDATE SET messages = feedback_digest.messages
                               + EXCLUDED.messages RETURNING id""",
                  (min(m["created_at"] for m in msgs).date(), now.date(), len(msgs)))
        pause = float(setting(conn, "llm_pause_minutes", 60))
    groups = _clusters([t for t, _ in masked], threshold)
    for g in groups:
        stats.seen += len(g)
        first = msgs[g[0]]
        prop = {"title": f"Пересмотреть по замечаниям ({TAG_TITLE.get(first['tag'], first['tag'])}): "
                         f"«{masked[g[0]][0][:90]}»",
                "rationale": f"{len(g)} сообщ. за неделю об одном и том же.",
                "question": "Принять и внести изменение?", "target": {}}
        sure = all(masked[i][1] for i in g)
        if get_config().llm_enabled and sure:
            try:
                reply = llm.complete([{"role": "system", "content": system},
                                      {"role": "user", "content": json.dumps(
                                          {"messages": [masked[i][0] for i in g]}, ensure_ascii=False)}],
                                     http=http, pause_minutes=pause, max_tokens=400)
                if isinstance(reply, dict) and all(isinstance(reply.get(k), str) and reply[k].strip()
                                                   for k in ("title", "rationale", "question")):
                    text = " ".join(reply[k] for k in ("title", "rationale", "question"))
                    if len(text) <= 1500 and not _URL.search(text) and "<" not in text:
                        prop = {k: reply[k].strip() for k in ("title", "rationale", "question")}
                        prop["rationale"] = f"{len(g)} сообщ. за неделю. " + prop["rationale"]
                        prop["target"] = reply.get("target")
            except llm.LLMUnavailable as e:
                log.info("предложение сводки по шаблону: %s", e)
        with conn_factory() as conn:
            prop["target"] = safe_target(conn, prop.get("target"))
            run(conn, """INSERT INTO digest_proposal (digest_id, title, rationale, question, target)
                         VALUES (%s, %s, %s, %s, %s)""",
                (did, prop["title"][:300], prop["rationale"], prop["question"], j(prop["target"])))
            run(conn, "UPDATE feedback_message SET digest_id = %s WHERE id = ANY(%s)",
                (did, [msgs[i]["id"] for i in g]))
        stats.new += 1
    return stats


def current(conn) -> dict | None:
    d = one(conn, "SELECT * FROM feedback_digest ORDER BY period_to DESC, id DESC LIMIT 1")
    if not d:
        return None
    d["proposals"] = rows(conn, """SELECT id, title, rationale, question, target, decision::text AS decision,
                                          decided_at, decision_note, applied_at
                                   FROM digest_proposal WHERE digest_id = %s ORDER BY id""", (d["id"],))
    return d


class Conflict(RuntimeError):
    pass


def decide(conn, proposal_id: int, decision: str, note: str | None, user_id: int, now: datetime) -> str:
    """S31: решение руководителя; применение — ночью задачей apply_digest."""
    if decision not in ("accepted", "declined", "discuss"):
        raise Invalid("решение")
    p = one(conn, "SELECT decision::text AS decision FROM digest_proposal WHERE id = %s FOR UPDATE", (proposal_id,))
    if not p:
        raise LookupError
    if p["decision"] in ("accepted", "declined"):
        raise Conflict("решение уже принято")
    run(conn, """UPDATE digest_proposal SET decision = %s, decided_by = %s, decided_at = %s, decision_note = %s
                 WHERE id = %s""", (decision, user_id, now, note, proposal_id))
    return decision


def apply_digest(conn_factory: ConnFactory, *, now: datetime | None = None, clock=None, **_):
    """Задача apply_digest (ночь): принятое из белого списка — в настройки или веса, всё в agent_change (пункт 20)."""
    now = now or (clock or SYSTEM).now()
    stats = JobStats()
    with conn_factory() as conn:
        todo = rows(conn, """SELECT * FROM digest_proposal WHERE decision = 'accepted' AND applied_at IS NULL
                             ORDER BY decided_at, id FOR UPDATE""")
        for p in todo:
            stats.seen += 1
            t = safe_target(conn, p["target"])
            note = f"сводка: {p['title']}"
            if "setting" in t:
                before = setting(conn, t["setting"])
                run(conn, "UPDATE settings SET value = %s, updated_at = %s WHERE key = %s",
                    (j(t["value"]), now, t["setting"]))
                run(conn, """INSERT INTO agent_change (proposal_id, kind, before, after, applied_by, applied_at, note)
                             VALUES (%s, 'threshold', %s, %s, %s, %s, %s)""",
                    (p["id"], j({"setting": t["setting"], "value": before}), j(t), p["decided_by"], now, note))
            elif "tweak" in t:   # S16: общая поправка — версия портрета «ждёт утверждения», не сразу в выдачу
                tw = t["tweak"]
                codes = [t["profile_code"]] if t["profile_code"] else t.get("profile_codes", [])
                for code in codes:
                    cur = one(conn, """SELECT * FROM role_profile WHERE code = %s AND approved_at IS NOT NULL
                                       ORDER BY approved_at DESC, id DESC LIMIT 1""", (code,))
                    ch: dict = {}
                    v = tw["value"]
                    if tw["kind"] == "plus":
                        ch["keywords"] = list(dict.fromkeys([*cur["keywords"], v["text"]]))
                    elif tw["kind"] == "minus":
                        ch["keywords"] = [k for k in cur["keywords"] if k.lower() != v["text"].lower()]
                    elif tw["kind"] == "stop":
                        ch["stop_factors"] = [*cur["stop_factors"], {"text": v["text"]}]
                    else:
                        ch["weights"] = {**(cur["weights"] or {}), v["feature"]: v["weight"]}
                    profiles.edit(conn, cur["id"], ch, p["decided_by"], now)
                run(conn, """INSERT INTO agent_change (proposal_id, kind, before, after, applied_by, applied_at, note)
                             VALUES (%s, 'profile', NULL, %s, %s, %s, %s)""",
                    (p["id"], j(t), p["decided_by"], now,
                     note + (f" — версии портретов на утверждение: {len(codes)}" if codes
                             else " — должностей у авторов нет, портреты не менялись")))
            elif "profile_code" in t:
                cur = one(conn, """SELECT id, weights FROM role_profile WHERE code = %s AND approved_at IS NOT NULL
                                   ORDER BY approved_at DESC, id DESC LIMIT 1""", (t["profile_code"],))
                weights = {**(cur["weights"] or {}), t["feature"]: t["value"]}
                new = profiles.edit(conn, cur["id"], {"weights": weights}, p["decided_by"], now)
                profiles.approve(conn, new["id"], p["decided_by"], now)
                run(conn, """INSERT INTO agent_change (proposal_id, kind, before, after, applied_by, applied_at, note)
                             VALUES (%s, 'weight', %s, %s, %s, %s, %s)""",
                    (p["id"], j({"id": cur["id"], "weights": cur["weights"]}), j({"id": new["id"], **t}),
                     p["decided_by"], now, note))
            else:
                run(conn, """INSERT INTO agent_change (proposal_id, kind, before, after, applied_by, applied_at, note)
                             VALUES (%s, 'template', NULL, %s, %s, %s, %s)""",
                    (p["id"], j({"accepted": p["title"]}), p["decided_by"], now,
                     note + " — принято, автоматической правки нет: вносит человек"))
            run(conn, "UPDATE digest_proposal SET applied_at = %s WHERE id = %s", (now, p["id"]))
            stats.changed += 1
    return stats


# ------------------------------------------------------------------ недельная сводка всех замечаний (S21)

def week_text(conn, now: datetime, days: int = 7) -> tuple[str, int]:
    """Все замечания за неделю от всех сотрудников по видам, с автором и датой; имена и контакты скрыты."""
    since = now - timedelta(days=days)
    msgs = rows(conn, """SELECT f.tag::text AS tag, f.body, f.created_at, u.full_name AS author
                         FROM feedback_message f JOIN app_user u ON u.id = f.author_id
                         WHERE f.created_at > %s ORDER BY f.tag, f.created_at""", (since,))
    head = f"Замечания сотрудников по обучению агента, {since:%d.%m}–{now:%d.%m}: {len(msgs)}"
    if not msgs:
        return head + "\n\nЗа неделю замечаний не было.", 0
    lines = [head]
    for tag in TAG_TITLE:
        part = [m for m in msgs if m["tag"] == tag]
        if part:
            lines.append(f"\n{TAG_TITLE[tag].capitalize()} — {len(part)}:")
            # наружу (Телеграм, почта) — ещё и любое «Фамилия И. О.»: вдруг это кандидат, которого нет в базе
            lines += [f"• {m['author']}, {m['created_at']:%d.%m}: {_FIO_LIKE.sub('[имя]', mask(conn, m['body'])[0])}"
                      for m in part]
    props = rows(conn, """SELECT title FROM digest_proposal WHERE decision IS NULL ORDER BY id DESC LIMIT 10""")
    if props:
        lines.append("\nПредложения агента ждут решения:")
        lines += [f"• {_FIO_LIKE.sub('[имя]', mask(conn, p['title'])[0])}" for p in props]
    return "\n".join(lines), len(msgs)


def feedback_week(conn_factory: ConnFactory, *, clock=None, http: httpx.Client | None = None, **_) -> JobStats:
    """Задача feedback_week (пятница 18:30 МСК): сводка — в Телеграм-бот человека и на почту MAIL_TO."""
    from . import mailer, telegram
    now = (clock or SYSTEM).now()
    stats = JobStats()
    with conn_factory() as conn:
        text, n = week_text(conn, now)
    stats.seen = n
    sent = []
    if telegram.configured():
        try:
            telegram.send(text, http=http)
            sent.append("Телеграм")
        except JobError as e:   # Телеграм не ответил — письмо всё равно уходит
            log.info("сводка замечаний в Телеграм не ушла: %s", e)
    try:
        mailer.send("Замечания сотрудников за неделю", text + f"\n\n{mailer._link()}")
        sent.append("почта")
    except (JobError, mailer.MailError) as e:
        log.info("сводка замечаний на почту не ушла: %s", type(e).__name__)
    stats.note = ("отправлено: " + ", ".join(sent)) if sent else "канал не настроен: сводка только на экране"
    stats.new = len(sent)
    return stats
