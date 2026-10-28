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


def hh_error(status: int, value: str, **extra) -> httpx.Response:
    """Ошибка в формате hh: {"errors": [{"type": ..., "value": ...}]}."""
    return httpx.Response(status, json={"errors": [{"type": extra.pop("type", "x"), "value": value}], **extra})


EXPIRED = httpx.Response(403, json={"oauth_error": "token-expired",
                                    "errors": [{"type": "oauth", "value": "token_expired"}]})


class FakeHH:
    def __init__(self, items=None, captcha_on_page=None, error_on_page=None, search_reply=None, full_reply=None,
                 search_access=True):
        self.items = items if items is not None else [hh_item(), weak_item()]
        self.captcha_on_page, self.error_on_page = captcha_on_page, error_on_page
        self.search_reply, self.full_reply, self.search_access = search_reply, full_reply, search_access
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path)
        assert req.headers["Authorization"] == "Bearer AT"
        assert "RuwareHR" in req.headers["User-Agent"]
        if path == "/me":
            return httpx.Response(200, json={"is_employer": True, "employer": {"id": "777"}, "manager": {"id": "55"}})
        if path == "/employers/777/managers/55/method_access":
            return httpx.Response(200, json={"items": [
                {"id": "3", "description": "резюме с откликом", "access": {"has_access": True}},
                {"id": "4", "description": "просмотр резюме", "access": {"has_access": self.search_access}}]})
        if path == "/employers/777/services/payable_api_actions/active":
            return httpx.Response(200, json={"items": [
                {"id": "1", "service_type": {"id": "API_LIMITED", "name": "Пакет запросов к API HH"},
                 "balance": {"actual": 9000, "initial": 10000}, "activated_at": "2026-10-01T00:00:00+0300",
                 "expires_at": "2027-10-01T00:00:00+0300"}]})
        if path == "/employers/777/managers/55/limits/resume":
            return httpx.Response(200, json={"limits": {"resume_view": 500, "resume_view_from_api": 300},
                                             "spend": {"resume_view": 10, "resume_view_from_api": 120},
                                             "left": {"resume_view": 490, "resume_view_from_api": 180}})
        if path == "/resumes":
            if self.search_reply is not None:
                return self.search_reply
            page = int(req.url.params.get("page", 0))
            if page == self.captcha_on_page:
                return httpx.Response(403, json={"errors": [{"type": "captcha_required"}]})
            if page == self.error_on_page:
                return httpx.Response(503)
            items = [search_view(i) for i in self.items] if page == 0 else []
            return httpx.Response(200, json={"items": items, "pages": 1, "found": len(self.items)})
        if path.startswith("/resumes/"):
            if self.full_reply is not None:
                return self.full_reply
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


class ExpiresOnHH(FakeHH):
    """hh считает токен истёкшим раньше нашего срока и отвечает 403, а не 401."""

    def __call__(self, req):
        if req.url.path == "/token":
            self.calls.append("/token")
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT2", "expires_in": 3600})
        if req.headers["Authorization"] == "Bearer OLD":
            self.calls.append(req.url.path)
            return EXPIRED
        return super().__call__(req)


def test_403_token_expired_is_refreshed(conn, conn_factory, clock):
    profile(conn)
    hh.store_tokens(conn, {"access_token": "OLD", "refresh_token": "RT", "expires_in": 30 * 86400}, "777",
                    clock.now())
    fake = ExpiresOnHH()
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert res.ok, res.error
    assert fake.calls[:3] == ["/resumes", "/token", "/resumes"]
    assert db.val(conn, "SELECT count(*) FROM observation") == 2


def test_403_token_revoked_asks_for_auth(conn, conn_factory, clock):
    profile(conn)
    revoked = httpx.Response(403, json={"oauth_error": "token-revoked",
                                        "errors": [{"type": "oauth", "value": "bad_authorization"}]})
    fake = FakeHH(search_reply=revoked)
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert not res.ok and isinstance(res.exc, jobs.JobNotConfigured) and "hh-auth" in res.error
    assert "/token" not in fake.calls, "отозванный токен обновлением не лечится"


