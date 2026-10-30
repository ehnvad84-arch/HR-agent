"""T5 manager-cabinet: выгрузка сотрудников 1С (S1, S18, M5, M6)."""
import json

from factories import org

from hr import auth, db, staff_1c

EMPS = [
    {"id": "e1", "login": "Ivanova", "full_name": "Иванова Анна", "department_id": "u-hr",
     "position": "Менеджер по персоналу", "active": True},
    {"id": "e2", "login": "petrov", "full_name": "Петров Пётр", "department_id": "u-hr",
     "position": "Руководитель отдела персонала", "active": True},
    {"id": "e3", "login": "sidorov", "full_name": "Сидоров Семён", "department_id": "u-sales",
     "position": "Начальник департамента продаж", "active": True},
    {"id": "e4", "login": "kozlov", "full_name": "Козлов Кирилл", "department_id": "u-s1",
     "position": "Менеджер проектных продаж", "active": True},
    {"id": "e5", "login": "glav", "full_name": "Главный Директор", "department_id": "u-root",
     "position": "Директор", "active": True},
]


def raw(emps, period="2026-10-30"):
    return json.dumps({"kind": "employees", "period": period, "employees": emps}, ensure_ascii=False).encode()


def roles(conn):
    return {r["login"]: r["role"] for r in db.rows(conn, "SELECT login, role::text AS role FROM app_user")}


def test_s1_roles_by_rule(conn, clock):
    org(conn, clock.now())
    r = staff_1c.apply(conn, raw(EMPS), "employees_1.json", None, clock.now())
    assert r == {"state": "applied", "file_name": "employees_1.json", "new": 5, "changed": 0, "fired": 0}
    got = roles(conn)
    assert got["ivanova"] == "recruiter" and got["petrov"] == "head_recruiter" and got["sidorov"] == "hiring_manager"
    assert got["kozlov"] == "no_access" and got["glav"] == "director"
    assert staff_1c.apply(conn, raw(EMPS), "employees_1.json", None, clock.now())["state"] == "same"


def test_s1_override_kept_and_fired_revoked(conn, clock, monkeypatch):
    from hr import config
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    org(conn, clock.now())
    staff_1c.apply(conn, raw(EMPS), "e.json", None, clock.now())
    kid = db.val(conn, "SELECT id FROM app_user WHERE login = 'kozlov'")
    db.run(conn, "INSERT INTO ops.user_override (user_id, role) VALUES (%s, 'hiring_manager')", (kid,))
    sid = db.val(conn, "SELECT id FROM app_user WHERE login = 'sidorov'")
    cookie, _ = auth.create_session(conn, sid, clock.now())
    emps = [dict(e) for e in EMPS]
    emps[2]["active"] = False
    emps[3]["position"] = "Старший менеджер проектных продаж"
    r = staff_1c.apply(conn, raw(emps, "2026-10-31"), "e2.json", None, clock.now())
    assert r["fired"] == 1 and roles(conn)["kozlov"] == "hiring_manager"
    assert auth.load_session(conn, cookie, clock.now()) is None
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'access'") == 2


def test_m6_too_many_missing_rejected(conn, clock):
    org(conn, clock.now())
    staff_1c.apply(conn, raw(EMPS), "e.json", None, clock.now())
    try:
        staff_1c.apply(conn, raw(EMPS[:2], "2026-11-01"), "e3.json", None, clock.now())
    except staff_1c.Invalid as e:
        assert "пропало 3 из 5" in str(e)
    else:
        raise AssertionError("неполная выгрузка применилась")


def test_manual_user_gets_external_id(conn, clock, api):
    org(conn, clock.now())
    c, h, _ = api("head_recruiter")
    c.post("/api/access/user", json={"login": "sidorov", "full_name": "Сидоров С.", "role": "director"}, headers=h)
    staff_1c.apply(conn, raw(EMPS), "e.json", None, clock.now())
    row = db.one(conn, "SELECT external_id, role::text AS role FROM app_user WHERE login = 'sidorov'")
    assert row == {"external_id": "e3", "role": "director"}


def test_api_upload_and_bad_file(conn, clock, api):
    org(conn, clock.now())
    c, h, _ = api("head_recruiter")
    r = c.post("/api/access/upload", headers=h, files={"file": ("employees.json", b"{}", "application/json")})
    assert r.status_code == 422 and "не применена" in r.json()["message"]
    r = c.post("/api/access/upload", headers=h, files={"file": ("employees.json", raw(EMPS), "application/json")})
    assert r.status_code == 200 and r.json()["new"] == 5
    assert c.get("/api/access/users").json()["sync"]["changes"] == 5
