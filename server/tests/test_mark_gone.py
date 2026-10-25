"""T14: резюме пропало с площадки (S11)."""

import httpx
import pytest
from cryptography.fernet import Fernet
from factories import hh_item, profile, source

from hr import collect, config, db, hh
from hr.http import RateLimiter
from hr.normalize import normalize
from hr.pipeline import process


@pytest.fixture(autouse=True)
def hh_env(monkeypatch, conn, clock):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    hh.store_tokens(conn, {"access_token": "AT", "expires_in": 365 * 86400}, "777", clock.now())
    yield
    config.get_config.cache_clear()


def fake(gone: set[str]):
    calls = []

    def handler(req):
        rid = req.url.path.rsplit("/", 1)[1]
        calls.append(rid)
        return httpx.Response(404) if rid in gone else httpx.Response(200, json=hh_item(id=rid))

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def two_people(conn, clock):
    profile(conn)
    ids = []
    for rid, city in (("r-gone", "Краснодар"), ("r-alive", "Динская")):
        exp = [{"company": f"Завод {rid}", "position": "Инженер-электронщик", "start": "2018-01-01", "end": None}]
        it = hh_item(id=rid, experience=exp, area={"id": "1", "name": city})
        ids.append(process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id)
    return ids


def test_s11_gone_resume_gets_disappeared_and_card_stays(conn, conn_factory, clock):
    gone_pid, alive_pid = two_people(conn, clock)
    clock.advance(days=50)
    http, calls = fake({"r-gone"})
    started = clock.now()
    stats = collect.mark_gone(conn_factory, http=http, clock=clock, limiter=RateLimiter(clock))
    assert sorted(calls) == ["r-alive", "r-gone"] and stats.changed == 1
    gone = db.one(conn, "SELECT * FROM observation WHERE external_id = 'r-gone'")
    assert gone["is_gone"]
    kinds = [e["kind"] for e in db.rows(conn, "SELECT kind FROM obs_event WHERE person_id = %s", (gone_pid,))]
    assert kinds[-1] == "disappeared"
    assert db.one(conn, "SELECT id FROM person WHERE id = %s", (gone_pid,))
    alive = db.one(conn, "SELECT * FROM observation WHERE external_id = 'r-alive'")
    assert not alive["is_gone"] and alive["last_seen"] == started
    assert "disappeared" not in [e["kind"] for e in db.rows(conn, "SELECT kind FROM obs_event WHERE person_id = %s",
                                                            (alive_pid,))]


def test_s11_fresh_observations_not_rechecked(conn, conn_factory, clock):
    two_people(conn, clock)
    clock.advance(days=10)
    http, calls = fake(set())
    collect.mark_gone(conn_factory, http=http, clock=clock, limiter=RateLimiter(clock))
    assert calls == []


def test_s11_recheck_limit(conn, conn_factory, clock):
    two_people(conn, clock)
    db.run(conn, "UPDATE settings SET value = '1' WHERE key = 'hh_recheck_per_run'")
    clock.advance(days=50)
    http, calls = fake({"r-gone", "r-alive"})
    collect.mark_gone(conn_factory, http=http, clock=clock, limiter=RateLimiter(clock))
    assert len(calls) == 1
    assert db.val(conn, "SELECT count(*) FROM observation WHERE is_gone") == 1


def test_s11_source_without_recheck_marked_by_last_seen(conn, conn_factory, clock):
    profile(conn)
    db.run(conn, "UPDATE source SET is_active = true WHERE code = 'trudvsem'")
    it = {"cv": {"id": "tv-1", "positionName": "Ведущий инженер-электронщик", "localityName": "Краснодар"}}
    process(conn, source(conn, "trudvsem"), normalize("trudvsem", it), it, clock.now())
    clock.advance(days=50)
    http, calls = fake(set())
    collect.mark_gone(conn_factory, http=http, clock=clock, limiter=RateLimiter(clock))
    assert db.val(conn, "SELECT is_gone FROM observation WHERE external_id = 'tv-1'") is True
    assert calls == []
