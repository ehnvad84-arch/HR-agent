"""T2b workplace: вход по паролю, выход, блокировка (S1)."""
import pytest
from fastapi.testclient import TestClient

from hr import auth, cli, config, db
from hr.main import app, get_clock, get_conn_factory

ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture(autouse=True)
def session_key(monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


@pytest.fixture
def client(conn_factory, clock):
    app.dependency_overrides[get_conn_factory] = lambda: conn_factory
    app.dependency_overrides[get_clock] = lambda: clock
    yield TestClient(app, base_url="https://localhost:8000")
    app.dependency_overrides.clear()


@pytest.fixture
def boss(conn):
    return auth.create_user(conn, "boss", "Руководитель подбора", "head_recruiter", "верный-пароль-1")


def login(c, password="верный-пароль-1", user="boss"):
    return c.post("/api/login", json={"login": user, "password": password}, headers=ORIGIN)


def test_s01_login_sets_cookie_and_returns_me(conn, boss, client):
    r = login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["login"] == "boss" and "profile.approve" in body["permissions"] and body["csrf"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["user"]["login"] == "boss"


def test_password_only_as_bcrypt_hash(conn, boss):
    h = db.val(conn, "SELECT hash FROM ops.user_password WHERE user_id = %s", (boss,))
    assert h.startswith("$2") and "верный" not in h


def test_wrong_login_and_wrong_password_same_answer(conn, boss, client):
    a, b = login(client, password="не тот"), login(client, user="никто")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_lock_after_failures(conn, boss, client, clock):
    for _ in range(5):
        login(client, password="не тот")
    r = login(client)
    assert r.status_code == 429, "верный пароль во время блокировки не пускает"
    clock.advance(seconds=16 * 60)
    assert login(client).status_code == 200
    assert db.val(conn, "SELECT failed_count FROM ops.user_password WHERE user_id = %s", (boss,)) == 0


def test_inactive_user_cannot_login(conn, boss, client):
    db.run(conn, "UPDATE app_user SET is_active = false WHERE id = %s", (boss,))
    assert login(client).status_code == 401


def test_logout_revokes(conn, boss, client):
    csrf = login(client).json()["csrf"]
    assert client.post("/api/logout", headers={**ORIGIN, auth.CSRF_HEADER: csrf}).status_code == 200
    assert client.get("/api/me").status_code == 401


def test_login_requires_same_origin(conn, boss, client):
    r = client.post("/api/login", json={"login": "boss", "password": "верный-пароль-1"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_cli_user_create_asks_password_without_echo(conn_factory, conn, monkeypatch, capsys):
    answers = iter(["пароль-из-ввода-1", "пароль-из-ввода-1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.db, "tx", conn_factory)
    assert cli.main(["user-create", "rec", "--name", "Рекрутер Проверочный", "--role", "recruiter"]) == 0
    uid = db.val(conn, "SELECT id FROM app_user WHERE login = 'rec'")
    assert auth.check_password(conn, "rec", "пароль-из-ввода-1") == uid
    assert "пароль-из-ввода" not in capsys.readouterr().out
