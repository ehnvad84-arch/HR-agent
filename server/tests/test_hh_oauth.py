"""T12: OAuth hh, токены зашифрованы (S5)."""
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from hr import config, db, hh, jobs
from hr.main import app, get_clock, get_conn_factory, get_hh_http


@pytest.fixture
def hh_env(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("HH_CLIENT_ID", "cid")
    monkeypatch.setenv("HH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("HH_EMPLOYER_ID", "777")
    monkeypatch.setenv("HH_REDIRECT_URI", "https://hr.test/api/oauth/hh/callback")
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


class FakeHH:
    def __init__(self, employer="777", refresh_ok=True):
        self.employer, self.refresh_ok, self.calls = employer, refresh_ok, []
        self.n = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, dict(parse_qs(req.content.decode())) if req.content else {}))
        if req.url.path == "/token":
            form = parse_qs(req.content.decode())
            if form["grant_type"] == ["refresh_token"] and not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"AT{self.n}", "refresh_token": f"RT{self.n}",
                                             "expires_in": 1209600, "token_type": "bearer"})
        if req.url.path == "/me":
            return httpx.Response(200, json={"is_employer": True, "employer": {"id": self.employer}})
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self), base_url="https://api.hh.ru")


@pytest.fixture
def api(conn_factory, clock):
    fake = FakeHH()
    app.dependency_overrides[get_conn_factory] = lambda: conn_factory
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_hh_http] = fake.client
    yield TestClient(app, base_url="https://localhost:8000"), fake
    app.dependency_overrides.clear()


def state_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def test_s05_no_token_is_not_configured(conn, hh_env, clock):
    with pytest.raises(jobs.JobNotConfigured, match="авторизац"):
        hh.access_token(conn, FakeHH().client(), clock.now())


def test_auth_link_stores_only_state_hash(conn, hh_env, clock):
    url = hh.auth_link(conn, clock.now())
    st = state_of(url)
    assert url.startswith("https://hh.ru/oauth/authorize?") and "client_id=cid" in url
    rows = db.rows(conn, "SELECT * FROM ops.oauth_state")
    assert len(rows) == 1 and rows[0]["state_hash"] != st and len(rows[0]["state_hash"]) == 64


def test_callback_stores_encrypted_tokens(conn, hh_env, api, clock):
    client, fake = api
    st = state_of(hh.auth_link(conn, clock.now()))
    r = client.get("/api/oauth/hh/callback", params={"code": "c1", "state": st})
    assert r.status_code == 200 and "AT1" not in r.text and "RT1" not in r.text
    row = db.one(conn, "SELECT * FROM ops.oauth_token WHERE provider = 'hh'")
    assert row and b"AT1" not in bytes(row["access_token_enc"]) and row["account_id"] == "777"
    assert hh.access_token(conn, fake.client(), clock.now()) == "AT1"
    leak = json.dumps([db.rows(conn, "SELECT * FROM settings"), db.rows(conn, "SELECT * FROM agent_change"),
                       db.rows(conn, "SELECT * FROM ops.job_run")], default=str)
    assert "AT1" not in leak and "RT1" not in leak


def test_callback_state_reused_or_unknown_is_400(conn, hh_env, api, clock):
    client, _ = api
    st = state_of(hh.auth_link(conn, clock.now()))
    assert client.get("/api/oauth/hh/callback", params={"code": "c1", "state": st}).status_code == 200
    assert client.get("/api/oauth/hh/callback", params={"code": "c1", "state": st}).status_code == 400
    assert client.get("/api/oauth/hh/callback", params={"code": "c1", "state": "nope"}).status_code == 400


def test_callback_expired_state_is_400(conn, hh_env, api, clock):
    client, _ = api
    st = state_of(hh.auth_link(conn, clock.now() - timedelta(minutes=11)))
    assert client.get("/api/oauth/hh/callback", params={"code": "c1", "state": st}).status_code == 400


def test_callback_wrong_employer_is_403(conn, hh_env, conn_factory, clock):
    fake = FakeHH(employer="123")
    app.dependency_overrides[get_conn_factory] = lambda: conn_factory
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_hh_http] = fake.client
    try:
        st = state_of(hh.auth_link(conn, clock.now()))
        r = TestClient(app, base_url="https://localhost:8000").get("/api/oauth/hh/callback",
                                                                   params={"code": "c1", "state": st})
        assert r.status_code == 403
        assert db.val(conn, "SELECT count(*) FROM ops.oauth_token") == 0
    finally:
        app.dependency_overrides.clear()


def test_expired_access_token_is_refreshed_once_and_rotated(conn, hh_env, clock):
    fake = FakeHH()
    hh.store_tokens(conn, {"access_token": "OLD", "refresh_token": "R0", "expires_in": 60}, "777", clock.now())
    later = clock.now() + timedelta(minutes=5)
    assert hh.access_token(conn, fake.client(), later) == "AT1"
    assert [c[1] for c in fake.calls] == ["/token"]
    assert fake.calls[0][2]["refresh_token"] == ["R0"]
    assert hh.access_token(conn, fake.client(), later) == "AT1", "свежий токен не обновляется повторно"
    assert len(fake.calls) == 1


def test_refresh_failure_is_not_configured(conn, hh_env, clock):
    hh.store_tokens(conn, {"access_token": "OLD", "refresh_token": "R0", "expires_in": 60}, "777", clock.now())
    with pytest.raises(jobs.JobNotConfigured):
        hh.access_token(conn, FakeHH(refresh_ok=False).client(), clock.now() + timedelta(hours=1))


def test_missing_secrets_key_is_not_configured(conn, monkeypatch, clock):
    monkeypatch.delenv("SECRETS_KEY", raising=False)
    config.get_config.cache_clear()
    try:
        with pytest.raises(jobs.JobNotConfigured, match="SECRETS_KEY"):
            hh.store_tokens(conn, {"access_token": "A", "expires_in": 60}, "777", clock.now())
    finally:
        config.get_config.cache_clear()
