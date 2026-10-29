"""Общие фикстуры: настоящий PostgreSQL, откат на каждый тест, без сети.

База берётся из DATABASE_URL и обязана оканчиваться на `_test`: фикстура
пересоздаёт схемы public и ops, на рабочей базе это уничтожило бы данные.
"""
from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from hr import db
from hr.clock import FakeClock

DSN = os.environ.get("DATABASE_URL", "")


def _db_host() -> set[str]:
    u = urlparse(DSN)
    return {u.hostname or "localhost", "127.0.0.1", "localhost", "::1"}


@pytest.fixture(scope="session", autouse=True)
def _schema():
    name = urlparse(DSN).path.lstrip("/")
    if not name.endswith("_test"):
        pytest.exit(f"DATABASE_URL должен указывать на базу *_test, сейчас: {name or 'не задан'}", 2)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS ops CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
    db.migrate(DSN)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Реальные соединения разрешены только с базой: внешний HTTP — через заглушки."""
    allowed = _db_host()
    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if self.family == socket.AF_UNIX or host in allowed:
            return real(self, address)
        raise RuntimeError(f"тест пытается выйти в сеть: {address}")

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def conn():
    """Одно соединение на тест; всё, что тест записал, откатывается."""
    c = psycopg.connect(DSN, row_factory=dict_row, autocommit=False)
    # открываем транзакцию сразу: иначе первый conn.transaction() в тесте станет настоящей
    # транзакцией и зафиксирует данные, а не точкой сохранения
    c.execute("SELECT 1")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def conn_factory(conn):
    """Та же фабрика транзакций, что db.tx, но на точках сохранения общего соединения."""

    @contextmanager
    def factory():
        with conn.transaction():
            yield conn

    return factory


@pytest.fixture
def clock():
    return FakeClock()


ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture
def api(conn, conn_factory, clock, monkeypatch):
    """Клиент рабочего места под руководителем подбора: (client, headers для POST, user_id).
    Роль меняется вызовом api_as(role)."""
    from fastapi.testclient import TestClient

    from hr import auth, config
    from hr.main import app, get_clock, get_conn_factory

    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    app.dependency_overrides[get_conn_factory] = lambda: conn_factory
    app.dependency_overrides[get_clock] = lambda: clock

    def as_role(role="head_recruiter", login=None):
        login = login or role
        uid = db.val(conn, """INSERT INTO app_user (login, full_name, role) VALUES (%s, %s, %s)
                              ON CONFLICT (login) DO UPDATE SET role = EXCLUDED.role RETURNING id""",
                     (login, f"Проверочный {role}", role))
        cookie, csrf = auth.create_session(conn, uid, clock.now())
        c = TestClient(app, base_url="https://localhost:8000")
        c.cookies.set(auth.COOKIE, cookie)
        return c, {**ORIGIN, auth.CSRF_HEADER: csrf}, uid

    yield as_role
    app.dependency_overrides.clear()
    config.get_config.cache_clear()
