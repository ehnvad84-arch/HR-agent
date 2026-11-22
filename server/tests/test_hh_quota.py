"""T9 semantic-search: суточный предел полных резюме hh на все прогоны (S11)."""
from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from factories import hh_item, profile, source
from test_contact_hh import RID
from test_contact_hh import FakeHH as ContactHH
from test_hh_daily import Limited

from hr import collect, config, db, decisions, hh, quota
from hr.http import RateLimiter
from hr.normalize import normalize
from hr.pipeline import process

S11 = "Предел полных резюме hh на сегодня исчерпан: {0} из {0}. Остальные подходящие — в завтрашнем сборе."


@pytest.fixture(autouse=True)
def hh_env(monkeypatch, conn, clock):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    hh.store_tokens(conn, {"access_token": "AT", "refresh_token": "RT", "expires_in": 30 * 86400}, "777",
                    clock.now())
    yield
    config.get_config.cache_clear()


def per_day(conn, n):
    db.run(conn, "UPDATE settings SET value = %s WHERE key = 'hh_full_resume_per_day'", (str(n),))


def used(conn, clock):
    return db.val(conn, "SELECT used FROM ops.daily_quota WHERE day = %s AND key = 'hh_full_resume'",
                  (quota.day_of(clock.now()),)) or 0


def crawl(conn_factory, fake, clock):
    return collect.collect_hh(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))


def test_s11_two_runs_a_day_share_the_daily_cap(conn, conn_factory, clock):
    profile(conn)
    per_day(conn, 2)
    fake = Limited(100)   # три сильных резюме и одно слабое
    first = crawl(conn_factory, fake, clock)
    assert len(fake.full_fetches) == 2 and first.note == S11.format(2)
    assert first.seen == 4 and first.new == 4, "после предела обход выдачи продолжается"
    clock.advance(seconds=3600)
    fake.calls.clear()
    second = crawl(conn_factory, fake, clock)
    assert fake.full_fetches == [], "второй прогон за сутки полных резюме не открывает"
    assert second.note == S11.format(2) and second.seen == 4
    assert used(conn, clock) == 2
    clock.advance(days=1)
    fake.calls.clear()
    crawl(conn_factory, fake, clock)
    assert len(fake.full_fetches) == 1, "назавтра — оставшееся подходящее"


def test_s11_daily_takes_smaller_of_views_quota_and_count(conn, conn_factory, clock):
    profile(conn)
    per_day(conn, 5)
    for _ in range(4):
        assert quota.take(conn_factory, "hh_full_resume", 5, now=clock.now())
    fake = Limited(10)
    st = collect.collect_hh_daily(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))
    assert st.extra["full_cap"] == 1 and len(fake.full_fetches) == 1
    assert used(conn, clock) == 5


def test_s11_daily_with_quota_spent_does_nothing(conn, conn_factory, clock):
    profile(conn)
    per_day(conn, 1)
    assert quota.take(conn_factory, "hh_full_resume", 1, now=clock.now())
    fake = Limited(10)
    st = collect.collect_hh_daily(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))
    assert fake.full_fetches == [] and "/resumes" not in fake.calls and st.note == S11.format(1)


def test_s11_mark_gone_recheck_counts_toward_cap(conn, conn_factory, clock):
    profile(conn)
    for rid in ("r1", "r2"):
        it = hh_item(id=rid, experience=[{"company": f"Завод {rid}", "position": "Инженер-электронщик",
                                          "start": "2018-01-01", "end": None}])
        process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
    clock.advance(days=50)
    hh.store_tokens(conn, {"access_token": "AT", "expires_in": 30 * 86400}, "777", clock.now())
    per_day(conn, 1)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=hh_item(id=req.url.path.rsplit("/", 1)[1]))

    st = collect.mark_gone(conn_factory, http=httpx.Client(transport=httpx.MockTransport(handler)), clock=clock,
                           limiter=RateLimiter(clock))
    assert len(calls) == 1 and used(conn, clock) == 1
    assert st.note.startswith(S11.format(1).split(".")[0])
    assert db.val(conn, "SELECT count(*) FROM observation WHERE is_gone") == 0, "не проверенное — не пропавшее"
    assert db.val(conn, "SELECT count(*) FROM observation WHERE last_seen < %s",
                  (clock.now() - timedelta(days=1),)) == 1


def test_s11_recruiter_contact_open_ignores_cap(conn, api, clock, conn_factory):
    profile(conn)
    it = hh_item(id=RID, hidden_fields=[])
    it.pop("contact", None)
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    uid = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('t', 'Т', 'recruiter') RETURNING id")
    cid = decisions.take(conn, pid, uid, clock.now())
    per_day(conn, 1)
    from hr.deps import get_hh_http
    from hr.main import app
    fake = ContactHH()
    app.dependency_overrides[get_hh_http] = fake.client
    c, h, _ = api("head_recruiter")
    assert quota.take(conn_factory, "hh_full_resume", 1)   # предел на сегодня исчерпан сбором
    aid = c.post(f"/api/candidate/{cid}/contact/prepare", json={}, headers=h).json()["action_id"]
    r = c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "executed"
    assert db.val(conn, "SELECT sum(used) FROM ops.daily_quota WHERE key = 'hh_full_resume'") == 1


@pytest.mark.parametrize("gone_first", [True, False])
def test_s11_mark_gone_and_evening_collect_share_one_daily_cap(conn, conn_factory, clock, gone_first):
    """Перепроверка пропавших и вечерний сбор за одни сутки берут из одного предела — в любом порядке."""
    profile(conn)
    for rid in ("r1", "r2"):
        it = hh_item(id=rid, experience=[{"company": f"Завод {rid}", "position": "Инженер-электронщик",
                                          "start": "2018-01-01", "end": None}])
        process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
    clock.advance(days=50)
    hh.store_tokens(conn, {"access_token": "AT", "expires_in": 30 * 86400}, "777", clock.now())
    per_day(conn, 2)
    rechecked = []

    def handler(req):
        rechecked.append(req.url.path)
        return httpx.Response(200, json=hh_item(id=req.url.path.rsplit("/", 1)[1]))

    fake = Limited(100)

    def gone():
        return collect.mark_gone(conn_factory, http=httpx.Client(transport=httpx.MockTransport(handler)),
                                 clock=clock, limiter=RateLimiter(clock))

    def evening():
        return collect.collect_hh_daily(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))

    first, second = (gone, evening) if gone_first else (evening, gone)
    first()
    second()
    assert used(conn, clock) == 2, "за сутки взято ровно предел, не больше"
    assert len(rechecked) + len(fake.full_fetches) == 2
    if gone_first:
        assert len(rechecked) == 2 and fake.full_fetches == [], "перепроверка заняла весь предел"
    else:
        assert len(fake.full_fetches) == 2 and rechecked == [], "сбор занял весь предел — перепроверка до завтра"
    assert db.val(conn, "SELECT count(*) FROM observation WHERE is_gone") == 0
