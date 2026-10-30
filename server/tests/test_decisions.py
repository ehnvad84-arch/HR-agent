"""T9 workplace: «В работу», «отложить», «не наш», «вести особо», профиль в сети (S11–S15; пункт 18)."""
import psycopg
import pytest
from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process

LONG = ("Опыт в схемотехнике сильный, но сейчас человек полгода как сменил работу и вряд ли рассмотрит переход. "
        "Вернуться к нему стоит после испытательного срока: посмотреть, как изменилось резюме и ожидания по доходу.")
assert len(LONG) >= 200


@pytest.fixture
def strong(conn, clock):
    prof = profile(conn)
    it = hh_item(last_name="Петров", first_name="Пётр")
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    db.run(conn, """INSERT INTO alert (person_id, role_profile_id, reason, headline)
                    VALUES (%s, %s, 'new_high_score', 'x')""", (pid, prof["id"]))
    return pid, prof


def vacancy(conn, prof):
    return db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, %s) RETURNING id",
                  (prof["id"], prof["title"]))


def test_s11_take_into_open_vacancy(conn, api, strong):
    pid, prof = strong
    vid = vacancy(conn, prof)
    c, h, uid = api()
    r = c.post(f"/api/person/{pid}/take", json={}, headers=h)
    assert r.status_code == 200
    cid = r.json()["candidate_id"]
    cand = db.one(conn, "SELECT * FROM candidate WHERE id = %s", (cid,))
    assert cand["status"] == "picked_by_agent" and cand["fio"] == "Петров Пётр" and cand["owner_id"] == uid
    assert db.val(conn, "SELECT vacancy_id FROM ops.candidate_vacancy WHERE candidate_id = %s", (cid,)) == vid
    cons = db.one(conn, "SELECT basis::text AS basis, channel FROM consent WHERE candidate_id = %s", (cid,))
    assert cons["basis"] == "candidate_initiated" and "hh" in cons["channel"]
    hist = db.val(conn, "SELECT to_status::text FROM status_history WHERE candidate_id = %s", (cid,))
    assert hist == "picked_by_agent"
    assert db.val(conn, "SELECT candidate_id FROM person WHERE id = %s", (pid,)) == cid
    assert db.val(conn, "SELECT state FROM alert WHERE person_id = %s", (pid,)) == "taken"
    assert db.val(conn, "SELECT count(*) FROM decision_log WHERE person_id = %s", (pid,)) == 0, "take — без журнала"


def test_s11_take_without_vacancy_goes_to_reserve(conn, api, strong):
    pid, _ = strong
    c, h, _ = api()
    cid = c.post(f"/api/person/{pid}/take", json={}, headers=h).json()["candidate_id"]
    assert db.val(conn, "SELECT status::text FROM candidate WHERE id = %s", (cid,)) == "reserve"
    assert db.val(conn, "SELECT count(*) FROM ops.candidate_vacancy WHERE candidate_id = %s", (cid,)) == 0


def test_take_twice_409(conn, api, strong):
    pid, _ = strong
    c, h, _ = api()
    c.post(f"/api/person/{pid}/take", json={}, headers=h)
    assert c.post(f"/api/person/{pid}/take", json={}, headers=h).status_code == 409


def test_hiring_manager_cannot_take(conn, api, strong):
    pid, _ = strong
    c, h, _ = api("hiring_manager")
    assert c.post(f"/api/person/{pid}/take", json={}, headers=h).status_code == 403


def test_s12_postpone(conn, api, strong, clock):
    pid, prof = strong
    c, h, _ = api()
    r = c.post("/api/decision", json={"person_id": pid, "kind": "postpone", "role_profile_id": prof["id"],
                                      "comment": LONG}, headers=h)
    assert r.status_code == 200
    p = db.one(conn, "SELECT postponed_until FROM person WHERE id = %s", (pid,))
    assert (p["postponed_until"] - clock.now().date()).days >= 89
    d = db.one(conn, "SELECT kind, score_at, role_profile_id FROM decision_log WHERE person_id = %s", (pid,))
    assert d["kind"] == "postpone" and d["score_at"] > 0 and d["role_profile_id"] == prof["id"]
    assert db.val(conn, "SELECT state FROM alert WHERE person_id = %s", (pid,)) == "postponed"


def test_s12_reject_by_profile(conn, api, strong):
    pid, prof = strong
    c, h, _ = api()
    r = c.post("/api/decision", json={"person_id": pid, "kind": "reject", "role_profile_id": prof["id"],
                                      "comment": LONG}, headers=h)
    assert r.status_code == 200
    assert db.val(conn, "SELECT watch::text FROM person WHERE id = %s", (pid,)) == "observed", "только по профилю"
    assert db.val(conn, "SELECT state FROM alert WHERE person_id = %s", (pid,)) == "dismissed"


def test_s12_reject_without_profile_drops(conn, api, strong):
    pid, _ = strong
    c, h, _ = api()
    c.post("/api/decision", json={"person_id": pid, "kind": "reject", "comment": LONG}, headers=h)
    assert db.val(conn, "SELECT watch::text FROM person WHERE id = %s", (pid,)) == "dropped"


def test_s13_short_comment_422_and_db_check(conn, api, strong):
    pid, prof = strong
    c, h, uid = api()
    r = c.post("/api/decision", json={"person_id": pid, "kind": "reject", "comment": "не подходит"}, headers=h)
    assert r.status_code == 422 and "200" in r.json()["message"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            db.run(conn, """INSERT INTO decision_log (person_id, kind, comment, author_id)
                            VALUES (%s, 'reject', 'коротко', %s)""", (pid, uid))


def test_s14_watch(conn, api, strong):
    pid, _ = strong
    c, h, uid = api()
    assert c.post(f"/api/person/{pid}/watch", json={"on": True}, headers=h).json()["watch"] == "watched"
    p = db.one(conn, "SELECT watch::text AS watch, watch_by FROM person WHERE id = %s", (pid,))
    assert p == {"watch": "watched", "watch_by": uid}
    assert c.post(f"/api/person/{pid}/watch", json={"on": False}, headers=h).json()["watch"] == "observed"


def test_s15_confirm_social(conn, api, strong):
    pid, _ = strong
    sid = db.val(conn, """INSERT INTO social_profile (person_id, kind, url)
                          VALUES (%s, 'github', 'https://github.com/x') RETURNING id""", (pid,))
    c, h, uid = api()
    assert c.post(f"/api/social/{sid}/confirm", json={}, headers=h).json()["confidence"] == "confirmed"
    assert db.val(conn, "SELECT confirmed_by FROM social_profile WHERE id = %s", (sid,)) == uid
    db.run(conn, "UPDATE social_profile SET confidence = 'rejected' WHERE id = %s", (sid,))
    assert c.post(f"/api/social/{sid}/confirm", json={}, headers=h).status_code == 409
