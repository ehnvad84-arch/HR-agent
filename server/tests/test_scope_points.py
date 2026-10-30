"""T3 manager-cabinet: граница видимости во всех точках чтения (S5, S7, S8, M1, M2)."""
from factories import org, user

from hr import auth, db
from hr.main import app


def setup(conn, clock, api):
    o = org(conn, clock.now())
    p_eng = db.val(conn, "INSERT INTO person (label) VALUES ('Инженер, 8 лет') RETURNING id")
    p_sales = db.val(conn, "INSERT INTO person (label) VALUES ('Продажи, 5 лет') RETURNING id")
    for pid, prof in ((p_eng, o["eng"]), (p_sales, o["sales"])):
        db.run(conn, "INSERT INTO match (person_id, role_profile_id, profile_version, score) VALUES (%s, %s, %s, 80)",
               (pid, prof["id"], prof["version"]))
        db.run(conn, """INSERT INTO observation (source_id, external_id, person_id, url, title, raw, content_hash)
                        SELECT id, %s, %s, 'https://hh.ru/resume/x', 'Резюме', '{}', md5(%s)
                        FROM source WHERE code = 'hh'""",
               (f"r{pid}", pid, f"r{pid}"))
    vac = db.val(conn, """INSERT INTO vacancy (role_profile_id, title, staff_position_id) VALUES (%s, 'МПП', %s)
                          RETURNING id""", (o["sales"]["id"], o["pos"]["p-mpp1"]))
    rnd, rh, rid = api("hiring_manager", login="rnd_head")
    user(conn, "rnd_head", "hiring_manager", o["units"]["u-rnd"])
    return o, p_eng, p_sales, vac, (rnd, rh, rid)


def test_manager_sees_only_own(conn, clock, api):
    o, p_eng, p_sales, vac, (c, h, _) = setup(conn, clock, api)
    assert c.get(f"/api/person/{p_eng}").status_code == 200
    for url in (f"/api/person/{p_sales}", f"/api/person/{p_sales}/dossier", f"/api/person/{p_sales}/photo",
                f"/api/vacancy/{vac}/matching", f"/api/vacancy/{vac}/candidates", f"/api/vacancy/{vac}/funnel",
                f"/api/profile/{o['sales']['id']}", f"/api/profile/{o['sales']['id']}/preview",
                "/api/base?fit=sales_project"):
        assert c.get(url).status_code == 404, url
    r = c.post(f"/api/profile/{o['sales']['id']}", json={"keywords": ["CRM"]}, headers=h)
    assert r.status_code == 403 and "не в вашем подразделении" in r.json()["message"]
    assert c.post(f"/api/profile/{o['eng']['id']}", json={"keywords": ["Altium"]}, headers=h).status_code == 200
    base = c.get("/api/base").json()
    assert [x["person_id"] for x in base["items"]] == [p_eng]
    assert [p["code"] for p in c.get("/api/profiles").json()["items"]] == ["engineer_hw_lead"]
    assert c.get("/api/vacancies").json()["open"] == []
    assert c.get("/api/schema").status_code == 403


def test_dossier_without_links(conn, clock, api):
    o, p_eng, _, _, (c, _, uid) = setup(conn, clock, api)
    d = c.get(f"/api/person/{p_eng}/dossier").json()
    assert "social" not in d and "decisions" not in d and all("url" not in x for x in d["observations"])
    assert [m["role_code"] for m in d["matches"]] == ["engineer_hw_lead"]
    assert db.val(conn, "SELECT count(*) FROM audit_log WHERE user_id = %s AND action = 'view'", (uid,)) == 1


def test_hr_sees_all_and_positions_tree(conn, clock, api):
    o, p_eng, p_sales, vac, (c, _, _) = setup(conn, clock, api)
    hr, _, _ = api("recruiter")
    assert hr.get(f"/api/person/{p_sales}").status_code == 200
    assert hr.get(f"/api/vacancy/{vac}/funnel").status_code == 200
    mine = c.get("/api/my/positions").json()
    assert [u["title"] for u in mine["units"]] == ["Отдел инженерных разработок"]
    assert len(hr.get("/api/my/positions").json()["units"]) == len(o["units"])


def test_every_base_read_route_is_scoped():
    assert auth.routes_unscoped(app) == []


def test_m2_free_text_without_contacts_and_s18_unit_change(conn, clock, api):
    o, p_eng, _, _, (c, _, uid) = setup(conn, clock, api)
    db.run(conn, """UPDATE observation SET raw = %s WHERE person_id = %s""",
           (db.j({"skills": "Пишите: ivan@mail.ru, +7 900 111-22-33",
                  "experience": [{"company": "Завод", "position": "Инженер",
                                  "description": "звонить 8 900 111 22 33"}]}), p_eng))
    d = c.get(f"/api/person/{p_eng}/dossier").json()
    text = str(d["observations"])
    assert "ivan@mail.ru" not in text and "111-22-33" not in text and "111 22 33" not in text
    assert "versions" not in d
    user(conn, "rnd_head", "hiring_manager", o["units"]["u-s1"])   # S18: перевели в отдел продаж №1
    assert c.get(f"/api/person/{p_eng}").status_code == 404
