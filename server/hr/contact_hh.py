"""Открытие контакта кандидата с hh (S16, S17; пункт 11). Платно: один контакт hh за открытие.

prepare к hh не ходит: только hidden_fields и contact_view_status из сохранённого резюме.
open — только после подтверждения человеком: свежий /resumes/{id} (уже FULL — без списания),
иначе ссылка actions.get_with_contact, и только если её хост — HH_API: иначе токен уйдёт по чужому адресу.
Значения контактов — только в таблице contact; в действие и журнал пишется лишь их число.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

import httpx

from . import hh, jobs
from .clock import Clock
from .config import get_config
from .db import ConnFactory, j, one, rows, run
from .http import PageBudget, RateLimiter

log = logging.getLogger(__name__)
BLOCKING = {"phones", "email", "other_contacts"}   # все каналы скрыты — открывать нечего
PHONE_TYPES = {"cell", "home", "work"}

_limiter = RateLimiter()


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class Upstream(RuntimeError):
    """hh не дал контакт: лимит, нет доступа, чужой адрес ссылки."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _resume(conn, candidate_id: int) -> dict:
    r = one(conn, """SELECT o.external_id, o.raw FROM candidate c
                     JOIN observation o ON o.person_id = c.person_id
                     JOIN source s ON s.id = o.source_id AND s.code = 'hh'
                     WHERE c.id = %s ORDER BY o.last_seen DESC LIMIT 1""", (candidate_id,))
    if not r:
        if not one(conn, "SELECT 1 FROM candidate WHERE id = %s", (candidate_id,)):
            raise NotFound
        raise Conflict("у кандидата нет резюме с hh")
    return r


def prepare(conn, candidate_id: int, user_id: int, now: datetime) -> dict:
    r = _resume(conn, candidate_id)
    raw = r["raw"] or {}
    hidden = [f.get("id") for f in raw.get("hidden_fields") or [] if isinstance(f, dict) and f.get("id")]
    already = bool(rows(conn, "SELECT 1 FROM contact WHERE candidate_id = %s LIMIT 1", (candidate_id,)))
    blocked = BLOCKING <= set(hidden)
    will_spend = not already and not blocked and raw.get("contact_view_status") != "FULL"
    payload = j({"resume_id": r["external_id"], "hidden": hidden, "blocked": blocked, "will_spend": will_spend,
                 "already_open": already, "by_user": user_id})
    # «Открыть контакт» и «Отмена» несколько раз подряд — одно ждущее действие, а не новое каждый раз
    waiting = one(conn, """SELECT id FROM action_queue WHERE kind = 'open_contact' AND candidate_id = %s
                           AND state = 'proposed' ORDER BY id DESC LIMIT 1 FOR UPDATE""", (candidate_id,))
    if waiting:
        run(conn, "UPDATE action_queue SET payload = %s WHERE id = %s", (payload, waiting["id"]))
        aid = waiting["id"]
    else:
        aid = one(conn, """INSERT INTO action_queue (kind, person_id, candidate_id, payload, proposed_at)
                           SELECT 'open_contact', person_id, id, %s, %s FROM candidate WHERE id = %s RETURNING id""",
                  (payload, now, candidate_id))["id"]
    return {"action_id": aid, "hidden": hidden, "already_open": already, "will_spend": will_spend}


def _value(c: dict) -> str:
    v = c.get("contact_value") if "contact_value" in c else c.get("value")
    if isinstance(v, dict):
        v = v.get("formatted") or v.get("number") or ""
    return str(v or "").strip()


def _kind(c: dict) -> str:
    t = ((c.get("type") or {}).get("id") or c.get("kind") or "other").lower()
    return "phone" if t in PHONE_TYPES or c.get("kind") == "phone" else "email" if t == "email" else t


def _hash(kind: str, value: str) -> str:
    norm = re.sub(r"\D", "", value) if kind == "phone" else value.lower()
    return hashlib.sha256(f"{kind}:{norm}".encode()).hexdigest()


