"""T15b workplace: предпросмотр новой версии профиля и «Кто утверждает» (S26, S39)."""
from datetime import timedelta

from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process


def test_s26_preview_counts_enter_and_leave(conn, api, clock):
    prof = profile(conn, approved_at=clock.now() - timedelta(days=1))
    it = hh_item()
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    c, h, _ = api()
    new = c.post(f"/api/profile/{prof['id']}", json={"keywords": ["COBOL", "Fortran", "Delphi"],
                                                     "title": "Программист COBOL"}, headers=h).json()["id"]
    r = c.get(f"/api/profile/{new}/preview").json()
    assert r["passes_now"] == 1 and r["passes_new"] == 0
    assert [x["person_id"] for x in r["leave"]] == [pid] and r["enter"] == []
    assert r["leave"][0]["score_now"] > r["leave"][0]["score_new"]
    assert db.val(conn, "SELECT count(*) FROM match WHERE role_profile_id = %s", (new,)) == 0, "без записи"
    c.post(f"/api/profile/{new}/approve", json={}, headers=h)
    assert c.get(f"/api/profile/{new}/preview").status_code == 409


def test_preview_needs_profile_edit(conn, api, clock):
    prof = profile(conn, approved=False)
    c, _, _ = api("no_access")   # руководителю право дано (manager-cabinet), чужой портрет — 404 в test_scope_points
    assert c.get(f"/api/profile/{prof['id']}/preview").status_code == 403


def test_s39_approvers(conn, api):
    c, _, uid = api()
    rec, _, _ = api("recruiter")
    r = c.get("/api/approvers").json()
    assert r["roles"] == ["head_recruiter"]
    assert [u["id"] for u in r["users"]] == [uid]
