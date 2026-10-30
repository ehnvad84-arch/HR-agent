"""T4 manager-cabinet: «Доступ», одноразовые коды, роль no_access (S2, S3, S4, M3, M4)."""
from conftest import ORIGIN
from factories import user

from hr import db


def test_s2_manual_user_and_role(conn, api):
    c, h, _ = api("head_recruiter")
    r = c.post("/api/access/user", json={"login": "Ivanov", "full_name": "Иванов И. И.", "role": "hiring_manager",
                                         "position": "Начальник отдела"}, headers=h)
    uid = r.json()["id"]
    row = next(u for u in c.get("/api/access/users").json()["items"] if u["id"] == uid)
    assert row["login"] == "ivanov" and row["manual"] and row["role"] == "hiring_manager" and not row["has_password"]
    assert c.post("/api/access/user", json={"login": "ivanov", "full_name": "x", "role": "recruiter"},
                  headers=h).status_code == 409
    assert c.post(f"/api/access/user/{uid}", json={"role": "agent"}, headers=h).status_code == 422
    assert c.post(f"/api/access/user/{uid}", json={"role": "director"}, headers=h).status_code == 200
    assert db.val(conn, "SELECT role::text FROM app_user WHERE id = %s", (uid,)) == "director"
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'access'") == 2
    r2, rh, _ = api("recruiter")   # специалист видит только руководителей — им он выдаёт коды
    assert {u["role"] for u in r2.get("/api/access/users").json()["items"]} == {"director"}
    m, _, _ = api("hiring_manager")
    assert m.get("/api/access/users").status_code == 403


def test_s3_code_login_flow(conn, api, clock):
    c, h, _ = api("recruiter")
    mid = user(conn, "boss", "hiring_manager")
    code = c.post(f"/api/access/user/{mid}/code", headers=h).json()["code"]
    assert len(code) == 10
    anon, _, _ = api("recruiter", login="other")
    anon.cookies.clear()
    bad = anon.post("/api/login/code", json={"login": "boss", "code": "WRONG12345", "password": "новыйпароль"},
                    headers=ORIGIN)
    assert bad.status_code == 401 and bad.json()["error"] == "bad_code"
    short = anon.post("/api/login/code", json={"login": "boss", "code": code, "password": "123"}, headers=ORIGIN)
    assert short.status_code == 422
    ok = anon.post("/api/login/code", json={"login": "BOSS", "code": code, "password": "новыйпароль"}, headers=ORIGIN)
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "hiring_manager"
    again = anon.post("/api/login/code", json={"login": "boss", "code": code, "password": "другойпароль"},
                      headers=ORIGIN)
    assert again.status_code == 401 and again.json() == bad.json()   # погашенный — тот же ответ
    assert anon.post("/api/login", json={"login": "boss", "password": "новыйпароль"}, headers=ORIGIN).status_code == 200


def test_m3_expired_and_lock(conn, api, clock):
    c, h, _ = api("head_recruiter")
    mid = user(conn, "boss2", "hiring_manager")
    code = c.post(f"/api/access/user/{mid}/code", headers=h).json()["code"]
    anon, _, _ = api("recruiter", login="x2")
    anon.cookies.clear()
    for _ in range(5):
        anon.post("/api/login/code", json={"login": "boss2", "code": "AAAAAAAAAA", "password": "новыйпароль"},
                  headers=ORIGIN)
    r = anon.post("/api/login/code", json={"login": "boss2", "code": code, "password": "новыйпароль"}, headers=ORIGIN)
    assert r.status_code == 429
    code2 = c.post(f"/api/access/user/{mid}/code", headers=h).json()["code"]   # новый гасит прежний
    assert db.val(conn, """SELECT count(*) FROM ops.invite_code WHERE user_id = %s
                           AND used_at IS NULL AND revoked_at IS NULL""", (mid,)) == 1
    clock.advance(days=3.1)
    r = anon.post("/api/login/code", json={"login": "boss2", "code": code2, "password": "новыйпароль"},
                  headers=ORIGIN)
    assert r.status_code == 401


def test_m4_recruiter_cannot_hijack(conn, api):
    c, h, _ = api("recruiter")
    head = user(conn, "hr_head", "head_recruiter")
    assert c.post(f"/api/access/user/{head}/code", headers=h).status_code == 403
    nobody = user(conn, "clerk", "no_access")
    assert c.post(f"/api/access/user/{nobody}/code", headers=h).status_code == 409
    hc, hh, _ = api("head_recruiter")
    assert hc.post(f"/api/access/user/{head}/code", headers=hh).status_code == 200
    assert db.val(conn, "SELECT count(*) FROM audit_log WHERE action = 'invite'") == 1


def test_s4_no_access(conn, api):
    c, _, _ = api("no_access")
    r = c.get("/api/me")
    assert r.status_code == 403 and r.json()["error"] == "no_access"
    assert c.get("/api/base").status_code == 403