def _release(conn_factory: ConnFactory, action_id: int, error: str) -> None:
    with conn_factory() as conn:
        run(conn, "UPDATE action_queue SET state = 'proposed', error = %s WHERE id = %s", (error, action_id))


def open_contact(conn_factory: ConnFactory, action_id: int, user_id: int, http: httpx.Client, clock: Clock,
                 limiter: RateLimiter | None = None) -> dict:
    now = clock.now()
    with conn_factory() as conn:   # «approved» на время вызова: второе нажатие не спишет второй контакт
        a = one(conn, "SELECT * FROM action_queue WHERE id = %s AND kind = 'open_contact' FOR UPDATE", (action_id,))
        if not a:
            raise NotFound
        if a["state"] != "proposed":
            raise Conflict("действие уже решено")
        if a["payload"].get("blocked"):
            raise Conflict("соискатель скрыл все контакты — открывать нечего")
        run(conn, "UPDATE action_queue SET state = 'approved', decided_by = %s, decided_at = %s WHERE id = %s",
            (user_id, now, action_id))
    rid, cid = a["payload"]["resume_id"], a["candidate_id"]
    with jobs.try_lock("source:hh") as free:
        if not free:
            _release(conn_factory, action_id, "busy")
            raise Conflict("идёт сбор hh, повторите через несколько минут")
        api = hh.Api(conn_factory, http, clock, limiter or _limiter, PageBudget(5, jobs.JobStats()))
        try:
            fresh = api.get(f"/resumes/{rid}")
            spent = False
            if fresh.get("contact_view_status") == "FULL" and fresh.get("contact"):
                found = fresh["contact"]
            else:
                url = ((fresh.get("actions") or {}).get("get_with_contact") or {}).get("url") or ""
                u, base = urlparse(url), urlparse(get_config().hh_api)
                if u.scheme != "https" or u.hostname != base.hostname:
                    raise Upstream("hh вернул ссылку на чужой адрес — контакт не открыт")
                found, spent = api.get(u.path, dict(parse_qsl(u.query))).get("contact") or [], True
        except Upstream as e:
            _release(conn_factory, action_id, "foreign_host")
            raise e
        except (jobs.JobError, hh.BudgetExhausted) as e:
            _release(conn_factory, action_id, type(e).__name__)
            raise Upstream(str(e), 503) from None
        except httpx.HTTPError as e:
            _release(conn_factory, action_id, type(e).__name__)
            raise Upstream("hh не ответил, повторите позже") from None
    stored = 0
    with conn_factory() as conn:
        for c in found if isinstance(found, list) else []:
            kind, value = _kind(c), _value(c)
            if value:
                stored += len(rows(conn, """INSERT INTO contact (candidate_id, kind, value, value_hash, is_primary)
                                            VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
                                   (cid, kind, value, _hash(kind, value), bool(c.get("preferred")))))
        result = {"contacts": stored, "spent": spent}
        run(conn, """UPDATE action_queue SET state = 'executed', executed_at = %s, error = NULL,
                         payload = payload || %s WHERE id = %s""", (now, j({"result": result}), action_id))
        run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail)
                     VALUES (%s, 'view_contact', 'candidate', %s, %s)""",
            (user_id, cid, j({"action_id": action_id, "opened": True, "spent": spent, "channel": "web"})))
    return {"state": "executed", "result": result}


def contacts(conn, candidate_id: int, user_id: int) -> list[dict]:
    """Показ контактов — каждый раз запись в audit_log (security.md)."""
    found = rows(conn, """SELECT kind, value, is_primary, verified FROM contact WHERE candidate_id = %s
                          ORDER BY is_primary DESC, kind""", (candidate_id,))
    run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail)
                 VALUES (%s, 'view_contact', 'candidate', %s, %s)""", (user_id, candidate_id, j({"channel": "web"})))
    return found
