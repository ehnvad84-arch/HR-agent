"""Сбор по остатку дневного лимита hh: ежедневно в 18:00 и кнопкой (S43, S44)."""
import httpx
import pytest
from cryptography.fernet import Fernet
from factories import hh_item, profile, weak_item
from test_collect_hh import FakeHH

from hr import collect, config, db, hh, tasks
from hr.http import RateLimiter


@pytest.fixture(autouse=True)
def hh_env(monkeypatch, conn, clock):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    hh.store_tokens(conn, {"access_token": "AT", "refresh_token": "RT", "expires_in": 30 * 86400}, "777",
                    clock.now())
    yield
    config.get_config.cache_clear()


class Limited(FakeHH):
    """Остаток просмотров на сегодня — left; три сильных резюме в выдаче."""

    def __init__(self, left):
        exp = lambda i: [{"company": f"Завод {i}", "position": "Инженер-электронщик", "start": "2018-01-01",  # noqa: E731
                          "end": None}]
        super().__init__(items=[hh_item(id=f"s{i}", experience=exp(i), area={"id": "53", "name": f"Город {i}"})
                                for i in range(3)] + [weak_item()])
        self.left = left

    def __call__(self, req):
        if req.url.path.endswith("/limits/resume"):
            self.calls.append(req.url.path)
            return httpx.Response(200, json={"limits": {"resume_view": 500}, "spend": {"resume_view": 500 - self.left},
                                             "left": {"resume_view": self.left}})
        return super().__call__(req)


def test_limits_today(conn_factory, clock):
    lim = hh.limits_today(conn_factory, Limited(37).client(), clock.now(), limiter=RateLimiter(clock))
    assert lim == {"views_left": 37, "package_left": 9000}


def test_s43_daily_uses_remaining_limit(conn, conn_factory, clock):
    profile(conn)
    fake = Limited(2)
    st = collect.collect_hh_daily(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))
    assert len(fake.full_fetches) == 2, "полных резюме ровно на остаток лимита"
    assert st.extra["views_left"] == 2
    assert "collect_hh_daily" in tasks.TASKS


def test_s43_nothing_left_does_nothing(conn, conn_factory, clock):
    profile(conn)
    fake = Limited(0)
    st = collect.collect_hh_daily(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))
    assert fake.full_fetches == [] and "/resumes" not in fake.calls and "лимит" in st.note


def client_with(api, fake, role="head_recruiter"):
    from hr.deps import get_hh_http
    from hr.main import app
    app.dependency_overrides[get_hh_http] = fake.client
    return api(role)


def test_s44_limits_endpoint_and_permission(conn, api):
    fake = Limited(15)
    c, _, _ = client_with(api, fake)
    assert c.get("/api/hh/limits").json() == {"views_left": 15, "package_left": 9000}
    r, _, _ = client_with(api, fake, "recruiter")
    assert r.get("/api/hh/limits").status_code == 403


def test_s44_manual_collect_capped_by_limit(conn, api):
    profile(conn)
    fake = Limited(1)
    c, h, _ = client_with(api, fake)
    assert c.post("/api/hh/collect", json={"count": 5}, headers=h).status_code == 422, "больше остатка"
    assert c.post("/api/hh/collect", json={"count": 0}, headers=h).status_code == 422
    r = c.post("/api/hh/collect", json={"count": 1}, headers=h)
    assert r.status_code == 202 and r.json()["count"] == 1
    assert len(fake.full_fetches) == 1
    assert db.val(conn, "SELECT ok FROM ops.job_run WHERE job = 'collect_hh' ORDER BY id DESC LIMIT 1")
