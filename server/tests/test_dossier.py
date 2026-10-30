"""T5 workplace: досье (S6) без значений контактов; просмотр нанимающим руководителем — в audit_log."""
from factories import hh_item, own_unit, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process


def strong(conn, clock, **kw):
    profile(conn)
    it = hh_item(**kw)
    return process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id


def test_s06_dossier_contents(conn, clock, api):
    pid = strong(conn, clock)
    c, _, _ = api()
    d = c.get(f"/api/person/{pid}/dossier").json()
    assert d["person"]["person_id"] == pid and d["person"]["label"]
    m = d["matches"][0]
    assert m["score"] > 0 and m["features"]["keywords"]["max"] > 0, "балл «сколько из скольких»"
    o = d["observations"][0]
    assert o["source"] == "hh" and "Altium Designer" in o["skills"] and o["employers"]
    assert isinstance(o["experience"], list) and o["experience"][0]["company"]
    assert d["versions"] and d["events"] is not None and d["social"] == [] and d["decisions"] == []
    assert d["candidate"] is None
    short = c.get(f"/api/person/{pid}").json()
    assert short["person"]["person_id"] == pid and "matches" in short and "versions" not in short


def test_no_contact_values_in_dossier(conn, clock, api):
    pid = strong(conn, clock, phone="+7 900 000-00-00")
    cid = db.val(conn, """INSERT INTO candidate (person_id, fio, retention_until) VALUES (%s, 'Тестов Т. Т.',
                          current_date + 1000) RETURNING id""", (pid,))
    db.run(conn, "UPDATE person SET candidate_id = %s WHERE id = %s", (cid, pid))
    db.run(conn, "INSERT INTO contact (candidate_id, kind, value, value_hash) VALUES (%s, 'email', 'x@y.ru', 'h')",
           (cid,))
    c, _, _ = api()
    r = c.get(f"/api/person/{pid}/dossier")
    assert "x@y.ru" not in r.text and "900 000" not in r.text
    assert r.json()["candidate"]["contact_opened"] is True


def test_unknown_person_404(api):
    c, _, _ = api()
    assert c.get("/api/person/999999/dossier").status_code == 404


def test_hiring_manager_view_is_audited(conn, clock, api):
    pid = strong(conn, clock)
    c, _, uid = api("hiring_manager")
    assert c.get(f"/api/person/{pid}").status_code == 404   # вне подразделения — как будто нет (manager-cabinet)
    own_unit(conn, uid, "engineer_hw_lead")
    assert c.get(f"/api/person/{pid}").status_code == 200
    row = db.one(conn, "SELECT * FROM audit_log WHERE user_id = %s", (uid,))
    assert row["action"] == "view" and row["object_type"] == "person" and row["object_id"] == pid
    c2, _, uid2 = api("head_recruiter")
    c2.get(f"/api/person/{pid}")
    assert db.val(conn, "SELECT count(*) FROM audit_log WHERE user_id = %s", (uid2,)) == 0
