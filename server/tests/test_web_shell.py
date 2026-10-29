"""T3 workplace: оболочка сервера — заголовки, формат ошибок, хост, журнал без query (S2, S35)."""
import logging

import pytest
from fastapi.testclient import TestClient

from hr import auth, config, web
from hr.main import app


@pytest.fixture(autouse=True)
def session_key(monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


@pytest.fixture
def client():
    return TestClient(app, base_url="https://localhost:8000", raise_server_exceptions=False)


def test_security_headers_on_page_and_api(client):
    for path in ("/", "/api/me"):
        h = client.get(path).headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"


def test_index_served_without_data(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_error_format_auth(client):
    r = client.get("/api/me")
    assert r.status_code == 401 and set(r.json()) == {"error", "message"}


def test_error_format_validation(client):
    r = client.post("/api/login", json={"login": "x"}, headers={"Origin": "http://localhost:8000"})
    assert r.status_code == 422 and r.json()["error"] == "validation" and r.json()["message"]


def test_error_format_not_found(client):
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and set(r.json()) == {"error", "message"}


def test_foreign_host_rejected():
    r = TestClient(app, base_url="https://evil.example").get("/")
    assert r.status_code == 400


def test_unexpected_error_hides_details(monkeypatch, client):
    def boom(*a, **k):
        raise RuntimeError("секрет из базы")

    monkeypatch.setattr(auth, "load_session", boom)
    client.cookies.set(auth.COOKIE, "a.b")
    r = client.get("/api/me")
    assert r.status_code == 500 and "секрет" not in r.text and r.json()["error"] == "internal"


def test_access_log_drops_query():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/api/search?q=Иванов", "1.1", 200), None)
    assert web.DropQuery().filter(rec)
    assert "Иванов" not in rec.getMessage() and "/api/search" in rec.getMessage()
