"""T8 manager-cabinet: личные поправки и «мой балл» (S14, S15, S16)."""
from factories import hh_item, profile, source

from hr import db, learning, tweaks
from hr.normalize import normalize
from hr.pipeline import process

BODY = "В выдачу попадают люди из розницы, для нас это не профильный опыт, убери слово «трассировка» из счёта."


def person(conn, clock, rid="a"):
    exp = [{"company": "Завод", "position": "Ведущий инженер-электронщик", "start": "2015-01-01", "end": None}]
    it = hh_item(id=rid, title="Ведущий инженер-электронщик", area={"id": "53", "name": "Краснодар"}, experience=exp,
                 skill_set=["Altium Designer", "схемотехника", "трассировка", "ЭМС"])
    return process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id


def test_s15_my_score_only_for_author(conn, clock, api):
    prof = profile(conn)
    pid = person(conn, clock)
    general = db.val(conn, "SELECT score FROM match WHERE person_id = %s", (pid,))
    a, ah, aid = api("recruiter", login="a")
    b, _, _ = api("recruiter", login="b")
    r = a.post("/api/tweaks", json={"kind": "stop", "value": {"text": "схемотехника"}}, headers=ah)
    assert r.status_code == 200
    assert a.post("/api/tweaks", json={"kind": "weight", "value": {"feature": "exp_years", "weight": 999}},
                  headers=ah).status_code == 422
    tweaks.rescore_user(conn, aid)
    mine = db.val(conn, "SELECT score FROM ops.user_match WHERE user_id = %s AND person_id = %s", (aid, pid))
    assert mine is not None and mine != general
    assert db.val(conn, "SELECT score FROM match WHERE person_id = %s", (pid,)) == general
    ra = a.get("/api/base?sort=my").json()["items"][0]
    rb = b.get("/api/base").json()["items"][0]
    assert ra["my_score"] == mine and rb["my_score"] is None and ra["score"] == rb["score"] == general
    assert prof["code"] == ra["role_code"]


def test_s14_own_chat_and_agent_suggestion(conn, clock, api):
    profile(conn)
    a, ah, aid = api("recruiter", login="a")
    b, _, _ = api("hiring_manager", login="b")
    assert a.post("/api/feedback", json={"body": BODY, "tag": "remark"}, headers=ah).status_code == 200
    assert len(a.get("/api/feedback").json()["items"]) == 1 and b.get("/api/feedback").json()["items"] == []
    t = a.get("/api/tweaks").json()["items"]
    assert [(x["kind"], x["value"]["text"], x["state"], x["source"]) for x in t] == \
        [("minus", "трассировка", "proposed", "agent")]
    assert tweaks.criteria(conn, aid)["keywords_minus"] == []   # предложение не действует до кнопки
    assert a.post(f"/api/tweak/{t[0]['id']}/confirm", headers=ah).json() == {"state": "active"}
    assert tweaks.criteria(conn, aid)["keywords_minus"] == ["трассировка"]


def test_s16_shared_goes_to_digest_then_pending_version(conn, conn_factory, clock, api):
    prof = profile(conn)
    uids = [api("recruiter", login=f"u{i}")[2] for i in range(3)]
    for uid in uids:
        db.run(conn, """INSERT INTO ops.user_tweak (user_id, role_code, kind, value) VALUES (%s, %s, 'plus', %s)""",
               (uid, prof["code"], db.j({"text": "PCIe"})))
    learning.build_digest(conn_factory, clock.now())
    p = db.one(conn, "SELECT id, target FROM digest_proposal WHERE target ? 'tweak'")
    assert p["target"]["profile_code"] == prof["code"]
    db.run(conn, "UPDATE digest_proposal SET decision = 'accepted', decided_by = %s, decided_at = now() WHERE id = %s",
           (uids[0], p["id"]))
    learning.apply_digest(conn_factory, now=clock.now())
    new = db.one(conn, """SELECT keywords, approved_at FROM role_profile WHERE code = %s ORDER BY id DESC LIMIT 1""",
                 (prof["code"],))
    assert "PCIe" in new["keywords"] and new["approved_at"] is None
    assert not tweaks.shared(conn)   # второй раз в сводку не идёт



def test_s16_all_positions_tweak_reaches_profiles(conn, conn_factory, clock, api):
    prof = profile(conn)
    for i in range(3):
        c, h, uid = api("recruiter", login=f"r{i}")
        assert c.post("/api/tweaks", json={"kind": "plus", "value": {"text": "FPGA"}}, headers=h).status_code == 200
    learning.build_digest(conn_factory, clock.now())
    p = db.one(conn, "SELECT id, target FROM digest_proposal WHERE target ? 'tweak'")
    assert p["target"]["profile_codes"] == [prof["code"]]
    db.run(conn, "UPDATE digest_proposal SET decision = 'accepted', decided_by = %s, decided_at = now() WHERE id = %s",
           (uid, p["id"]))
    learning.apply_digest(conn_factory, now=clock.now())
    new = db.one(conn, "SELECT keywords, approved_at FROM role_profile WHERE code = %s ORDER BY id DESC LIMIT 1",
                 (prof["code"],))
    assert "FPGA" in new["keywords"] and new["approved_at"] is None
