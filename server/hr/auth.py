"""Вход в рабочее место: сессия, права по role_permission, CSRF (раздел 10 ТЗ, security.md).

В cookie — случайный токен с подписью HMAC от SESSION_KEY, в базе только хеши токена и CSRF.
Права проверяет сервер: каждый маршрут /api/ объявляет require(<право>), интерфейс только прячет.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import urlparse

import bcrypt
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute

from .clock import SYSTEM, Clock
from .config import get_config
from .db import ConnFactory, one, rows, run, setting
from .deps import get_clock, get_conn_factory

COOKIE = "hr_session"
CSRF_HEADER = "X-CSRF-Token"
SESSION_ONLY = "session"            # право «любой вошедший»: /api/me, выход
OPEN_ROUTES = {"/api/login", "/api/oauth/hh/callback"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class User:
    id: int
    login: str
    full_name: str
    role: str
    permissions: frozenset[str]
    token_hash: str


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _key() -> bytes:
    k = get_config().session_key
    if not k:
        raise HTTPException(503, {"error": "not_configured", "message": "SESSION_KEY не задан в .env"})
    return k.encode()


def _sign(token: str) -> str:
    return hmac.new(_key(), token.encode(), hashlib.sha256).hexdigest()


def permissions(conn: psycopg.Connection, role: str) -> frozenset[str]:
    return frozenset(r["permission"] for r in rows(conn, "SELECT permission FROM role_permission WHERE role = %s",
                                                     (role,)))


def create_session(conn: psycopg.Connection, user_id: int, now: datetime) -> tuple[str, str]:
    """Новая сессия: (значение cookie, CSRF-токен для заголовка)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    days = int(setting(conn, "session_max_days", 7))
    run(conn, """INSERT INTO ops.web_session (token_hash, user_id, csrf_hash, created_at, last_seen_at, expires_at)
                 VALUES (%s, %s, %s, %s, %s, %s)""",
        (_sha(token), user_id, _sha(csrf), now, now, now + timedelta(days=days)))
    return f"{token}.{_sign(token)}", csrf


def _token(cookie: str | None) -> str | None:
    if not cookie or "." not in cookie:
        return None
    token, sig = cookie.rsplit(".", 1)
    return token if hmac.compare_digest(sig, _sign(token)) else None


def load_session(conn: psycopg.Connection, cookie: str | None, now: datetime) -> tuple[User, str] | None:
    """Пользователь и хеш CSRF живой сессии; продлевает простой. None — сессии нет."""
    token = _token(cookie)
    if not token:
        return None
    s = one(conn, """SELECT s.*, u.login, u.full_name, u.role::text AS role, u.is_active
                     FROM ops.web_session s JOIN app_user u ON u.id = s.user_id
                     WHERE s.token_hash = %s FOR UPDATE OF s""", (_sha(token),))
    idle = timedelta(hours=int(setting(conn, "session_idle_hours", 12)))
    if not s or s["revoked_at"] or not s["is_active"] or s["expires_at"] <= now or s["last_seen_at"] + idle <= now:
        return None
    run(conn, "UPDATE ops.web_session SET last_seen_at = %s WHERE token_hash = %s", (now, s["token_hash"]))
    user = User(s["user_id"], s["login"], s["full_name"], s["role"], permissions(conn, s["role"]), s["token_hash"])
    return user, s["csrf_hash"]


def revoke(conn: psycopg.Connection, cookie: str | None, now: datetime) -> None:
    token = _token(cookie)
    if token:
        run(conn, "UPDATE ops.web_session SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL",
            (now, _sha(token)))


# ------------------------------------------------------------- пароль

PASSWORD_MIN = 10
BCRYPT_COST = 12


class PasswordError(ValueError):
    pass


class LoginLocked(RuntimeError):
    def __init__(self, minutes: int):
        super().__init__(minutes)
        self.minutes = minutes


def _hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN:
        raise PasswordError(f"пароль не короче {PASSWORD_MIN} знаков")
    if len(password.encode()) > 72:
        raise PasswordError("пароль длиннее 72 байт: bcrypt его не примет")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_COST)).decode()


