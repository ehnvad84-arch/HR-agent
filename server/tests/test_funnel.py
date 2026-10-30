"""T10 workplace: воронка — перевод, резерв, закрытие, дата встречи, среднее время на этапе (S24, S36, S37)."""
from datetime import timedelta

import pytest
from factories import hh_item, profile, source

from hr import db, decisions
from hr.normalize import normalize
from hr.pipeline import process


@pytest.fixture
def cand(conn, clock):
    prof = profile(conn)
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, %s) RETURNING id",
                 (prof["id"], prof["title"]))
    it = hh_item()
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    uid = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('t', 'Т', 'recruiter') RETURNING id")
    return decisions.take(conn, pid, uid, clock.now()), vid, pid


def status(conn, cid):
    return db.val(conn, "SELECT status::text FROM candidate WHERE id = %s", (cid,))


def test_s24_move_writes_history(conn, api, cand):
    cid, vid, _ = cand
    c, h, uid = api()
    r = c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "first_message_sent"}, headers=h)
    assert r.status_code == 200 and status(conn, cid) == "first_message_sent"
    last = db.one(conn, """SELECT from_status::text AS f, to_status::text AS t, changed_by FROM status_history
                           WHERE candidate_id = %s ORDER BY id DESC LIMIT 1""", (cid,))
    assert last == {"f": "picked_by_agent", "t": "first_message_sent", "changed_by": uid}
    assert db.val(conn, "SELECT last_contact_at FROM candidate WHERE id = %s", (cid,)) is not None


def test_s36_meeting_date(conn, api, cand, clock):
    cid, vid, _ = cand
    c, h, _ = api()
    at = (clock.now() + timedelta(hours=3)).isoformat()
    c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "phone_interview",
                                                 "scheduled_at": at}, headers=h)
    i = db.one(conn, "SELECT kind, vacancy_id FROM interview WHERE candidate_id = %s", (cid,))
    assert i == {"kind": "phone", "vacancy_id": vid}
    assert len(c.get("/api/today").json()["meetings"]) == 1


def test_s37_reserve_and_close(conn, api, cand):
    cid, vid, _ = cand
    c, h, _ = api()
    c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "reserve"}, headers=h)
    assert status(conn, cid) == "reserve"
    r = c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "rejected_by_candidate",
                                                     "comment": "отказался: остаётся на месте"}, headers=h)
    assert r.status_code == 200 and status(conn, cid) == "rejected_by_candidate"
    assert db.val(conn, "SELECT reject_reason FROM candidate WHERE id = %s", (cid,)).startswith("отказался")


def test_unknown_stage_422(conn, api, cand):
    cid, vid, _ = cand
    c, h, _ = api()
    assert c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "luna"},
                  headers=h).status_code == 422


def test_attach_reserve_candidate_to_vacancy(conn, api, clock):
    prof = profile(conn)
    it = hh_item()
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    c, h, uid = api()
    cid = decisions.take(conn, pid, uid, clock.now())   # вакансии нет — резерв
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, 'В') RETURNING id", (prof["id"],))
    c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "picked_by_agent"}, headers=h)
    assert db.val(conn, "SELECT count(*) FROM ops.candidate_vacancy WHERE candidate_id = %s AND vacancy_id = %s",
                  (cid, vid)) == 1


def test_s24_funnel_stages_and_avg_days(conn, api, cand, clock):
    cid, vid, _ = cand
    db.run(conn, "UPDATE status_history SET changed_at = %s WHERE candidate_id = %s",
           (clock.now() - timedelta(days=4), cid))
    c, _, _ = api()
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    stage = next(s for s in f["stages"] if s["status"] == "picked_by_agent")
    assert stage["people"][0]["candidate_id"] == cid and stage["avg_days"] == 4
    assert [s["status"] for s in f["stages"]][:2] == ["picked_by_agent", "first_message_sent"]
    assert c.get("/api/vacancy/999999/funnel").status_code == 404
