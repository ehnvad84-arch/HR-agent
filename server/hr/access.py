"""Доступ к порталу (manager-cabinet S2–S4): ручное заведение, роли, одноразовые коды первого входа.

Роль, поставленная человеком, закрепляется в ops.user_override — выгрузка сотрудников 1С её не перетирает.
Код входа: 10 знаков из 32, в базе только HMAC от SESSION_KEY, живой код у человека один, срок —
invite_code_hours. Неверный, истёкший, погашенный код и неизвестный логин — один ответ (M3).
Специалист выдаёт код только руководителю или директору без пароля; остальное — с access.manage (M4).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

import psycopg

from . import auth
from .db import j, one, rows, run, setting, val

ROLES = ("recruiter", "head_recruiter", "hiring_manager", "director", "no_access")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без 0/O и 1/I: код диктуют голосом
CODE_LEN = 10


class Invalid(ValueError):
    pass


class Forbidden(PermissionError):
    pass


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


def _log(conn, by: int | None, now: datetime, note: str, before=None, after=None) -> None:
    run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                 VALUES ('access', %s, %s, %s, %s, %s)""",
        (j(before) if before is not None else None, j(after) if after is not None else None, by, now, note))


def users(conn: psycopg.Connection) -> list[dict]:
    """Экран «Доступ»: все, кроме служебной роли agent."""
    return rows(conn, """
        SELECT u.id, u.login, u.full_name, u.position, u.org_unit_id, ou.title AS org_unit, u.role::text AS role,
               u.is_active, (o.user_id IS NOT NULL) AS manual,
               (p.user_id IS NOT NULL) AS has_password,
               (SELECT max(s.created_at) FROM ops.web_session s WHERE s.user_id = u.id) AS last_login_at,
               (SELECT c.expires_at FROM ops.invite_code c WHERE c.user_id = u.id AND c.used_at IS NULL
                  AND c.revoked_at IS NULL) AS code_expires_at
        FROM app_user u LEFT JOIN org_unit ou ON ou.id = u.org_unit_id
        LEFT JOIN ops.user_override o ON o.user_id = u.id
        LEFT JOIN ops.user_password p ON p.user_id = u.id
        WHERE u.role::text <> 'agent'
        ORDER BY u.is_active DESC, u.full_name""")


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise Invalid("неизвестная роль")


def _set_override(conn, user_id: int, role: str, by: int | None, now: datetime) -> None:
    run(conn, """INSERT INTO ops.user_override (user_id, role, set_by, set_at) VALUES (%s, %s, %s, %s)
                 ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role, set_by = EXCLUDED.set_by,
                     set_at = EXCLUDED.set_at""", (user_id, role, by, now))


def create(conn: psycopg.Connection, login: str, full_name: str, org_unit_id: int | None, position: str | None,
           role: str, by: int, now: datetime) -> int:
    """S2: человек заведён вручную — роль закреплена, выгрузка 1С допишет идентификатор по логину."""
    _check_role(role)
    login, full_name = login.strip().lower(), full_name.strip()
    if not login or not full_name:
        raise Invalid("логин и ФИО")
    if val(conn, "SELECT 1 FROM app_user WHERE lower(login) = %s", (login,)):
        raise Conflict("логин занят")
    if org_unit_id is not None and not val(conn, "SELECT 1 FROM org_unit WHERE id = %s", (org_unit_id,)):
        raise Invalid("подразделение")
    uid = val(conn, """INSERT INTO app_user (login, full_name, position, org_unit_id, role)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""", (login, full_name, position, org_unit_id, role))
    _set_override(conn, uid, role, by, now)
    _log(conn, by, now, f"заведён вручную: {login}, роль {role}", after={"user_id": uid, "role": role})
    return uid


