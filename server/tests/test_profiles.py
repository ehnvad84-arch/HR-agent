"""T15 workplace: профили — список, правка → новая версия, утверждение → пересчёт и журнал (S25–S27; 21, 32)."""
from datetime import timedelta

import pytest
from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process


@pytest.fixture
def prof(conn, clock):
    p = profile(conn, approved_at=clock.now() - timedelta(days=1))
    it = hh_item()
    process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
    return p


def test_s25_list_with_state_and_search(conn, api, prof):
    profile(conn, code="otk", title="Инженер ОТК", dept="qc", approved=False)
    c, _, _ = api()
    items = c.get("/api/profiles").json()["items"]
    states = {i["code"]: i["state"] for i in items}
    assert states == {"engineer_hw_lead": "approved", "otk": "pending"}
    found = c.get("/api/profiles", params={"q": "ОТК"}).json()["items"]
    assert [i["code"] for i in found] == ["otk"]
    one = c.get(f"/api/profile/{prof['id']}").json()
    assert one["keywords"] and one["weights"] and one["state"] == "approved"


def test_s26_edit_creates_pending_version_and_old_still_scores(conn, api, prof):
    c, h, uid = api("recruiter")
    r = c.post(f"/api/profile/{prof['id']}", json={"keywords": ["Altium", "OrCAD"], "salary_max": 220000}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "pending"
    new = db.one(conn, "SELECT * FROM role_profile WHERE id = %s", (r.json()["id"],))
    assert new["code"] == prof["code"] and new["version"] == "1.1" and new["approved_at"] is None
    assert new["keywords"] == ["Altium", "OrCAD"] and new["salary_max"] == 220000
    assert db.val(conn, "SELECT count(*) FROM match WHERE role_profile_id = %s", (new["id"],)) == 0, \
        "выдача по прежней версии до утверждения (21)"
    ch = db.one(conn, "SELECT kind, applied_by, after FROM agent_change ORDER BY id DESC LIMIT 1")
    assert ch["kind"] == "profile" and ch["applied_by"] == uid and ch["after"]["state"] == "pending"


def test_edit_validates_weights(conn, api, prof):
    c, h, _ = api()
    assert c.post(f"/api/profile/{prof['id']}", json={"weights": {"рост": 50}}, headers=h).status_code == 422
    assert c.post(f"/api/profile/{prof['id']}", json={"weights": {"keywords": -5}}, headers=h).status_code == 422
    assert c.post(f"/api/profile/{prof['id']}", json={"geo": "марс"}, headers=h).status_code == 422


def test_s27_32_recruiter_cannot_approve(conn, api, prof):
    c, h, _ = api("recruiter")
    new = c.post(f"/api/profile/{prof['id']}", json={"keywords": ["Altium"]}, headers=h).json()["id"]
    assert c.post(f"/api/profile/{new}/approve", json={}, headers=h).status_code == 403


def test_s27_approve_rescores_and_logs(conn, api, prof):
    c, h, uid = api()
    new = c.post(f"/api/profile/{prof['id']}", json={"keywords": ["Altium", "схемотехника"]}, headers=h).json()["id"]
    r = c.post(f"/api/profile/{new}/approve", json={}, headers=h)
    assert r.status_code == 202
    p = db.one(conn, "SELECT approved_by, approved_at FROM role_profile WHERE id = %s", (new,))
    assert p["approved_by"] == uid and p["approved_at"]
    assert db.val(conn, "SELECT count(*) FROM match WHERE role_profile_id = %s", (new,)) >= 1, "база пересчитана"
    assert db.val(conn, "SELECT ok FROM ops.job_run WHERE job = 'rescore_profile' ORDER BY id DESC LIMIT 1")
    ch = db.one(conn, "SELECT after FROM agent_change WHERE kind = 'profile' ORDER BY id DESC LIMIT 1")
    assert ch["after"]["state"] == "approved"
    assert c.post(f"/api/profile/{new}/approve", json={}, headers=h).status_code == 409
    items = {i["code"]: i for i in c.get("/api/profiles").json()["items"]}
    assert items[prof["code"]]["id"] == new and items[prof["code"]]["version"] == "1.1"


def test_base_uses_current_version_only(conn, api, prof):
    c, h, _ = api()
    before = c.get("/api/base").json()["items"][0]["score"]
    new = c.post(f"/api/profile/{prof['id']}", json={"keywords": ["COBOL", "Fortran", "Delphi"]},
                 headers=h).json()["id"]
    c.post(f"/api/profile/{new}/approve", json={}, headers=h)
    after = c.get("/api/base").json()["items"][0]["score"]
    now = db.val(conn, "SELECT score FROM match WHERE role_profile_id = %s", (new,))
    assert after == now < before, "старая версия с бо́льшим баллом не тянет выдачу"
