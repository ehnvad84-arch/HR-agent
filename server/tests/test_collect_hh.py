"""T13: обход hh (S1, S3, S4, S32)."""
import copy
from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from factories import hh_item, profile, weak_item

from hr import collect, config, db, hh, jobs
from hr.http import RateLimiter


@pytest.fixture(autouse=True)
def hh_env(monkeypatch, conn, clock):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    hh.store_tokens(conn, {"access_token": "AT", "refresh_token": "RT", "expires_in": 30 * 86400}, "777",
                    clock.now())
    yield
    config.get_config.cache_clear()


def search_view(item: dict) -> dict:
    """Элемент выдачи поиска: без навыков, как у hh."""
    v = copy.deepcopy(item)
    v.pop("skill_set", None)
    return v


class FakeHH:
    def __init__(self, items=None, captcha_on_page=None, error_on_page=None):
        self.items = items if items is not None else [hh_item(), weak_item()]
        self.captcha_on_page, self.error_on_page = captcha_on_page, error_on_page
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path)
        assert req.headers["Authorization"] == "Bearer AT"
        assert "RuwareHR" in req.headers["User-Agent"]
        if path == "/resumes":
            page = int(req.url.params.get("page", 0))
            if page == self.captcha_on_page:
                return httpx.Response(403, json={"errors": [{"type": "captcha_required"}]})
            if page == self.error_on_page:
                return httpx.Response(503)
            items = [search_view(i) for i in self.items] if page == 0 else []
            return httpx.Response(200, json={"items": items, "pages": 1, "found": len(self.items)})
        if path.startswith("/resumes/"):
            rid = path.rsplit("/", 1)[1]
            full = next(i for i in self.items if i["id"] == rid)
            return httpx.Response(200, json=full)
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self), headers={"User-Agent": "RuwareHR/1.0 (t@t)"})

    @property
    def full_fetches(self):
        return [c for c in self.calls if c.startswith("/resumes/")]


def run(conn_factory, fake, clock, **kw):
    return collect.collect_hh(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock), **kw)


def test_s01_crawl_brings_resumes(conn, conn_factory, clock):
    profile(conn)
    profile(conn, code="otk", title="Инженер ОТК", dept="qc", keywords=["AQL"])
    fake = FakeHH()
    stats = run(conn_factory, fake, clock)
    assert stats.seen == 4 and stats.new == 2 and stats.errors == 0
    assert db.val(conn, "SELECT count(*) FROM observation") == 2
    assert db.val(conn, "SELECT count(*) FROM obs_version") == 2
    q = db.rows(conn, "SELECT state, params FROM crawl_queue ORDER BY id")
    assert [r["state"] for r in q] == ["done", "done"] and q[0]["params"]["area"] == "53"
    strong = db.one(conn, "SELECT * FROM observation WHERE external_id = 'a1b2c3d4e5'")
    assert strong["person_id"] and "Altium Designer" in strong["skills"], "полное резюме дало навыки"


def test_s01_via_jobs_run_journal_ok(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH()
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, locks=("source:hh",),
                   http=fake.client(), clock=clock, limiter=RateLimiter(clock))
    assert res.ok and res.error is None
    assert db.one(conn, "SELECT ok FROM ops.job_run WHERE job = 'collect_hh' ORDER BY id DESC LIMIT 1")["ok"]


def test_s02_second_crawl_no_duplicates_no_false_changes(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH()
    run(conn_factory, fake, clock)
    clock.advance(days=1)
    fake.calls.clear()
    stats = run(conn_factory, fake, clock)
    assert stats.new == 0 and stats.changed == 0
    assert db.val(conn, "SELECT count(*) FROM obs_version") == 2
    assert fake.full_fetches == [], "неизменное резюме повторно не запрашивается"


def test_s03_captcha_stops_crawl(conn, conn_factory, clock):
    profile(conn)
    profile(conn, code="otk", title="Инженер ОТК", dept="qc")
    fake = FakeHH(captcha_on_page=0)
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert not res.ok and isinstance(res.exc, jobs.JobBlocked) and not res.retry
    q = db.rows(conn, "SELECT state, last_error FROM crawl_queue ORDER BY id")
    assert len(q) == 1 and q[0]["state"] == "error" and "проверк" in q[0]["last_error"]
    assert fake.calls == ["/resumes"], "после капчи запросов больше нет"


def test_s03_server_error_skips_profile_and_continues(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH(error_on_page=0)
    stats = run(conn_factory, fake, clock)
    assert stats.errors >= 1
    assert db.val(conn, "SELECT state FROM crawl_queue") == "error"


def test_s04_rate_limit_and_page_budget(conn, conn_factory, clock):
    for i in range(5):
        profile(conn, code=f"p{i}", title=f"Инженер {i}")
    db.run(conn, "UPDATE settings SET value = '3' WHERE key = 'crawl_max_pages'")
    fake = FakeHH(items=[weak_item()])
    stats = run(conn_factory, fake, clock)
    assert stats.pages == 3 and stats.limit_reached and "предел" in stats.note
    assert len(fake.calls) == 3
    assert all(s >= 0.99 for s in clock.slept) and len(clock.slept) == 2


def test_s32_full_resume_only_for_passing_and_capped(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH()
    run(conn_factory, fake, clock)
    assert fake.full_fetches == ["/resumes/a1b2c3d4e5"], "слабому полное резюме не запрашивается"


def test_s32_full_resume_cap_zero(conn, conn_factory, clock):
    profile(conn)
    db.run(conn, "UPDATE settings SET value = '0' WHERE key = 'hh_full_resume_per_run'")
    fake = FakeHH()
    stats = run(conn_factory, fake, clock)
    assert fake.full_fetches == [] and stats.new == 2


def test_s32_changed_resume_refetched(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH()
    run(conn_factory, fake, clock)
    fake.items[0]["updated_at"] = "2026-10-27T10:00:00+0300"
    fake.items[0]["skill_set"] = ["Altium Designer", "схемотехника", "трассировка", "ЭМС", "OrCAD"]
    fake.calls.clear()
    clock.advance(days=1)
    stats = run(conn_factory, fake, clock)
    assert fake.full_fetches == ["/resumes/a1b2c3d4e5"] and stats.changed == 1
    assert "OrCAD" in db.val(conn, "SELECT skills FROM observation WHERE external_id = 'a1b2c3d4e5'")


def test_s05_no_token(conn, conn_factory, clock):
    profile(conn)
    db.run(conn, "DELETE FROM ops.oauth_token")
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=FakeHH().client(), clock=clock)
    assert not res.ok and "авторизац" in res.error


def test_expired_token_refreshed_before_crawl(conn, conn_factory, clock):
    profile(conn)
    hh.store_tokens(conn, {"access_token": "OLD", "refresh_token": "RT", "expires_in": 1}, "777",
                    clock.now() - timedelta(hours=1))

    class Refreshing(FakeHH):
        def __call__(self, req):
            if req.url.path == "/token":
                self.calls.append("/token")
                return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT2", "expires_in": 3600})
            return super().__call__(req)

    fake = Refreshing()
    run(conn_factory, fake, clock)
    assert fake.calls[0] == "/token"


def test_selftest_reports_found(conn, clock):
    assert hh.selftest(conn, FakeHH().client(), clock.now())["found"] == 2
