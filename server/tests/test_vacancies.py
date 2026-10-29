"""T14 workplace: вакансии — список, создание, закрытие с комментарием, «Открыть заново» (S21, S23, S38; 23, 24)."""
import psycopg
import pytest
from factories import profile

from hr import db

LONG = ("Позиция закрыта: кандидат из резерва вышел на работу 1 ноября, испытательный срок три месяца. "
        "Профиль подтвердился, по нему стоит продолжать наблюдение: "
        "в отделе ещё одна ставка откроется весной, к марту.")
assert len(LONG) >= 200


@pytest.fixture
def prof(conn):
    return profile(conn)


def create(c, h, prof, **kw):
    return c.post("/api/vacancy", json={"role_profile_id": prof["id"], "title": prof["title"], **kw}, headers=h)


def test_s21_create_and_list(conn, api, prof):
    c, h, uid = api()
    r = create(c, h, prof, headcount=2, target_date="2026-12-31")
    assert r.status_code == 200
    v = db.one(conn, "SELECT origin::text AS origin, headcount, recruiter_id, state FROM vacancy WHERE id = %s",
               (r.json()["id"],))
    assert v == {"origin": "recruiter", "headcount": 2, "recruiter_id": uid, "state": "open"}
    lst = c.get("/api/vacancies").json()
    assert lst["open"][0]["id"] == r.json()["id"] and lst["open"][0]["in_work"] == 0 and lst["closed"] == []


def test_unapproved_profile_rejected(conn, api):
    draft = profile(conn, code="draft_p", approved=False)
    c, h, _ = api()
    assert create(c, h, draft).status_code == 422


def test_recruiter_can_hiring_manager_cannot(conn, api, prof):
    c, h, _ = api("hiring_manager")
    assert create(c, h, prof).status_code == 403


def test_s23_close_requires_200_chars_and_keeps_sourcing(conn, api, prof):
    c, h, uid = api()
    vid = create(c, h, prof).json()["id"]
    r = c.post(f"/api/vacancy/{vid}/close", json={"comment": "закрыли"}, headers=h)
    assert r.status_code == 422 and "200" in r.json()["message"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            db.run(conn, "UPDATE vacancy SET state = 'closed', close_comment = 'коротко' WHERE id = %s", (vid,))
    r = c.post(f"/api/vacancy/{vid}/close", json={"comment": LONG}, headers=h)
    assert r.json()["state"] == "closed"
    v = db.one(conn, "SELECT state, keep_sourcing, closed_by, closed_at FROM vacancy WHERE id = %s", (vid,))
    assert v["keep_sourcing"] is True and v["closed_by"] == uid and v["closed_at"]
    assert c.post(f"/api/vacancy/{vid}/close", json={"comment": LONG}, headers=h).status_code == 409
    assert c.get("/api/vacancies").json()["closed"][0]["id"] == vid


def test_s38_reopen(conn, api, prof):
    c, h, uid = api()
    vid = create(c, h, prof).json()["id"]
    c.post(f"/api/vacancy/{vid}/close", json={"comment": LONG}, headers=h)
    r = c.post(f"/api/vacancy/{vid}/reopen", json={}, headers=h)
    assert r.json()["state"] == "open"
    assert db.val(conn, "SELECT state FROM vacancy WHERE id = %s", (vid,)) == "open"
    a = db.one(conn, "SELECT action, object_type FROM audit_log WHERE object_id = %s AND user_id = %s", (vid, uid))
    assert a == {"action": "edit", "object_type": "vacancy"}
    assert c.post(f"/api/vacancy/{vid}/reopen", json={}, headers=h).status_code == 409
