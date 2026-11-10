"""Доступ к PostgreSQL. Вся запись в базу идёт через этот пакет (AD-1)."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import get_config

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schema_recruiting.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Фабрика транзакций: в работе — db.tx, в тестах — точки сохранения одного соединения
ConnFactory = Callable[[], AbstractContextManager[psycopg.Connection]]

_pool: ConnectionPool | None = None


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


def setting(conn: psycopg.Connection, key: str, default: Any = None) -> Any:
    r = one(conn, "SELECT value FROM settings WHERE key = %s", (key,))
    return r["value"] if r else default


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _scalar(conn: psycopg.Connection, sql: str) -> list[Any]:
    """Первый столбец всех строк — при любой фабрике строк соединения."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]


def applied(conn: psycopg.Connection) -> set[str]:
    """Имена применённых миграций; журнала ещё нет — пусто."""
    if not _scalar(conn, "SELECT to_regclass('ops.schema_migration') IS NOT NULL")[0]:
        return set()
    return set(_scalar(conn, "SELECT name FROM ops.schema_migration"))


def pending(conn: psycopg.Connection) -> list[str]:
    """Что применит migrate(), по порядку: основная схема, если её нет, и неприменённые миграции."""
    todo = [] if _scalar(conn, "SELECT to_regclass('public.person') IS NOT NULL")[0] else [SCHEMA_FILE.name]
    done = applied(conn)
    return todo + [f.name for f in migration_files() if f.name not in done]


def migrate(dsn: str | None = None) -> list[str]:
    """Основная схема, если её нет, затем миграции из server/migrations по порядку. Повтор безопасен."""
    done: list[str] = []
    with psycopg.connect(dsn or get_config().database_url, autocommit=True) as conn:
        todo = set(pending(conn))
        if SCHEMA_FILE.name in todo:
            conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
            done.append(SCHEMA_FILE.name)
        conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
        conn.execute("""CREATE TABLE IF NOT EXISTS ops.schema_migration (
                            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        for f in migration_files():
            if f.name not in todo:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO ops.schema_migration (name) VALUES (%s)", (f.name,))
            done.append(f.name)
    return done
