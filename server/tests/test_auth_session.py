"""T2 workplace: сессия, права по role_permission, CSRF (S2, S3; пункты 32, 48)."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from hr import auth, config, db
from hr.main import get_clock, get_conn_factory

ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture(autouse=True)
def session_key(monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


def user(conn, role="head_recruiter", login="boss", active=True) -> int:
    return db.val(conn, """INSERT INTO app_user (login, full_name, role, is_active)
                           VALUES (%s, 'Проверочный', %s, %s) RETURNING id""", (login, role, active))


@pytest.fixture
def web(conn_factory, clock):
    app = FastAPI()

    @app.get("/api/read")
    def read(u: Annotated[auth.User, Depends(auth.require("base.read"))]):
        return {"login": u.login}

    @app.post("/api/open")
    def open_(u: Annotated[auth.User, Depends(auth.require("contact.open"))]):
        return {"ok": True}

    app.dependency_overrides[get_conn_factory] = lambda: conn_factory
    app.dependency_overrides[get_clock] = lambda: clock
    return app, TestClient(app, base_url="http://localhost:8000")


def login_as(conn, clock, client, role="head_recruiter", **kw):
    uid = user(conn, role=role, **kw)
    cookie, csrf = auth.create_session(conn, uid, clock.now())
    client.cookies.set(auth.COOKIE, cookie)
    return uid, csrf


def test_s02_no_session_is_401(web):
    _, c = web
    r = c.get("/api/read")
    assert r.status_code == 401 and "login" not in r.text


def test_s02_forged_cookie_is_401(conn, clock, web):
    _, c = web
    login_as(conn, clock, c)
    token = c.cookies.get(auth.COOKIE).split(".")[0]
    c.cookies.set(auth.COOKIE, token + ".forged")
    assert c.get("/api/read").status_code == 401


def test_session_ok_and_cookie_holds_no_user_data(conn, clock, web):
    _, c = web
    login_as(conn, clock, c)
    r = c.get("/api/read")
    assert r.status_code == 200 and r.json()["login"] == "boss"
    assert "boss" not in c.cookies.get(auth.COOKIE)
    assert db.val(conn, "SELECT count(*) FROM ops.web_session") == 1


def test_idle_session_expires(conn, clock, web):
    _, c = web
    login_as(conn, clock, c)
    clock.advance(seconds=13 * 3600)
    assert c.get("/api/read").status_code == 401


def test_revoked_and_inactive_user_are_401(conn, clock, web):
    _, c = web
    uid, _ = login_as(conn, clock, c)
    db.run(conn, "UPDATE app_user SET is_active = false WHERE id = %s", (uid,))
    assert c.get("/api/read").status_code == 401
    db.run(conn, "UPDATE app_user SET is_active = true WHERE id = %s", (uid,))
    auth.revoke(conn, c.cookies.get(auth.COOKIE), clock.now())
    assert c.get("/api/read").status_code == 401


def test_s03_no_permission_is_403(conn, clock, web):
    _, c = web
    _, csrf = login_as(conn, clock, c, role="hiring_manager", login="nach")
    assert c.get("/api/read").status_code == 200
    r = c.post("/api/open", headers={**ORIGIN, auth.CSRF_HEADER: csrf})
    assert r.status_code == 403, "у нанимающего руководителя нет открытия контактов (48)"


def test_csrf_required_on_post(conn, clock, web):
    _, c = web
    _, csrf = login_as(conn, clock, c)
    assert c.post("/api/open", headers=ORIGIN).status_code == 403
    assert c.post("/api/open", headers={**ORIGIN, auth.CSRF_HEADER: "alien"}).status_code == 403
    assert c.post("/api/open", headers={"Origin": "https://evil.example", auth.CSRF_HEADER: csrf}).status_code == 403
    assert c.post("/api/open", headers={auth.CSRF_HEADER: csrf}).status_code == 403, "без Origin и Referer"
    assert c.post("/api/open", headers={**ORIGIN, auth.CSRF_HEADER: csrf}).status_code == 200
    assert c.post("/api/open", headers={"Referer": "http://127.0.0.1:8000/", auth.CSRF_HEADER: csrf}).status_code == 200


def test_session_max_age(conn, clock, web):
    _, c = web
    login_as(conn, clock, c)
    for _ in range(8):   # заходит каждый день, но больше session_max_days сессия не живёт
        clock.advance(days=1)
        c.get("/api/read")
    assert c.get("/api/read").status_code == 401


def test_route_without_permission_is_found():
    app = FastAPI()

    @app.get("/api/bare")
    def bare():
        return {}

    @app.get("/api/guarded")
    def guarded(u: Annotated[auth.User, Depends(auth.require("base.read"))]):
        return {}

    assert auth.routes_without_permission(app) == ["GET /api/bare"]


def test_every_app_route_declares_permission():
    """S3: у каждого маршрута /api/ рабочего места есть право; открыты только вход и OAuth hh."""
    from hr.main import app
    assert auth.routes_without_permission(app) == []


def test_session_key_required(conn, clock, monkeypatch, web):
    _, c = web
    login_as(conn, clock, c)
    monkeypatch.setenv("SESSION_KEY", "")
    config.get_config.cache_clear()
    assert c.get("/api/read").status_code == 503
