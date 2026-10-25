"""T18: следы в сети (S29, S30, угроза T3)."""
import httpx
from factories import hh_item, profile, source

from hr import db, social
from hr.http import RateLimiter
from hr.normalize import normalize
from hr.pipeline import process

USER = {"login": "atestov", "html_url": "https://github.com/atestov", "name": "Andrei Testovyi",
        "location": "Krasnodar", "public_repos": 14, "created_at": "2019-03-01T00:00:00Z",
        "email": "a@example.com", "bio": "Люблю рыбалку"}
REPOS = [{"name": "stm32-sensors", "language": "C", "pushed_at": "2026-10-13T00:00:00Z", "fork": False},
         {"name": "pcb-tools", "language": "Python", "pushed_at": "2026-09-01T00:00:00Z", "fork": False}]


class FakeGitHub:
    def __init__(self, users_found=None, nick_ok=(), rate_limited=False):
        self.users_found = users_found if users_found is not None else []
        self.nick_ok, self.rate_limited = set(nick_ok), rate_limited
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path + ("?" + req.url.query.decode() if req.url.query else ""))
        if path == "/search/users":
            if self.rate_limited:
                return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, json={"message": "rate limit"})
            items = [{"login": u} for u in self.users_found]
            return httpx.Response(200, json={"total_count": len(items), "items": items})
        if path.startswith("/users/") and path.endswith("/repos"):
            return httpx.Response(200, json=REPOS)
        if path.startswith("/users/"):
            login = path.split("/")[2]
            if login in self.users_found or login in self.nick_ok:
                return httpx.Response(200, json={**USER, "login": login, "html_url": f"https://github.com/{login}"})
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def person(conn, clock, nick=None, **kw):
    profile(conn, weights={"keywords": 60, "open_code": 40})
    it = hh_item(**kw)
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    if nick:
        db.run(conn, "UPDATE person SET nicknames = %s WHERE id = %s", ([nick], pid))
    return pid


def run(conn_factory, fake, clock, **kw):
    return social.collect_social(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock), **kw)


def open_code_points(conn, pid):
    return db.val(conn, "SELECT features->'open_code'->>'points' FROM match WHERE person_id = %s", (pid,))


def test_s29_nickname_gives_confirmed_and_counts_in_score(conn, conn_factory, clock):
    pid = person(conn, clock, nick="atestov")
    assert open_code_points(conn, pid) == "0"
    run(conn_factory, FakeGitHub(nick_ok={"atestov"}), clock)
    sp = db.one(conn, "SELECT * FROM social_profile WHERE person_id = %s", (pid,))
    assert sp["kind"] == "github" and sp["confidence"] == "confirmed" and sp["matched_by"] == ["nick"]
    assert "14 репозиториев" in sp["summary"] and "C" in sp["topics"]
    assert "рыбалк" not in sp["summary"], "в summary только профессиональное"
    assert str(sp["last_activity"]) == "2026-10-13"
    assert int(open_code_points(conn, pid)) > 0


def test_s29_single_namesake_in_city_is_confirmed(conn, conn_factory, clock):
    pid = person(conn, clock)
    fake = FakeGitHub(users_found=["atestov"])
    run(conn_factory, fake, clock)
    sp = db.one(conn, "SELECT * FROM social_profile WHERE person_id = %s", (pid,))
    assert sp["confidence"] == "confirmed" and set(sp["matched_by"]) == {"name", "city"}
    search = [c for c in fake.calls if c.startswith("/search/users")][0]
    assert "location%3Akrasnodar" in search or "location:krasnodar" in search


def test_t3_two_namesakes_are_both_probable_and_give_no_points(conn, conn_factory, clock):
    pid = person(conn, clock)
    run(conn_factory, FakeGitHub(users_found=["atestov", "atestov2"]), clock)
    conf = {r["confidence"] for r in db.rows(conn, "SELECT confidence FROM social_profile WHERE person_id = %s",
                                             (pid,))}
    assert conf == {"probable"}
    assert open_code_points(conn, pid) == "0"


def test_s30_no_profiles_writes_result_for_every_source(conn, conn_factory, clock):
    pid = person(conn, clock)
    run(conn_factory, FakeGitHub(), clock)
    rows = {r["kind"]: r for r in db.rows(conn, "SELECT * FROM social_search WHERE person_id = %s", (pid,))}
    expected = set(db.setting(conn, "social_sources"))
    assert set(rows) == expected
    assert all(r["found"] is False for r in rows.values())
    assert rows["github"]["queries"] and rows["github"]["note"] is None
    assert rows["vk"]["note"] == "источник не подключён"


def test_rate_limit_stops_github_without_false_negatives(conn, conn_factory, clock):
    pid = person(conn, clock)
    run(conn_factory, FakeGitHub(rate_limited=True), clock)
    kinds = {r["kind"] for r in db.rows(conn, "SELECT kind FROM social_search WHERE person_id = %s", (pid,))}
    assert "github" not in kinds, "не искали — не значит не нашли"


def test_search_rate_one_per_two_seconds(conn, conn_factory, clock):
    person(conn, clock)
    run(conn_factory, FakeGitHub(), clock)
    assert clock.slept and all(s >= 1.99 for s in clock.slept)


def test_already_searched_person_skipped_next_run(conn, conn_factory, clock):
    person(conn, clock)
    run(conn_factory, FakeGitHub(), clock)
    fake = FakeGitHub()
    stats = run(conn_factory, fake, clock)
    assert fake.calls == [] and stats.seen == 0
