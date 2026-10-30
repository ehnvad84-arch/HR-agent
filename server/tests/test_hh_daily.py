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
        self.queries = []

    queries: list

    def __call__(self, req):
        if req.url.path == "/resumes":
            self.queries.append(dict(req.url.params))
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


def test_s46_collect_one_profile_in_chosen_area(conn, conn_factory, clock):
    eng = profile(conn)
    profile(conn, code="otk", title="Инженер ОТК", dept="qc")
    fake = Limited(1)
    collect.collect_hh_daily(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock), count=1,
                             profile_id=eng["id"], area="1438")
    searches = [c for c in fake.calls if c == "/resumes"]
    assert len(searches) >= 1
    assert all(q["area"] == "1438" and "инженер-электронщик" in q["text"].lower() for q in fake.queries)


def test_s46_api_collect_with_profile_and_area(conn, api):
    eng = profile(conn)
    fake = Limited(1)
    c, h, _ = client_with(api, fake)
    r = c.post("/api/hh/collect", json={"count": 1, "role_profile_id": eng["id"], "area_id": "53"}, headers=h)
    assert r.status_code == 202 and r.json()["area_id"] == "53"
    assert c.post("/api/hh/collect", json={"count": 1, "area_id": "53; drop"}, headers=h).status_code == 422
    assert c.post("/api/hh/collect", json={"count": 1, "role_profile_id": 999999}, headers=h).status_code == 422


def test_s46_areas_defaults_and_suggest(conn, api):
    class Suggest(Limited):
        def __call__(self, req):
            if req.url.path == "/suggests/areas":
                assert req.url.params["text"] == "Соч"
                return httpx.Response(200, json={"items": [{"id": "237", "text": "Сочи"}]})
            return super().__call__(req)

    c, _, _ = client_with(api, Suggest(5))
    base = c.get("/api/hh/areas").json()["items"]
    assert [a["id"] for a in base] == ["113", "1438", "53"]
    assert c.get("/api/hh/areas", params={"q": "Соч"}).json()["items"] == [{"id": "237", "name": "Сочи"}]


def test_s48_progress_visible_in_status(conn, conn_factory, clock, api):
    from hr import jobs
    profile(conn)
    fake = Limited(2)
    res = jobs.run("collect_hh", collect.collect_hh_daily, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert res.ok
    c, _, _ = client_with(api, fake)
    body = c.get("/api/hh/collect/status").json()
    assert body["running"] is False
    pr = body["run"]["progress"]
    assert pr["full_total"] == 2 and pr["full_done"] == 2 and pr["profiles"] == 1 and pr["stage"] == "photos"
    assert body["run"]["ok"] is True and body["run"]["stats"]["seen"] == 4
    r, _, _ = client_with(api, fake, "recruiter")
    assert r.get("/api/hh/collect/status").status_code == 403


def test_s48_status_running_and_empty(conn, conn_factory, api):
    c, _, _ = client_with(api, Limited(1))
    assert c.get("/api/hh/collect/status").json() == {"running": False, "run": None}
    with conn_factory() as cx:
        db.run(cx, """INSERT INTO ops.job_run (job, progress) VALUES ('collect_hh', '{"full_done": 3}')""")
    body = c.get("/api/hh/collect/status").json()
    assert body["running"] is True and body["run"]["progress"]["full_done"] == 3
