"""T9 funnel-report: API библиотеки этапов и наборов вакансии (S1–S5), права и граница видимости (AD-7)."""
import pytest
from factories import own_unit, profile, user

from hr import auth, db
from hr.main import app


@pytest.fixture
def prof(conn):
    return profile(conn)


@pytest.fixture
def boss(conn):
    return user(conn, "boss", "hiring_manager")


def new_vacancy(c, h, prof, boss):
    r = c.post("/api/vacancy", json={"role_profile_id": prof["id"], "title": prof["title"], "hiring_manager_id": boss},
               headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def sid(conn, title):
    return db.val(conn, "SELECT id FROM ops.stage WHERE title = %s", (title,))


def items(sets):
    return [{"slot": i["slot"], "stage_id": i["stage_id"], "label": i["label"]} for i in sets["current"]["items"]]


def four_meetings(conn, sets):
    """S3: вместо одного «Собеседования» — четыре с разными руководителями."""
    out = []
    for i in items(sets):
        out.append(i)
        if i["slot"] == 5:
            out += [{"stage_id": sid(conn, "Собеседование с руководителем"), "label": f"Руководитель {n}"}
                    for n in (2, 3, 4)]
    return out


def listed(c, vid):
    return next(v for v in c.get("/api/vacancies").json()["open"] if v["id"] == vid)


# ------------------------------------------------------------------ S1: библиотека

def test_s1_library_read_and_head_adds_stage(conn, api):
    r, _, _ = api("recruiter")
    lib = r.get("/api/stages").json()
    assert [x["code"] for x in lib["rows"]] == ["selected", "contact", "screening", "interview", "meeting",
                                                "security", "offer", "first_day"]
    assert {s["title"] for s in lib["stages"]} >= {"Тестовое задание", "Собеседование с руководителем"}
    assert lib["reasons"][-1]["code"] == "other"

    head, hh, _ = api("head_recruiter")
    made = head.post("/api/stages", json={"title": "Собеседование с директором", "report_row": "meeting"}, headers=hh)
    assert made.status_code == 200 and made.json()["id"] == sid(conn, "Собеседование с директором")
    dup = head.post("/api/stages", json={"title": "собеседование С ДИРЕКТОРОМ", "report_row": "meeting"}, headers=hh)
    assert dup.status_code == 409 and dup.json()["error"] == "duplicate"
    bad = head.post("/api/stages", json={"title": "Звонок", "report_row": "nope"}, headers=hh)
    assert bad.status_code == 422 and bad.json()["error"] == "validation"


def test_s1_retire_stage(conn, api):
    head, hh, _ = api("head_recruiter")
    extra = sid(conn, "Тестовое задание")
    r = head.post(f"/api/stages/{extra}/retire", json={}, headers=hh)
    assert r.status_code == 200 and r.json() == {"id": extra}
    assert db.val(conn, "SELECT is_active FROM ops.stage WHERE id = %s", (extra,)) is False
    again = head.post(f"/api/stages/{extra}/retire", json={}, headers=hh)
    assert again.status_code == 409 and again.json()["error"] == "retired"
    std = head.post(f"/api/stages/{sid(conn, 'Оффер')}/retire", json={}, headers=hh)
    assert std.status_code == 409 and std.json()["error"] == "standard"
    assert head.post("/api/stages/999999/retire", json={}, headers=hh).status_code == 404


def test_s1_library_write_needs_stage_approve(conn, api):
    for role in ("recruiter", "hiring_manager", "agent"):
        c, h, _ = api(role)
        assert c.post("/api/stages", json={"title": "Звонок", "report_row": "contact"}, headers=h).status_code == 403
        assert c.post(f"/api/stages/{sid(conn, 'Тестовое задание')}/retire", json={}, headers=h).status_code == 403


# ------------------------------------------------------------------ S2–S4: набор вакансии

def test_s2_new_vacancy_has_standard_set(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert [i["slot"] for i in sets["current"]["items"]] == list(range(1, 9))
    assert sets["current"]["people"] == {} and sets["proposed"] is None and sets["declined"] is None
    v = listed(r, vid)
    assert v["stages_pending"] is False and v["stages_declined"] is False


def test_s3_propose_pending_and_withdraw(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    body = {"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json()), "note": "4 руководителя"}
    p = r.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=h)
    assert p.status_code == 200 and p.json()["version"] == 2
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert len(sets["current"]["items"]) == 8 and len(sets["proposed"]["items"]) == 11
    assert sets["proposed"]["note"] == "4 руководителя"
    assert listed(r, vid)["stages_pending"] is True
    again = r.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=h)
    assert again.status_code == 409 and again.json()["error"] == "pending_exists"
    w = r.post(f"/api/stage-sets/{p.json()['id']}/withdraw", json={}, headers=h)
    assert w.status_code == 200 and w.json() == {"state": "withdrawn"}
    assert listed(r, vid)["stages_pending"] is False
    twice = r.post(f"/api/stage-sets/{p.json()['id']}/withdraw", json={}, headers=h)
    assert twice.status_code == 409 and twice.json()["error"] == "not_proposed"
    assert r.post("/api/stage-sets/999999/withdraw", json={}, headers=h).status_code == 404


def test_s4_head_approves(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    p = r.post(f"/api/vacancy/{vid}/stage-sets",
               json={"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json())}, headers=h).json()
    assert r.post(f"/api/stage-sets/{p['id']}/approve", json={}, headers=h).status_code == 403
    head, hh, hid = api("head_recruiter")
    a = head.post(f"/api/stage-sets/{p['id']}/approve", json={}, headers=hh)
    assert a.status_code == 200 and a.json() == {"state": "approved"}
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert sets["current"]["version"] == 2 and len(sets["current"]["items"]) == 11 and sets["proposed"] is None
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'stages' AND applied_by = %s", (hid,)) == 1


def test_s4_head_declines_with_comment_recruiter_sees_it(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    p = r.post(f"/api/vacancy/{vid}/stage-sets",
               json={"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json())}, headers=h).json()
    head, hh, _ = api("head_recruiter")
    empty = head.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "  "}, headers=hh)
    assert empty.status_code == 422
    assert r.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "нет"}, headers=h).status_code == 403
    d = head.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "Хватит двух собеседований"}, headers=hh)
    assert d.status_code == 200 and d.json() == {"state": "declined"}
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert sets["declined"]["decision_comment"] == "Хватит двух собеседований"
    assert sets["declined"]["decided_by_name"] == "Проверочный head_recruiter"
    assert len(sets["current"]["items"]) == 8
    v = listed(r, vid)
    assert v["stages_declined"] is True and v["stages_pending"] is False
    late = head.post(f"/api/stage-sets/{p['id']}/approve", json={}, headers=hh)
    assert late.status_code == 409 and late.json()["error"] == "not_proposed"


# ------------------------------------------------------------------ S5: этап с людьми не убрать

def test_s5_busy_stage_and_fixed_rows(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    st = db.one(conn, "SELECT id FROM ops.stage WHERE report_row = 'security' AND is_standard")
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    db.run(conn, """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row)
                    VALUES (%s, %s, 6, %s, 'security')""", (cid, vid, st["id"]))
    cur = items(r.get(f"/api/vacancy/{vid}/stage-sets").json())
    busy = r.post(f"/api/vacancy/{vid}/stage-sets", json={"items": [i for i in cur if i["slot"] != 6]}, headers=h)
    assert busy.status_code == 409
    assert busy.json()["error"] == "stage_busy" and busy.json()["slot"] == 6 and busy.json()["count"] == 1
    assert "сначала переведите" in busy.json()["message"]
    fixed = r.post(f"/api/vacancy/{vid}/stage-sets", json={"items": [i for i in cur if i["slot"] != 8]}, headers=h)
    assert fixed.status_code == 422 and fixed.json()["error"] == "validation"
    assert r.post("/api/vacancy/999999/stage-sets", json={"items": cur}, headers=h).status_code == 404


# ------------------------------------------------------------------ права и граница видимости

def test_sets_scope_manager_sees_own_vacancy_only(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    other = new_vacancy(r, h, prof, boss)
    m, mh, mid = api("hiring_manager", login="rnd_head")
    own_unit(conn, mid, prof["code"])
    mine = new_vacancy(r, h, prof, mid)
    assert m.get(f"/api/vacancy/{mine}/stage-sets").status_code == 200
    assert m.get(f"/api/vacancy/{other}/stage-sets").status_code == 404
    assert m.get("/api/vacancy/999999/stage-sets").status_code == 404
    assert m.get("/api/stages").status_code == 200
    body = {"items": four_meetings(conn, r.get(f"/api/vacancy/{mine}/stage-sets").json())}
    assert m.post(f"/api/vacancy/{mine}/stage-sets", json=body, headers=mh).status_code == 403


def test_agent_writes_no_stages(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    body = {"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json())}
    p = r.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=h).json()
    a, ah, _ = api("agent")
    assert a.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=ah).status_code == 403
    for act in ("withdraw", "approve"):
        assert a.post(f"/api/stage-sets/{p['id']}/{act}", json={}, headers=ah).status_code == 403
    assert a.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "x"}, headers=ah).status_code == 403


def test_stage_routes_scoped_and_with_permission():
    assert auth.routes_unscoped(app) == []
    assert not [r for r in auth.routes_without_permission(app) if "stage" in r]
    paths = {p for p, _, _ in auth.api_routes(app)}
    assert {"/api/stages", "/api/stages/{stage_id}/retire", "/api/vacancy/{vacancy_id}/stage-sets",
            "/api/stage-sets/{set_id}/withdraw", "/api/stage-sets/{set_id}/approve",
            "/api/stage-sets/{set_id}/decline"} <= paths