def test_view_limit_continues_without_full_resumes(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH(full_reply=hh_error(429, "view_limit_exceeded", type="resumes"))
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert res.ok, res.error
    assert res.stats.new == 2 and "лимит просмотров" in res.stats.note
    assert len(fake.full_fetches) == 1, "после лимита полные резюме больше не запрашиваются"


@pytest.mark.parametrize("value", ["daily_resume_search_limit_exceeded",
                                   "daily_resume_search_limit_without_services_exceeded"])
def test_search_limit_is_not_captcha(conn, conn_factory, clock, value):
    profile(conn)
    fake = FakeHH(search_reply=hh_error(429, value, type="resumes"))
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert not res.ok and isinstance(res.exc, jobs.JobLimit) and not res.retry
    assert "лимит поиска" in res.error and "робот" not in res.error
    run_stats = db.val(conn, "SELECT stats FROM ops.job_run WHERE job = 'collect_hh' ORDER BY id DESC LIMIT 1")
    assert not run_stats.get("blocked")


def test_403_without_paid_access(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH(search_reply=hh_error(403, "action_must_be_payed", type="api_access_payment"))
    res = jobs.run("collect_hh", collect.collect_hh, conn_factory, http=fake.client(), clock=clock,
                   limiter=RateLimiter(clock))
    assert not res.ok and isinstance(res.exc, jobs.JobNotConfigured) and "оплач" in res.error


def test_selftest_reports_access_services_and_limits(conn, conn_factory, clock):
    fake = FakeHH()
    res = hh.selftest(conn_factory, fake.client(), clock.now(), limiter=RateLimiter(clock))
    assert res["search_access"] is True and res["found"] == 2
    assert res["views_left"] == 180 and res["views_left_site"] == 490
    assert res["services"] == [{"type": "API_LIMITED", "name": "Пакет запросов к API HH", "balance": 9000,
                                "expires_at": "2027-10-01T00:00:00+0300"}]
    assert all(s >= 0.99 for s in clock.slept), "не чаще запроса в секунду"


def test_selftest_without_search_access_skips_search(conn, conn_factory, clock):
    fake = FakeHH(search_access=False)
    res = hh.selftest(conn_factory, fake.client(), clock.now(), limiter=RateLimiter(clock))
    assert res["search_access"] is False and res["found"] is None
    assert "/resumes" not in fake.calls


def test_selftest_keeps_refreshed_token_when_later_request_fails(conn, conn_factory, clock):
    """refresh_token одноразовый: новый токен сохраняется до проверок, даже если они упадут."""
    hh.store_tokens(conn, {"access_token": "OLD", "refresh_token": "RT", "expires_in": 1}, "777",
                    clock.now() - timedelta(hours=1))

    class Broken(ExpiresOnHH):
        def __call__(self, req):
            if req.url.path.endswith("/method_access"):
                return httpx.Response(500)
            return super().__call__(req)

    with pytest.raises(httpx.HTTPStatusError):
        hh.selftest(conn_factory, Broken().client(), clock.now(), limiter=RateLimiter(clock))
    assert hh.access_token(conn, Broken().client(), clock.now()) == "AT"


def test_token_refresh_respects_rate_limit(conn, conn_factory, clock):
    profile(conn)
    hh.store_tokens(conn, {"access_token": "OLD", "refresh_token": "RT", "expires_in": 30 * 86400}, "777",
                    clock.now())
    fake = ExpiresOnHH()
    run(conn_factory, fake, clock)
    assert "/token" in fake.calls
    assert len(clock.slept) == len(fake.calls) - 1 and all(s >= 0.99 for s in clock.slept), \
        "POST /token тоже не чаще запроса в секунду"


def test_resume_saved_after_view_limit_gets_full_text_next_run(conn, conn_factory, clock):
    profile(conn)
    fake = FakeHH(full_reply=hh_error(429, "view_limit_exceeded", type="resumes"))
    run(conn_factory, fake, clock)
    fake.full_reply, fake.calls = None, []
    clock.advance(days=1)
    run(conn_factory, fake, clock)
    assert fake.full_fetches == ["/resumes/a1b2c3d4e5"], "недобранное полное резюме запрашивается снова"
    assert "Altium Designer" in db.val(conn, "SELECT skills FROM observation WHERE external_id = 'a1b2c3d4e5'")
