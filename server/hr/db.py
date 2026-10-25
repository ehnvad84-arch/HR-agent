"""Доступ к PostgreSQL. Вся запись в базу идёт через этот модуль (раздел 2 ТЗ)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import get_config

_pool: ConnectionPool | None = None

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schema_recruiting.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_config().database_url, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row, "autocommit": False}, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    """Одна транзакция: всё или ничего."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def rows(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def one(conn: psycopg.Connection, sql: str, params: Any = None) -> dict | None:
    r = rows(conn, sql, params)
    return r[0] if r else None


def val(conn: psycopg.Connection, sql: str, params: Any = None) -> Any:
    r = one(conn, sql, params)
    return next(iter(r.values())) if r else None


def run(conn: psycopg.Connection, sql: str, params: Any = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def j(v: Any) -> Jsonb:
    return Jsonb(v)


# ------------------------------------------------------------------ настройки

def setting(conn: psycopg.Connection, key: str, default: Any = None) -> Any:
    r = one(conn, "SELECT value FROM settings WHERE key = %s", (key,))
    return r["value"] if r else default


def set_setting(conn: psycopg.Connection, key: str, value: Any, user_id: int | None, note: str = "") -> None:
    """Любая смена настройки попадает в журнал изменений системы."""
    before = setting(conn, key)
    run(conn, """INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, now())
                 ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (key, j(value)))
    if before != value:
        run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, note)
                     VALUES ('threshold', %s, %s, %s, %s)""",
            (j({key: before}), j({key: value}), user_id, note or f"настройка {key}"))


def agent_user_id(conn: psycopg.Connection) -> int:
    return val(conn, "SELECT id FROM app_user WHERE login = 'agent'")


def audit(conn: psycopg.Connection, user_id: int | None, action: str, object_type: str,
          object_id: int | None, **detail: Any) -> None:
    run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail)
                 VALUES (%s, %s, %s, %s, %s)""", (user_id, action, object_type, object_id, j(detail)))


def notify(conn: psycopg.Connection, user_id: int, kind: str, body: dict) -> None:
    run(conn, "INSERT INTO notification (user_id, kind, body) VALUES (%s, %s, %s)", (user_id, kind, j(body)))


def notify_role(conn: psycopg.Connection, role: str, kind: str, body: dict) -> int:
    users = rows(conn, "SELECT id FROM app_user WHERE role = %s AND is_active", (role,))
    for u in users:
        notify(conn, u["id"], kind, body)
    return len(users)


# ------------------------------------------------------------------ схема

def apply_schema(dsn: str | None = None) -> list[str]:
    """Накатывает основную схему (если её нет) и миграции MVP. Повторный запуск безопасен."""
    applied: list[str] = []
    with psycopg.connect(dsn or get_config().database_url, autocommit=True) as conn:
        has_schema = conn.execute("SELECT to_regclass('public.person') IS NOT NULL").fetchone()[0]
        if not has_schema:
            conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
            applied.append(SCHEMA_FILE.name)
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migration (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migration").fetchall()}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
    return applied


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)
