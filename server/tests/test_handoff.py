"""T6 manager-cabinet: передача кандидата в отдел персонала (S6, S9, S10, S11)."""
from factories import org, user

from hr import db

GOOD = "Опыт проектных продаж систем безопасности, работал с интеграторами, нам подходит."
REASON = "Позвонили, человек не ищет работу и просит не беспокоить ближайший год, +7 900 123-45-67."


def person(conn, prof, label="Продажи, 5 лет", score=80):
    pid = db.val(conn, "INSERT INTO person (label) VALUES (%s) RETURNING id", (label,))
    db.run(conn, "INSERT INTO match (person_id, role_profile_id, profile_version, score) VALUES (%s, %s, %s, %s)",
           (pid, prof["id"], prof["version"], score))
    return pid


def setup(conn, clock, api):
    o = org(conn, clock.now())
    rc, rh, rid = api("recruiter", login="owner")
    vac = db.val(conn, """INSERT INTO vacancy (role_profile_id, title, staff_position_id, recruiter_id)
                          VALUES (%s, 'МПП', %s, %s) RETURNING id""", (o["sales"]["id"], o["pos"]["p-mpp1"], rid))
    mc, mh, mid = api("hiring_manager", login="sales_head")
    user(conn, "sales_head", "hiring_manager", o["units"]["u-sales"])
    return o, vac, (mc, mh, mid), (rc, rh, rid)


def test_s9_send_and_s11_take(conn, clock, api):
    o, vac, (mc, mh, _), (rc, rh, _) = setup(conn, clock, api)
    pid = person(conn, o["sales"])
    opts = mc.get(f"/api/handoff/options?person_id={pid}").json()
    assert opts["comment_min"] == 50 and [v["id"] for v in opts["vacancies"]] == [vac]
    short = mc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": "норм"}, headers=mh)
    assert short.status_code == 422
    r = mc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": GOOD}, headers=mh)
    assert r.status_code == 200 and r.json()["recruiter"] == "Проверочный recruiter"
    dup = mc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": GOOD}, headers=mh)
    assert dup.status_code == 409 and "уже передан" in dup.json()["message"]
    inbox = rc.get("/api/today").json()["handoffs"]
    assert [h["person_id"] for h in inbox] == [pid] and inbox[0]["from_user"] == "Проверочный hiring_manager"
    other, _, _ = api("recruiter", login="stranger")
    assert other.get("/api/today").json()["handoffs"] == []
    head, _, _ = api("head_recruiter")
    assert len(head.get("/api/today").json()["handoffs"]) == 1
    hid = inbox[0]["id"]
    assert other.post(f"/api/handoff/{hid}/take", headers=_h(api, "stranger")).status_code == 403
    t = rc.post(f"/api/handoff/{hid}/take", headers=rh)
    cid = t.json()["candidate_id"]
    assert db.val(conn, "SELECT 1 FROM ops.candidate_vacancy WHERE candidate_id = %s AND vacancy_id = %s", (cid, vac))
    mine = mc.get("/api/today").json()["handoffs"]
    assert mine[0]["state"] == "taken" and mine[0]["stage"] == "picked_by_agent" and mine[0]["stage_title"]
    assert rc.post(f"/api/handoff/{hid}/take", headers=rh).status_code == 409


def _h(api, login):
    return api("recruiter", login=login)[1]


def test_s11_reject_reason_masked_for_manager(conn, clock, api):
    o, vac, (mc, mh, _), (rc, rh, _) = setup(conn, clock, api)
    pid = person(conn, o["sales"])
    hid = mc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": GOOD}, headers=mh).json()["id"]
    assert rc.post(f"/api/handoff/{hid}/reject", json={"reason": "нет"}, headers=rh).status_code == 422
    assert rc.post(f"/api/handoff/{hid}/reject", json={"reason": REASON}, headers=rh).json() == {"state": "rejected"}
    h = mc.get("/api/handoffs").json()["items"][0]
    assert h["state"] == "rejected" and "123-45-67" not in h["reject_reason"] and "[телефон]" in h["reject_reason"]
    assert not db.val(conn, "SELECT count(*) FROM decision_log")


def test_already_candidate_only_linked(conn, clock, api):
    o, vac, (mc, mh, _), (rc, rh, rid) = setup(conn, clock, api)
    pid = person(conn, o["sales"])
    cid = rc.post(f"/api/person/{pid}/take", json={}, headers=rh).json()["candidate_id"]
    hid = mc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": GOOD}, headers=mh).json()["id"]
    assert rc.post(f"/api/handoff/{hid}/take", headers=rh).json() == {"candidate_id": cid}


def test_out_of_scope_404_and_hr_cannot_send(conn, clock, api):
    o, vac, (mc, mh, _), (rc, rh, _) = setup(conn, clock, api)
    eng = person(conn, o["eng"], "Инженер")
    assert mc.post("/api/handoff", json={"person_id": eng, "vacancy_id": vac, "comment": GOOD},
                   headers=mh).status_code == 404
    pid = person(conn, o["sales"])
    assert rc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": GOOD},
                   headers=rh).status_code == 403


def test_s10_candidate_reason_visible(conn, clock, api):
    o, vac, (mc, mh, _), (rc, rh, _) = setup(conn, clock, api)
    pid = person(conn, o["sales"])
    hid = mc.post("/api/handoff", json={"person_id": pid, "vacancy_id": vac, "comment": GOOD}, headers=mh).json()["id"]
    cid = rc.post(f"/api/handoff/{hid}/take", headers=rh).json()["candidate_id"]
    db.run(conn, "UPDATE candidate SET status = 'rejected_by_candidate', reject_reason = %s WHERE id = %s",
           ("Выбрал другое предложение, телефон +7 900 123-45-67", cid))
    h = mc.get("/api/handoffs").json()["items"][0]
    assert h["closed"] and "другое предложение" in h["candidate_reason"] and "123-45-67" not in h["candidate_reason"]