def update(conn: psycopg.Connection, user_id: int, by: int, now: datetime, role: str | None = None,
           org_unit_id: int | None = None, manual: bool | None = None) -> dict:
    """S2: смена роли закрепляет её; manual=False снимает закрепление (дальше — по правилу выгрузки)."""
    u = one(conn, "SELECT id, login, role::text AS role, org_unit_id FROM app_user WHERE id = %s", (user_id,))
    if not u or u["role"] == "agent":
        raise NotFound
    if role is not None:
        _check_role(role)
        run(conn, "UPDATE app_user SET role = %s WHERE id = %s", (role, user_id))
        _set_override(conn, user_id, role, by, now)
    if org_unit_id is not None:
        if not val(conn, "SELECT 1 FROM org_unit WHERE id = %s", (org_unit_id,)):
            raise Invalid("подразделение")
        run(conn, "UPDATE app_user SET org_unit_id = %s WHERE id = %s", (org_unit_id, user_id))
    if manual is False:
        run(conn, "DELETE FROM ops.user_override WHERE user_id = %s", (user_id,))
    if role is not None and role != u["role"]:   # роль сменилась — прежние сессии с прежними правами гасим
        run(conn, "UPDATE ops.web_session SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
            (now, user_id))
    _log(conn, by, now, f"доступ изменён: {u['login']}",
         before={"role": u["role"], "org_unit_id": u["org_unit_id"]},
         after={"role": role or u["role"], "org_unit_id": org_unit_id or u["org_unit_id"], "manual": manual})
    return {"id": user_id}


def _code_hash(code: str) -> str:
    return hmac.new(auth._key(), code.upper().encode(), hashlib.sha256).hexdigest()


def issue_code(conn: psycopg.Connection, target_id: int, issuer: auth.User, now: datetime) -> dict:
    """S3: новый код гасит прежний живой; показывается один раз. Выдача — в audit_log."""
    t = one(conn, """SELECT u.id, u.role::text AS role, u.is_active, (p.user_id IS NOT NULL) AS has_password
                     FROM app_user u LEFT JOIN ops.user_password p ON p.user_id = u.id WHERE u.id = %s""",
            (target_id,))
    if not t or t["role"] == "agent":
        raise NotFound
    if not t["is_active"] or t["role"] == "no_access":
        raise Conflict("у человека нет доступа к порталу")
    if "access.manage" not in issuer.permissions and (t["role"] not in ("hiring_manager", "director")
                                                      or t["has_password"]):
        raise Forbidden("код этому человеку выдаёт руководитель отдела персонала")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    expires = now + timedelta(hours=int(setting(conn, "invite_code_hours", 72)))
    run(conn, """UPDATE ops.invite_code SET revoked_at = %s
                 WHERE user_id = %s AND used_at IS NULL AND revoked_at IS NULL""", (now, target_id))
    run(conn, """INSERT INTO ops.invite_code (user_id, code_hash, issued_by, issued_at, expires_at)
                 VALUES (%s, %s, %s, %s, %s)""", (target_id, _code_hash(code), issuer.id, now, expires))
    run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail)
                 VALUES (%s, 'invite', 'app_user', %s, %s)""", (issuer.id, target_id, j({"expires_at": str(expires)})))
    return {"code": code, "expires_at": expires}


def login_by_code(conn: psycopg.Connection, login: str, code: str, password: str, now: datetime) -> int | None:
    """S3: id при верном живом коде — пароль сохранён хешем, код погашен. None — любой отказ (M3).
    После login_max_failures ошибок подряд по коду — auth.LoginLocked."""
    hashed = auth._hash_password(password)   # PasswordError до проверки кода: короткий пароль не тратит попытку
    c = one(conn, """SELECT c.id, c.user_id, c.code_hash, c.expires_at, c.failed_count, c.locked_until, u.is_active
                     FROM app_user u JOIN ops.invite_code c ON c.user_id = u.id
                     WHERE lower(u.login) = lower(%s) AND c.used_at IS NULL AND c.revoked_at IS NULL
                     FOR UPDATE OF c""", (login.strip(),))
    if not c or not c["is_active"]:
        return None
    if c["locked_until"] and c["locked_until"] > now:
        raise auth.LoginLocked(max(1, int((c["locked_until"] - now).total_seconds() // 60) + 1))
    if c["expires_at"] <= now:
        return None
    if not hmac.compare_digest(c["code_hash"], _code_hash(code.strip())):
        failed, lock = c["failed_count"] + 1, None
        if failed >= int(setting(conn, "login_max_failures", 5)):
            lock, failed = now + timedelta(minutes=int(setting(conn, "login_lock_minutes", 15))), 0
        run(conn, "UPDATE ops.invite_code SET failed_count = %s, locked_until = %s WHERE id = %s",
            (failed, lock, c["id"]))
        return None
    run(conn, "UPDATE ops.invite_code SET used_at = %s WHERE id = %s", (now, c["id"]))
    run(conn, """INSERT INTO ops.user_password (user_id, hash) VALUES (%s, %s)
                 ON CONFLICT (user_id) DO UPDATE SET hash = EXCLUDED.hash, changed_at = now(),
                     failed_count = 0, locked_until = NULL""", (c["user_id"], hashed))
    return c["user_id"]