def create_user(conn: psycopg.Connection, login: str, full_name: str, role: str, password: str) -> int:
    """Временная учётная запись до входа из 1С (Решения): пользователь и хеш пароля."""
    login = login.strip().lower()   # логин без учёта регистра: «Vadim» и «vadim» — одна запись
    uid = one(conn, """INSERT INTO app_user (login, full_name, role) VALUES (%s, %s, %s)
                       ON CONFLICT (login) DO UPDATE SET full_name = EXCLUDED.full_name, role = EXCLUDED.role
                       RETURNING id""", (login, full_name, role))["id"]
    run(conn, """INSERT INTO ops.user_password (user_id, hash) VALUES (%s, %s)
                 ON CONFLICT (user_id) DO UPDATE SET hash = EXCLUDED.hash, changed_at = now(),
                     failed_count = 0, locked_until = NULL""", (uid, _hash_password(password)))
    return uid


def check_password(conn: psycopg.Connection, login: str, password: str, now: datetime | None = None) -> int | None:
    """id пользователя при верном пароле; None — неверно (логин и пароль не различаются).
    После login_max_failures ошибок подряд — LoginLocked на login_lock_minutes."""
    now = now or SYSTEM.now()
    r = one(conn, """SELECT u.id, u.is_active, p.hash, p.failed_count, p.locked_until
                     FROM app_user u JOIN ops.user_password p ON p.user_id = u.id
                     WHERE lower(u.login) = lower(%s) FOR UPDATE OF p""", (login.strip(),))
    if not r or not r["is_active"]:
        return None
    if r["locked_until"] and r["locked_until"] > now:
        raise LoginLocked(max(1, int((r["locked_until"] - now).total_seconds() // 60) + 1))
    raw = password.encode()
    if len(raw) <= 72 and bcrypt.checkpw(raw, r["hash"].encode()):
        run(conn, "UPDATE ops.user_password SET failed_count = 0, locked_until = NULL WHERE user_id = %s", (r["id"],))
        return r["id"]
    failed = r["failed_count"] + 1
    lock = None
    if failed >= int(setting(conn, "login_max_failures", 5)):
        lock, failed = now + timedelta(minutes=int(setting(conn, "login_lock_minutes", 15))), 0
    run(conn, "UPDATE ops.user_password SET failed_count = %s, locked_until = %s WHERE user_id = %s",
        (failed, lock, r["id"]))
    return None


def rotate_csrf(conn: psycopg.Connection, token_hash: str) -> str:
    """Новый CSRF-токен сессии: в базе только хеш, поэтому после перезагрузки страницы выдаётся новый."""
    csrf = secrets.token_urlsafe(32)
    run(conn, "UPDATE ops.web_session SET csrf_hash = %s WHERE token_hash = %s", (_sha(csrf), token_hash))
    return csrf


def same_origin(request: Request) -> bool:
    return _same_origin(request)


def _same_origin(request: Request) -> bool:
    src = request.headers.get("origin") or request.headers.get("referer")
    return bool(src) and urlparse(src).hostname in get_config().web_hosts


def _check_csrf(request: Request, csrf_hash: str) -> None:
    sent = request.headers.get(CSRF_HEADER, "")
    if not (sent and hmac.compare_digest(_sha(sent), csrf_hash) and _same_origin(request)):
        raise HTTPException(403, {"error": "csrf", "message": "Запрос отклонён: обновите страницу и повторите"})


def require(permission: str, csrf: bool | None = None):
    """Зависимость маршрута: живая сессия, право и, для изменяющих запросов, CSRF.
    csrf=True — проверять и на GET (выгрузка досье)."""
    def dependency(request: Request,
                   conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
                   clock: Annotated[Clock, Depends(get_clock)]) -> User:
        with conn_factory() as conn:
            found = load_session(conn, request.cookies.get(COOKIE), clock.now())
        if not found:
            raise HTTPException(401, {"error": "auth", "message": "Войдите в рабочее место"})
        user, csrf_hash = found
        if permission != SESSION_ONLY and permission not in user.permissions:
            raise HTTPException(403, {"error": "forbidden", "message": "Недостаточно прав"})
        if csrf or (csrf is None and request.method not in SAFE_METHODS):
            _check_csrf(request, csrf_hash)
        return user

    dependency.permission = permission
    return dependency


def routes_without_permission(app: FastAPI) -> list[str]:
    """Маршруты /api/, у которых не объявлено право: тест держит этот список пустым (S3)."""
    def declared(dep) -> bool:
        return getattr(dep.call, "permission", None) is not None or any(declared(d) for d in dep.dependencies)

    bad = []
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/") and r.path not in OPEN_ROUTES \
                and not declared(r.dependant):
            bad += [f"{m} {r.path}" for m in sorted(r.methods)]
    return bad
